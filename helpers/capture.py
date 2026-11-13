import os
import csv
import queue
import numpy as np
from tqdm import tqdm
from helpers.encoding import FFmpegWriter, combine_videos2gridview
from helpers.cameras import camera_transforms as make_camera_transforms
from helpers.weather import get_sun_angles
from helpers.geometry import build_camera_frustum, get_brand_id, vehicle_bbox_corners_world, classify_bbox_vs_frustum

def gen_car_moving_videos_and_save_trajectory_updated(duration, vehicle_type, vehicle_color_name='White', vehicle_color='255,255,255', velocity=(0.0, 0.0), spawn_pos=(0.0, 0.0), lane=0, lane_random_offset=0, yaw=0, folder_name='test', combine_videos2gridview_flag=False, disable_traffic_lights=True, disable_weather=True, index=None, time_str=None, *, output_root='output', host='localhost', port=2000):
    import carla
    print(f'  === Generating {duration}sec videos for {vehicle_type} with velocity {velocity}, spawn position {spawn_pos}, yaw {yaw} ===')
    fps = 30
    camera_fov = 120
    camera_resolution = (1280, 720)
    vehicle_desired_velocity = carla.Vector3D(x=velocity[0], y=velocity[1], z=0.0)
    vehicle_spawn_location = carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3)
    camera_transforms = make_camera_transforms()
    if folder_name is None:
        output_folder = f'dataset/{vehicle_type}_{vehicle_color_name}_vx{velocity[0]:.3f}_vy{velocity[1]:.3f}_x{spawn_pos[0]:.3f}_y{spawn_pos[1]:.3f}_yaw{yaw:.1f}'
    elif index is None:
        output_folder = f'{folder_name}/{vehicle_type}_{vehicle_color_name}_vx{velocity[0]:.3f}_vy{velocity[1]:.3f}_x{spawn_pos[0]:.3f}_y{spawn_pos[1]:.3f}_yaw{yaw:.1f}_lane{lane}_randomoffset{lane_random_offset:.2f}'
    else:
        output_folder = f'{folder_name}/index{index:04d}_{vehicle_type}_{vehicle_color_name}_vx{velocity[0]:.3f}_vy{velocity[1]:.3f}_x{spawn_pos[0]:.3f}_y{spawn_pos[1]:.3f}_yaw{yaw:.1f}_lane{lane}_randomoffset{lane_random_offset:.2f}'
    if os.path.exists(f'{output_root}/{output_folder}'):
        print(f'============== SKIP!!! Output folder {output_folder} already exists ==============')
        with open(os.path.join(output_root, 'debug.txt'), 'a') as f:
            f.write(f'Output folder {output_folder} already exists\n\n')
        return
    os.makedirs(f'{output_root}/{output_folder}', exist_ok=True)
    client = carla.Client(host, port)
    client.set_timeout(20.0)
    world = client.get_world()
    map_name = world.get_map().name
    print('Current CARLA map:', map_name)
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = 1 / fps
    world.apply_settings(settings)
    blueprint_library = world.get_blueprint_library()
    if disable_traffic_lights:
        traffic_lights = world.get_actors().filter('traffic.traffic_light*')
        for light in traffic_lights:
            light.set_state(carla.TrafficLightState.Green)
            light.freeze(True)
    if disable_weather:
        print('Disabling weather...')
        weather = carla.WeatherParameters(cloudiness=0.0, precipitation=0.0, precipitation_deposits=0.0, wind_intensity=0.0, sun_azimuth_angle=0.0, sun_altitude_angle=90.0)
        world.set_weather(weather)
        print('Weather disabled')
    if time_str is not None:
        angles = get_sun_angles('Los Angeles', '2025-10-01', time_str)
        print(angles)
        weather = carla.WeatherParameters(sun_azimuth_angle=angles['sun_azimuth_angle'], sun_altitude_angle=angles['sun_altitude_angle'])
        world.set_weather(weather)
    print('Weather:', world.get_weather())
    vehicle_bp = blueprint_library.filter(vehicle_type)[0]
    if vehicle_bp.has_attribute('color'):
        available_colors = vehicle_bp.get_attribute('color').recommended_values
        print('Available colors:', available_colors)
        print('Setting color to:', vehicle_color_name)
        vehicle_bp.set_attribute('color', vehicle_color)
    vehicle_spawn_point = carla.Transform(vehicle_spawn_location, carla.Rotation(pitch=0.0, yaw=yaw, roll=0.0))
    vehicle = world.spawn_actor(vehicle_bp, vehicle_spawn_point)
    static_camera_bp = blueprint_library.find('sensor.camera.rgb')
    static_camera_bp.set_attribute('image_size_x', str(camera_resolution[0]))
    static_camera_bp.set_attribute('image_size_y', str(camera_resolution[1]))
    static_camera_bp.set_attribute('fov', str(camera_fov))
    static_cameras = []
    static_cam_queues = []
    ffmpeg_writers = []
    vehicle_trajectory = [['frame', 'timestamp', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'fov_states']]

    def enqueue_camera_data(q):
        return lambda image: q.put(image)
    for i, transform in enumerate(camera_transforms):
        camera = world.spawn_actor(static_camera_bp, transform)
        static_cameras.append(camera)
        cam_queue = queue.Queue()
        static_cam_queues.append(cam_queue)
        camera.listen(enqueue_camera_data(cam_queue))
        writer = FFmpegWriter(output_prefix=f'{output_root}/{output_folder}/video_camera{i + 1}', width=camera_resolution[0], height=camera_resolution[1], fps=fps)
        ffmpeg_writers.append(writer)
    cam_models = []
    for i in range(len(static_cameras)):
        car_tf = camera_transforms[i]
        loc, rot = (car_tf.location, car_tf.rotation)
        cam_pos = np.array([loc.x, loc.y, loc.z], dtype=float)
        cam_model = build_camera_frustum(cam_pos, pitch_deg=rot.pitch, yaw_deg=rot.yaw, roll_deg=rot.roll, hfov_deg=float(camera_fov), width_px=camera_resolution[0], height_px=camera_resolution[1], near_clip=0.1, far_clip=1000.0)
        cam_models.append(cam_model)
    try:
        print('\nStarting simulation...')
        for frame in tqdm(range(duration * fps), desc='  Capturing Frames', unit='frame'):
            world.tick()
            vehicle.set_target_velocity(vehicle_desired_velocity)
            vehicle_location = vehicle.get_transform().location
            vehicle_velocity = vehicle.get_velocity()
            brand_id = get_brand_id(vehicle)
            if brand_id is None:
                print(f'frame {frame}: brand_id is None')
                continue
            tf = vehicle.get_transform()
            pos_xyz = (tf.location.x, tf.location.y, tf.location.z)
            yaw_deg = tf.rotation.yaw
            world_corners = vehicle_bbox_corners_world(pos_xyz, yaw_deg, brand_id)
            vehicle_fov_states = []
            for i in range(len(static_cameras)):
                state_i = classify_bbox_vs_frustum(world_corners, cam_models[i])
                vehicle_fov_states.append(state_i)
            for idx, (queue_, writer) in enumerate(zip(static_cam_queues, ffmpeg_writers)):
                static_image = queue_.get(timeout=2.0)
                static_array = np.frombuffer(static_image.raw_data, dtype=np.uint8)
                rgb_array = static_array.reshape((static_image.height, static_image.width, 4))[:, :, :3]
                writer.write_frame(rgb_array)
            vehicle_trajectory.append([frame, frame / fps, vehicle_location.x, vehicle_location.y, vehicle_location.z, vehicle_velocity.x, vehicle_velocity.y, vehicle_velocity.z, vehicle_fov_states])
    finally:
        print('\nStopping simulation...\n')
        for camera in static_cameras:
            camera.stop()
            camera.destroy()
        vehicle.destroy()
        settings.synchronous_mode = False
        world.apply_settings(settings)
        print(f'\nSaving trajectory to {output_folder}/vehicle_trajectory.csv\n')
        with open(f'{output_root}/{output_folder}/vehicle_trajectory.csv', 'w') as f:
            writer = csv.writer(f)
            writer.writerows(vehicle_trajectory)
        for writer in ffmpeg_writers:
            writer.close()
        if combine_videos2gridview_flag:
            combine_videos2gridview(output_folder, output_root)
