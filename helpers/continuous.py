"""Shared camera recording with independent vehicle controllers and pedestrians."""
import os
import csv
import time
import queue
import carla
import numpy as np
from tqdm import tqdm
from helpers.encoding import FFmpegWriter
from helpers.cameras import camera_transforms as make_camera_transforms

class CARLASimulator:

    def __init__(self, folder_name, fps=30, camera_fov=120, camera_resolution=(1280, 720), output_root='output', host='localhost', port=2000, disable_traffic_lights=True, disable_weather=True, *, maintain_velocity=False, record_fov=False, pedestrian_count=0, pedestrian_seed=0, exit_delta_frames=0, group_gap_frames=0, stop_when_empty=False, camera_poses=None):
        self.fps = fps
        self.output_root = output_root
        self.camera_resolution = camera_resolution
        self.camera_transforms = make_camera_transforms(camera_poses)
        self.maintain_velocity = maintain_velocity
        self.record_fov = record_fov
        self.exit_delta_frames = exit_delta_frames
        self.group_gap_frames = group_gap_frames
        self.stop_when_empty = stop_when_empty
        self.last_despawn_frame = None
        self.group_offsets = {}
        self.pedestrians = None
        self.vehicle_configs = []
        self.active_vehicles = {}
        self.despawn_queue = {}
        self.csv_path = os.path.join(self.output_root, folder_name, 'all_vehicle_trajectories.csv')
        self.vehicle_trajectories = [['frame', 'timestamp', 'vehicle_id', 'vehicle_type', 'x', 'y', 'z', 'vx', 'vy', 'vz']]
        if record_fov:
            self.vehicle_trajectories[0] += ['vehicle_index', 'yaw', 'pitch', 'roll', 'fov_states']
            from helpers.geometry import build_camera_frustum
            self.cam_models = [build_camera_frustum(
                (tf.location.x, tf.location.y, tf.location.z),
                tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll,
                camera_fov, *camera_resolution, near_clip=0.1, far_clip=1000.0)
                for tf in self.camera_transforms]
        self.client = carla.Client(host, port)
        self.client.set_timeout(10.0)
        self.world = self.client.get_world()
        self.settings = self.world.get_settings()
        self.settings.synchronous_mode = True
        self.settings.fixed_delta_seconds = 1 / self.fps
        self.world.apply_settings(self.settings)
        self.blueprint_library = self.world.get_blueprint_library()
        self.static_cameras, self.static_cam_queues, self.static_cam_buffers = self._setup_cameras(camera_fov)
        self.next_spawn_idx = 0
        self.folder_name = folder_name
        if disable_traffic_lights:
            traffic_lights = self.world.get_actors().filter('traffic.traffic_light*')
            for light in traffic_lights:
                light.set_state(carla.TrafficLightState.Green)
                light.freeze(True)
        if disable_weather:
            weather = carla.WeatherParameters(cloudiness=0.0, precipitation=0.0, precipitation_deposits=0.0, wind_intensity=0.0, sun_azimuth_angle=0.0, sun_altitude_angle=90.0)
            self.world.set_weather(weather)
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        self.segmented_writers = []
        for i in range(len(self.static_cameras)):
            writer = FFmpegWriter(output_prefix=os.path.join(self.output_root, self.folder_name, f'video_camera{i + 1}'), width=self.camera_resolution[0], height=self.camera_resolution[1], fps=self.fps)
            self.segmented_writers.append(writer)
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(self.vehicle_trajectories[0])
        if pedestrian_count:
            from helpers.pedestrians import Pedestrians
            try:
                self.pedestrians = Pedestrians(self.client, self.world, pedestrian_count, pedestrian_seed)
            except Exception:
                self._cleanup()
                raise

    def _setup_cameras(self, fov):
        camera_transforms = self.camera_transforms
        bp = self.blueprint_library.find('sensor.camera.rgb')
        bp.set_attribute('image_size_x', str(self.camera_resolution[0]))
        bp.set_attribute('image_size_y', str(self.camera_resolution[1]))
        bp.set_attribute('fov', str(fov))
        cameras, queues, buffers = ([], [], [])

        def enqueue(q):
            return lambda image: q.put(image)
        for tf in camera_transforms:
            cam = self.world.spawn_actor(bp, tf)
            q = queue.Queue()
            cam.listen(enqueue(q))
            cameras.append(cam)
            queues.append(q)
            buffers.append([])
        return (cameras, queues, buffers)

    def schedule_vehicle(self, vehicle_type, velocity, spawn_pos, spawn_yaw, spawn_time, duration, vehicle_color_name=None, vehicle_color=None, *, spawn_frame=None, despawn_frame=None, vehicle_index=None, group_index=None, driver_factory=None, timeout_frames=None, group_gap_frames=None):
        # Exact frame bounds avoid rounding a planned event into the wrong tick.
        spawn_frame = int(spawn_time * self.fps) if spawn_frame is None else spawn_frame
        despawn_frame = int((spawn_time + duration) * self.fps) if despawn_frame is None else despawn_frame
        self.vehicle_configs.append({'vehicle_type': vehicle_type, 'velocity': carla.Vector3D(x=velocity[0], y=velocity[1], z=0.0), 'spawn_pos': carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3), 'spawn_yaw': spawn_yaw, 'spawn_frame': spawn_frame, 'despawn_frame': despawn_frame, 'vehicle_color_name': vehicle_color_name, 'vehicle_color': vehicle_color, 'vehicle_index': vehicle_index, 'group_index': group_index, 'driver_factory': driver_factory, 'timeout_frames': timeout_frames, 'group_gap_frames': group_gap_frames})

    def _update_vehicles(self, frame):
        for vid, cfg in list(self.active_vehicles.items()):
            driver = cfg.get('driver')
            ready = driver.arrived() if driver is not None else frame >= cfg['effective_despawn_frame']
            if driver is not None and not ready and frame - cfg['actual_spawn_frame'] >= cfg['timeout_frames']:
                raise RuntimeError(f"Vehicle {cfg['vehicle_index']} did not reach its target within MAX_DURATION")
            if ready and (self.last_despawn_frame is None or frame - self.last_despawn_frame >= self.exit_delta_frames):
                cfg['actor'].destroy()
                cfg['actual_despawn_frame'] = frame
                self.last_despawn_frame = frame
                print(f'🔴 Despawned vehicle {vid} at frame {frame}')
                del self.active_vehicles[vid]
                self.despawn_queue.pop(vid, None)
        while self.next_spawn_idx < len(self.vehicle_configs):
            cfg = self.vehicle_configs[self.next_spawn_idx]
            group = cfg['group_index']
            if group not in self.group_offsets:
                # Continuous groups cannot overtake a slow preceding group.
                if self.active_vehicles:
                    break
                gap = cfg['group_gap_frames'] if cfg['group_gap_frames'] is not None else self.group_gap_frames
                self.group_offsets[group] = 0 if self.last_despawn_frame is None else self.last_despawn_frame + gap - cfg['spawn_frame']
            shift = self.group_offsets[group]
            if frame < cfg['spawn_frame'] + shift:
                break
            bp = self.blueprint_library.filter(cfg['vehicle_type'])[0]
            if cfg.get('vehicle_color') and bp.has_attribute('color'):
                bp.set_attribute('color', cfg['vehicle_color'])
            transform = carla.Transform(cfg['spawn_pos'], carla.Rotation(yaw=cfg['spawn_yaw']))
            actor = self.world.spawn_actor(bp, transform)
            cfg['actor'], cfg['id'] = actor, actor.id
            cfg['actual_spawn_frame'] = frame
            cfg['effective_despawn_frame'] = cfg['despawn_frame'] + shift
            self.active_vehicles[actor.id] = cfg
            self.despawn_queue[actor.id] = cfg['effective_despawn_frame']
            cfg['driver'] = cfg['driver_factory'](actor) if cfg['driver_factory'] else None
            if cfg['driver'] is None:
                actor.set_target_velocity(cfg['velocity'])
            print(f'🟢 Spawned vehicle {actor.id} at frame {frame}')
            self.next_spawn_idx += 1

    def run_simulation(self, total_duration_sec):
        total_frames = int(total_duration_sec * self.fps)
        self.vehicle_configs.sort(key=lambda cfg: cfg['spawn_frame'])
        try:
            limit = 'up to ' if self.stop_when_empty else ''
            print(f'\n🚦 Starting simulation for {limit}{total_duration_sec} seconds ({total_frames} frames)...')
            start_wall_time = time.time()
            for frame in tqdm(range(total_frames), desc='Simulating', unit='frame'):
                if self.record_fov:
                    # Apply lifecycle/control before the image-producing tick so
                    # FOV labels and recorded camera frames describe the same scene.
                    self._update_vehicles(frame)
                    if self.maintain_velocity:
                        for cfg in self.active_vehicles.values():
                            if cfg.get('driver') is None:
                                cfg['actor'].set_target_velocity(cfg['velocity'])
                    for cfg in self.active_vehicles.values():
                        if cfg.get('driver') is not None:
                            cfg['driver'].step()
                world_frame = self.world.tick()
                if not self.record_fov:
                    self._update_vehicles(frame)
                with open(self.csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    for vid, cfg in self.active_vehicles.items():
                        actor = cfg['actor']
                        if self.maintain_velocity and not self.record_fov and cfg.get('driver') is None:
                            actor.set_target_velocity(cfg['velocity'])
                        transform = actor.get_transform()
                        loc = transform.location
                        vel = actor.get_velocity()
                        row = [frame, frame / self.fps, vid, cfg['vehicle_type'], loc.x, loc.y, loc.z, vel.x, vel.y, vel.z]
                        if self.record_fov:
                            from helpers.visibility import actor_fov_states
                            states = actor_fov_states(actor, self.cam_models)
                            row += [cfg['vehicle_index'], transform.rotation.yaw, transform.rotation.pitch, transform.rotation.roll, states]
                        writer.writerow(row)
                for i, (q, writer) in enumerate(zip(self.static_cam_queues, self.segmented_writers)):
                    image = q.get(timeout=2.0)
                    if self.record_fov:
                        while image.frame < world_frame:
                            image = q.get(timeout=2.0)
                        if image.frame != world_frame:
                            raise RuntimeError('Camera frame does not match the FOV/trajectory tick')
                    array = np.frombuffer(image.raw_data, dtype=np.uint8)
                    rgb = array.reshape((image.height, image.width, 4))[:, :, :3]
                    writer.write_frame(rgb)
                if self.stop_when_empty and self.next_spawn_idx == len(self.vehicle_configs) and not self.active_vehicles:
                    break
            else:
                if self.stop_when_empty and (self.active_vehicles or self.next_spawn_idx < len(self.vehicle_configs)):
                    raise RuntimeError('Recording reached its time budget before all vehicles completed')
        finally:
            self._cleanup()

    def _cleanup(self):
        errors = []
        def attempt(action):
            try:
                action()
            except (RuntimeError, OSError) as exc:
                errors.append(str(exc))
        if self.pedestrians is not None:
            attempt(self.pedestrians.cleanup)
        for cfg in self.active_vehicles.values():
            attempt(cfg['actor'].destroy)
        for cam in self.static_cameras:
            attempt(cam.stop)
            attempt(cam.destroy)
        for writer in self.segmented_writers:
            attempt(writer.close)
        self.settings.synchronous_mode = False
        attempt(lambda: self.world.apply_settings(self.settings))
        print('\n🧹 Cleaned up simulation.')
        if errors:
            import sys
            message = 'Cleanup errors: ' + '; '.join(errors)
            if sys.exc_info()[0] is None:
                raise RuntimeError(message)
            print(message, file=sys.stderr)
