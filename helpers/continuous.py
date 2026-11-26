"""Continuous four-camera recording with scheduled vehicle events."""
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

    def __init__(self, folder_name, fps=30, camera_fov=120, camera_resolution=(1280, 720), output_root='output', host='localhost', port=2000, disable_traffic_lights=True, disable_weather=True, *, maintain_velocity=False, record_fov=False):
        self.fps = fps
        self.output_root = output_root
        self.camera_resolution = camera_resolution
        self.maintain_velocity = maintain_velocity
        self.record_fov = record_fov
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
                for tf in make_camera_transforms()]
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

    def _setup_cameras(self, fov):
        camera_transforms = make_camera_transforms()
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

    def schedule_vehicle(self, vehicle_type, velocity, spawn_pos, spawn_yaw, spawn_time, duration, vehicle_color_name=None, vehicle_color=None, *, spawn_frame=None, despawn_frame=None, vehicle_index=None):
        # Exact frame bounds avoid rounding a planned event into the wrong tick.
        spawn_frame = int(spawn_time * self.fps) if spawn_frame is None else spawn_frame
        despawn_frame = int((spawn_time + duration) * self.fps) if despawn_frame is None else despawn_frame
        self.vehicle_configs.append({'vehicle_type': vehicle_type, 'velocity': carla.Vector3D(x=velocity[0], y=velocity[1], z=0.0), 'spawn_pos': carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3), 'spawn_yaw': spawn_yaw, 'spawn_frame': spawn_frame, 'despawn_frame': despawn_frame, 'vehicle_color_name': vehicle_color_name, 'vehicle_color': vehicle_color, 'vehicle_index': vehicle_index})

    def run_simulation(self, total_duration_sec):
        total_frames = int(total_duration_sec * self.fps)
        self.vehicle_configs.sort(key=lambda cfg: cfg['spawn_frame'])
        try:
            print(f'\n🚦 Starting simulation for {total_duration_sec} seconds ({total_frames} frames)...')
            start_wall_time = time.time()
            for frame in tqdm(range(total_frames), desc='Simulating', unit='frame'):
                self.world.tick()
                to_despawn = [vid for vid, dframe in self.despawn_queue.items() if dframe == frame]
                for vid in to_despawn:
                    actor = self.active_vehicles[vid]['actor']
                    actor.destroy()
                    print(f'🔴 Despawned vehicle {vid} at frame {frame}')
                    del self.active_vehicles[vid]
                    del self.despawn_queue[vid]
                while self.next_spawn_idx < len(self.vehicle_configs) and self.vehicle_configs[self.next_spawn_idx]['spawn_frame'] == frame:
                    cfg = self.vehicle_configs[self.next_spawn_idx]
                    bp = self.blueprint_library.filter(cfg['vehicle_type'])[0]
                    if cfg.get('vehicle_color') and bp.has_attribute('color'):
                        bp.set_attribute('color', cfg['vehicle_color'])
                    elif cfg.get('vehicle_color'):
                        print(f"⚠️ Blueprint {cfg['vehicle_type']} has no color attribute; requested {cfg['vehicle_color_name']}")
                    transform = carla.Transform(cfg['spawn_pos'], carla.Rotation(yaw=cfg['spawn_yaw']))
                    actor = self.world.spawn_actor(bp, transform)
                    actor.set_target_velocity(cfg['velocity'])
                    cfg['actor'] = actor
                    cfg['id'] = actor.id
                    self.active_vehicles[actor.id] = cfg
                    self.despawn_queue[actor.id] = cfg['despawn_frame']
                    print(f'🟢 Spawned vehicle {actor.id} at frame {frame}')
                    self.next_spawn_idx += 1
                with open(self.csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    for vid, cfg in self.active_vehicles.items():
                        actor = cfg['actor']
                        if self.maintain_velocity:
                            actor.set_target_velocity(cfg['velocity'])
                        transform = actor.get_transform()
                        loc = transform.location
                        vel = actor.get_velocity()
                        row = [frame, frame / self.fps, vid, cfg['vehicle_type'], loc.x, loc.y, loc.z, vel.x, vel.y, vel.z]
                        if self.record_fov:
                            from helpers.geometry import get_brand_id, vehicle_bbox_corners_world, classify_bbox_vs_frustum
                            brand = get_brand_id(actor)
                            if brand is not None:
                                corners = vehicle_bbox_corners_world((loc.x, loc.y, loc.z), transform.rotation.yaw, brand)
                            else:
                                corners = np.array([(v.x, v.y, v.z) for v in actor.bounding_box.get_world_vertices(transform)])
                            states = [classify_bbox_vs_frustum(corners, camera) for camera in self.cam_models]
                            row += [cfg['vehicle_index'], transform.rotation.yaw, transform.rotation.pitch, transform.rotation.roll, states]
                        writer.writerow(row)
                for i, (q, writer) in enumerate(zip(self.static_cam_queues, self.segmented_writers)):
                    image = q.get(timeout=2.0)
                    array = np.frombuffer(image.raw_data, dtype=np.uint8)
                    rgb = array.reshape((image.height, image.width, 4))[:, :, :3]
                    writer.write_frame(rgb)
        finally:
            self._cleanup()

    def _cleanup(self):
        for cfg in self.active_vehicles.values():
            cfg['actor'].destroy()
        for cam in self.static_cameras:
            cam.stop()
            cam.destroy()
        self.settings.synchronous_mode = False
        self.world.apply_settings(self.settings)
        for writer in self.segmented_writers:
            writer.close()
        print('\n🧹 Cleaned up simulation.')
