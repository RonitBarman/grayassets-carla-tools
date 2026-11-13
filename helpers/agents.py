"""Legacy agent and pedestrian capture, with explicit paths and shared geometry."""
import os
import math
import time
import csv
import queue
import logging
import carla
import numpy as np
from numpy import random as np_random
from tqdm import tqdm
from helpers.encoding import FFmpegWriter, combine_videos2gridview
from helpers.weather import get_sun_angles
from helpers.cameras import camera_transforms as make_camera_transforms
from helpers.geometry import build_camera_frustum, get_brand_id, vehicle_bbox_corners_world, classify_bbox_vs_frustum

def nearest_waypoint(world_map, x, y):
    loc = carla.Location(x=float(x), y=float(y), z=0.0)
    wp = world_map.get_waypoint(loc, project_to_road=True, lane_type=carla.LaneType.Driving)
    return wp

def distance(a: carla.Location, b: carla.Location) -> float:
    dx = a.x - b.x
    dy = a.y - b.y
    dz = a.z - b.z
    return math.sqrt(dx * dx + dy * dy + dz * dz)

def set_synchronous(world, traffic_manager, sync=True, fps=20):
    settings = world.get_settings()
    settings.synchronous_mode = sync
    settings.fixed_delta_seconds = 1.0 / fps if sync else None
    world.apply_settings(settings)
    traffic_manager.set_synchronous_mode(sync)
    traffic_manager.set_global_distance_to_leading_vehicle(1.0)
    traffic_manager.set_hybrid_physics_mode(True)
    traffic_manager.set_random_device_seed(0)

def gen_car_moving_videos_and_save_trajectory_from_drive_to_xy(duration, vehicle_type, vehicle_color_name='White', vehicle_color='255,255,255', velocity=(0.0, 0.0), spawn_pos=(0.0, 0.0), target_pos=(0.0, 0.0), speed_mps=10.0, yaw=0, town='Town01', fps=30, behavior='normal', arrival_radius=3.0, max_runtime=180.0, debug=False, lane=0, lane_random_offset=0, folder_name='test', combine_videos2gridview_flag=False, disable_traffic_lights=True, disable_weather=True, index=None, time_str=None, direction=None, movement=None, agent_mode='behavioragent', *, output_root='output', host='localhost', port=2000):
    print(f'  === {direction} {movement}: {vehicle_type} with spawn position {spawn_pos}, target position {target_pos}, speed {speed_mps} m/s, yaw {yaw} ===')
    fps = 30
    camera_fov = 120
    camera_resolution = (1280, 720)
    vehicle_desired_velocity = carla.Vector3D(x=velocity[0], y=velocity[1], z=0.0)
    vehicle_spawn_location = carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3)
    camera_transforms = make_camera_transforms()
    if folder_name is None:
        output_folder = f'dataset/{vehicle_type}_{vehicle_color_name}_x1{spawn_pos[0]:.3f}_y1{spawn_pos[1]:.3f}_x2{target_pos[0]:.3f}_y2{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    elif index is None:
        output_folder = f'{folder_name}/{vehicle_type}_{vehicle_color_name}_{direction}_{movement}_x1{spawn_pos[0]:.3f}_y1{spawn_pos[1]:.3f}_x2{target_pos[0]:.3f}_y2{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    else:
        output_folder = f'{folder_name}/index{index:04d}_{vehicle_type}_{vehicle_color_name}_{direction}_{movement}_x1_{spawn_pos[0]:.3f}_y1_{spawn_pos[1]:.3f}_x2_{target_pos[0]:.3f}_y2_{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    if os.path.exists(f'{output_root}/{output_folder}'):
        print(f'============== SKIP!!! Output folder {output_folder} already exists ==============')
        with open(os.path.join(output_root, 'debug.txt'), 'a') as f:
            f.write(f'Output folder {output_folder} already exists\n\n')
        return
    os.makedirs(f'{output_root}/{output_folder}', exist_ok=True)
    client = carla.Client(host, port)
    client.set_timeout(15.0)
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
    world_map = world.get_map()
    start_wp = nearest_waypoint(world_map, spawn_pos[0], spawn_pos[1])
    goal_wp = nearest_waypoint(world_map, target_pos[0], target_pos[1])
    vehicle_spawn_point = carla.Transform(location=carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3), rotation=carla.Rotation(pitch=0.0, yaw=yaw, roll=0.0))
    vehicle = world.spawn_actor(vehicle_bp, vehicle_spawn_point)
    speed_kmh = speed_mps * 3.6
    agent = None
    for _ in range(1):
        world.tick()
    if agent_mode == 'behavioragent':
        from agents.navigation.behavior_agent import BehaviorAgent
        agent = BehaviorAgent(vehicle, behavior=behavior)
        agent.set_target_speed(float(speed_kmh))
        goal_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
        agent.set_destination(start_location=vehicle_spawn_point.location, end_location=goal_loc)
        agent.pedestrian_avoid_manager = lambda waypoint: (False, None, -1)
        print(f'BehaviorAgent created with target speed {speed_kmh} km/h')
    elif agent_mode == 'basicagent':
        from agents.navigation.basic_agent import BasicAgent
        agent = BasicAgent(vehicle)
        agent.set_target_speed(float(speed_kmh))
        goal_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
        agent.set_destination(start_location=vehicle_spawn_point.location, end_location=goal_loc)
        agent.pedestrian_avoid_manager = lambda waypoint: (False, None, -1)
        print(f'BasicAgent created with target speed {speed_kmh} km/h')
    static_camera_bp = blueprint_library.find('sensor.camera.rgb')
    static_camera_bp.set_attribute('image_size_x', str(camera_resolution[0]))
    static_camera_bp.set_attribute('image_size_y', str(camera_resolution[1]))
    static_camera_bp.set_attribute('fov', str(camera_fov))
    static_cameras = []
    static_cam_queues = []
    ffmpeg_writers = []
    vehicle_trajectory = [['frame', 'timestamp', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'yaw', 'pitch', 'roll', 'fov_states']]

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
        reached_time = None
        t0 = time.time()
        last_control = carla.VehicleControl(throttle=0.3, steer=0.0, brake=0.0)
        last_recover_frame = -10000
        RECOVER_COOLDOWN = 10

        def reset_destination_safely(agent, vehicle, target_pos):
            cur_loc = vehicle.get_location()
            try:
                wp_cur = world.get_map().get_waypoint(cur_loc, project_to_road=True, lane_type=carla.LaneType.Driving)
                wp_goal = world.get_map().get_waypoint(carla.Location(x=target_pos[0], y=target_pos[1], z=0.0), project_to_road=True, lane_type=carla.LaneType.Driving)
                start_loc = wp_cur.transform.location if wp_cur else cur_loc
                end_loc = wp_goal.transform.location if wp_goal else carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
            except Exception:
                start_loc = cur_loc
                end_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
            try:
                agent.set_destination(start_location=start_loc, end_location=end_loc)
            except Exception:
                pass
        for frame in tqdm(range(duration * fps), desc='  Capturing Frames', unit='frame'):
            world.tick()
            use_control = last_control
            if agent is not None:
                try:
                    if agent.done():
                        if reached_time is None:
                            reached_time = time.time()
                        if time.time() - reached_time > 2.0:
                            print(f'Agent reached destination at frame {frame}')
                            use_control = carla.VehicleControl(throttle=0.0, brake=1.0)
                            vehicle.apply_control(use_control)
                            print('Stopping video recording early - destination reached')
                            break
                    ctrl = agent.run_step()
                    use_control = ctrl
                except AttributeError as e:
                    msg = str(e)
                    if 'is_junction' in msg or '_incoming_waypoint' in msg or 'NoneType' in msg:
                        print(f'frame {frame}: transient waypoint error -> using last_control; err={e}')
                        if frame - last_recover_frame >= RECOVER_COOLDOWN:
                            last_recover_frame = frame
                            reset_destination_safely(agent, vehicle, target_pos)
                    else:
                        print(f'frame {frame}: AttributeError {e} -> using last_control')
                except Exception as e:
                    print(f'frame {frame}: agent.run_step failed: {e} -> using last_control')
            else:
                vehicle.set_target_velocity(vehicle_desired_velocity)
            vehicle.apply_control(use_control)
            last_control = use_control
            vehicle_location = vehicle.get_transform().location
            distance_to_target = distance(vehicle_location, carla.Location(x=target_pos[0], y=target_pos[1], z=0.0))
            if distance_to_target < arrival_radius:
                print(f'Vehicle is within {arrival_radius}m of target at frame {frame} (distance: {distance_to_target:.2f}m)')
                print('Stopping video recording early - target position reached')
                break
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
            vehicle_trajectory.append([frame, frame / fps, vehicle_location.x, vehicle_location.y, vehicle_location.z, vehicle_velocity.x, vehicle_velocity.y, vehicle_velocity.z, tf.rotation.yaw, tf.rotation.pitch, tf.rotation.roll, vehicle_fov_states])
    finally:
        print('\nStopping simulation...\n')
        for camera in static_cameras:
            camera.stop()
            camera.destroy()
        if agent is not None:
            vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            for _ in range(5):
                world.tick()
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

def gen_car_moving_videos_and_save_trajectory_from_drive_to_xy_withPedestrian(duration, vehicle_type, vehicle_color_name='White', vehicle_color='255,255,255', velocity=(0.0, 0.0), spawn_pos=(0.0, 0.0), target_pos=(0.0, 0.0), speed_mps=10.0, yaw=0, town='Town01', fps=30, behavior='normal', arrival_radius=3.0, max_runtime=180.0, debug=False, lane=0, lane_random_offset=0, folder_name='test', combine_videos2gridview_flag=False, disable_traffic_lights=True, disable_weather=True, index=None, time_str=None, direction=None, movement=None, agent_mode='behavioragent', debug_info=False, pedestrian_num=10, pedestrian_seed=0, *, output_root='output', host='localhost', port=2000):
    print(f'  === {direction} {movement}: {vehicle_type} with spawn position {spawn_pos}, target position {target_pos}, speed {speed_mps} m/s, yaw {yaw} ===')
    camera_fov = 120
    camera_resolution = (1280, 720)
    vehicle_desired_velocity = carla.Vector3D(x=velocity[0], y=velocity[1], z=0.0)
    vehicle_spawn_location = carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3)
    camera_transforms = make_camera_transforms()
    if folder_name is None:
        output_folder = f'dataset/{vehicle_type}_{vehicle_color_name}_x1{spawn_pos[0]:.3f}_y1{spawn_pos[1]:.3f}_x2{target_pos[0]:.3f}_y2{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    elif index is None:
        output_folder = f'{folder_name}/{vehicle_type}_{vehicle_color_name}_{direction}_{movement}_x1{spawn_pos[0]:.3f}_y1{spawn_pos[1]:.3f}_x2{target_pos[0]:.3f}_y2{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    else:
        output_folder = f'{folder_name}/index{index:04d}_{vehicle_type}_{vehicle_color_name}_{direction}_{movement}_x1_{spawn_pos[0]:.3f}_y1_{spawn_pos[1]:.3f}_x2_{target_pos[0]:.3f}_y2_{target_pos[1]:.3f}_yaw{yaw:.1f}_speed{speed_mps:.3f}'
    if os.path.exists(f'{output_root}/{output_folder}'):
        print(f'============== SKIP!!! Output folder {output_folder} already exists ==============')
        with open(os.path.join(output_root, 'debug.txt'), 'a') as f:
            f.write(f'Output folder {output_folder} already exists\n\n')
        return
    os.makedirs(f'{output_root}/{output_folder}', exist_ok=True)
    client = carla.Client(host, port)
    client.set_timeout(15.0)
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
    world_map = world.get_map()
    start_wp = nearest_waypoint(world_map, spawn_pos[0], spawn_pos[1])
    goal_wp = nearest_waypoint(world_map, target_pos[0], target_pos[1])
    vehicle_spawn_point = carla.Transform(location=carla.Location(x=spawn_pos[0], y=spawn_pos[1], z=0.3), rotation=carla.Rotation(pitch=0.0, yaw=yaw, roll=0.0))
    vehicle = world.spawn_actor(vehicle_bp, vehicle_spawn_point)
    speed_kmh = speed_mps * 3.6
    agent = None
    for _ in range(1):
        world.tick()
    if agent_mode == 'behavioragent':
        from agents.navigation.behavior_agent import BehaviorAgent
        agent = BehaviorAgent(vehicle, behavior=behavior)
        agent.set_target_speed(float(speed_kmh))
        goal_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
        agent.set_destination(start_location=vehicle_spawn_point.location, end_location=goal_loc)
        agent.pedestrian_avoid_manager = lambda waypoint: (False, None, -1)
        print(f'BehaviorAgent created with target speed {speed_kmh} km/h')
    elif agent_mode == 'basicagent':
        from agents.navigation.basic_agent import BasicAgent
        agent = BasicAgent(vehicle)
        agent.set_target_speed(float(speed_kmh))
        goal_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
        agent.set_destination(start_location=vehicle_spawn_point.location, end_location=goal_loc)
        agent.pedestrian_avoid_manager = lambda waypoint: (False, None, -1)
        print(f'BasicAgent created with target speed {speed_kmh} km/h')
    walkers_list = []
    all_walker_ids = []
    if pedestrian_num > 0:
        np_random.seed(pedestrian_seed)
        world.set_pedestrians_seed(pedestrian_seed)
        blueprintsWalkers = blueprint_library.filter('walker.pedestrian.*')
        walker_spawn_points = []
        for i in range(pedestrian_num):
            spawn_point = carla.Transform()
            loc = world.get_random_location_from_navigation()
            if loc is not None:
                spawn_point.location = loc
                walker_spawn_points.append(spawn_point)
        batch = []
        requested_walker_speeds = []
        for spawn_point in walker_spawn_points:
            walker_bp = np_random.choice(blueprintsWalkers)
            if walker_bp.has_attribute('speed'):
                requested_walker_speeds.append(walker_bp.get_attribute('speed').recommended_values[1])
            else:
                requested_walker_speeds.append(0.0)
            batch.append(carla.command.SpawnActor(walker_bp, spawn_point))
        results = client.apply_batch_sync(batch, True)
        spawned_walker_speeds = []
        for i in range(len(results)):
            if results[i].error:
                logging.error(results[i].error)
            else:
                walkers_list.append({'id': results[i].actor_id})
                spawned_walker_speeds.append(requested_walker_speeds[i])
        batch = []
        walker_controller_bp = blueprint_library.find('controller.ai.walker')
        for i in range(len(walkers_list)):
            batch.append(carla.command.SpawnActor(walker_controller_bp, carla.Transform(), walkers_list[i]['id']))
        results = client.apply_batch_sync(batch, True)
        for i in range(len(results)):
            if results[i].error:
                logging.error(results[i].error)
            else:
                walkers_list[i]['con'] = results[i].actor_id
        for i in range(len(walkers_list)):
            all_walker_ids.append(walkers_list[i]['con'])
            all_walker_ids.append(walkers_list[i]['id'])
        all_walker_actors = world.get_actors(all_walker_ids)
        world.tick()
        world.set_pedestrians_cross_factor(0.0)
        for i in range(0, len(all_walker_ids), 2):
            all_walker_actors[i].start()
            all_walker_actors[i].go_to_location(world.get_random_location_from_navigation())
            all_walker_actors[i].set_max_speed(float(spawned_walker_speeds[int(i / 2)]))
        print(f'Spawned {len(walkers_list)} pedestrians')
    static_camera_bp = blueprint_library.find('sensor.camera.rgb')
    static_camera_bp.set_attribute('image_size_x', str(camera_resolution[0]))
    static_camera_bp.set_attribute('image_size_y', str(camera_resolution[1]))
    static_camera_bp.set_attribute('fov', str(camera_fov))
    static_cameras = []
    static_cam_queues = []
    ffmpeg_writers = []
    vehicle_trajectory = [['frame', 'timestamp', 'x', 'y', 'z', 'vx', 'vy', 'vz', 'yaw', 'pitch', 'roll', 'fov_states']]

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
        reached_time = None
        t0 = time.time()
        last_control = carla.VehicleControl(throttle=0.3, steer=0.0, brake=0.0)
        last_recover_frame = -10000
        RECOVER_COOLDOWN = 10

        def reset_destination_safely(agent, vehicle, target_pos):
            cur_loc = vehicle.get_location()
            try:
                wp_cur = world.get_map().get_waypoint(cur_loc, project_to_road=True, lane_type=carla.LaneType.Driving)
                wp_goal = world.get_map().get_waypoint(carla.Location(x=target_pos[0], y=target_pos[1], z=0.0), project_to_road=True, lane_type=carla.LaneType.Driving)
                start_loc = wp_cur.transform.location if wp_cur else cur_loc
                end_loc = wp_goal.transform.location if wp_goal else carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
            except Exception:
                start_loc = cur_loc
                end_loc = carla.Location(x=target_pos[0], y=target_pos[1], z=0.0)
            try:
                agent.set_destination(start_location=start_loc, end_location=end_loc)
            except Exception:
                pass
        for frame in tqdm(range(duration * fps), desc='  Capturing Frames', unit='frame'):
            world.tick()
            use_control = last_control
            if agent is not None:
                try:
                    if agent.done():
                        if reached_time is None:
                            reached_time = time.time()
                        if time.time() - reached_time > 2.0:
                            print(f'Agent reached destination at frame {frame}')
                            use_control = carla.VehicleControl(throttle=0.0, brake=1.0)
                            vehicle.apply_control(use_control)
                            print('Stopping video recording early - destination reached')
                            break
                    ctrl = agent.run_step()
                    use_control = ctrl
                except AttributeError as e:
                    msg = str(e)
                    if 'is_junction' in msg or '_incoming_waypoint' in msg or 'NoneType' in msg:
                        if debug_info:
                            print(f'frame {frame}: transient waypoint error -> using last_control; err={e}')
                        if frame - last_recover_frame >= RECOVER_COOLDOWN:
                            last_recover_frame = frame
                            reset_destination_safely(agent, vehicle, target_pos)
                    elif debug_info:
                        print(f'frame {frame}: AttributeError {e} -> using last_control')
                except Exception as e:
                    if debug_info:
                        print(f'frame {frame}: agent.run_step failed: {e} -> using last_control')
            else:
                vehicle.set_target_velocity(vehicle_desired_velocity)
            vehicle.apply_control(use_control)
            last_control = use_control
            vehicle_location = vehicle.get_transform().location
            distance_to_target = distance(vehicle_location, carla.Location(x=target_pos[0], y=target_pos[1], z=0.0))
            if distance_to_target < arrival_radius:
                print(f'Vehicle is within {arrival_radius}m of target at frame {frame} (distance: {distance_to_target:.2f}m)')
                print('Stopping video recording early - target position reached')
                break
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
            vehicle_trajectory.append([frame, frame / fps, vehicle_location.x, vehicle_location.y, vehicle_location.z, vehicle_velocity.x, vehicle_velocity.y, vehicle_velocity.z, tf.rotation.yaw, tf.rotation.pitch, tf.rotation.roll, vehicle_fov_states])
    finally:
        print('\nStopping simulation...\n')
        for camera in static_cameras:
            camera.stop()
            camera.destroy()
        if all_walker_ids:
            all_walker_actors = world.get_actors(all_walker_ids)
            for i in range(0, len(all_walker_ids), 2):
                all_walker_actors[i].stop()
            print(f'Destroying {len(walkers_list)} pedestrians')
            client.apply_batch([carla.command.DestroyActor(x) for x in all_walker_ids])
        if agent is not None:
            vehicle.apply_control(carla.VehicleControl(throttle=0.0, brake=1.0))
            for _ in range(5):
                world.tick()
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

def get_spawn_target_config(initial_location: str, movement: str):
    """
    Converts semantic spawn settings into CARLA-compatible spawn and target locations.

    Args:
        initial_location (str): One of ['up', 'down', 'left', 'right']
        movement (str): One of ['straight', 'turn_right', 'turn_left']

    Returns:
        Tuple[carla.Location, carla.Location]: (spawn_location, target_location)
    """
    X0 = -10
    X1 = -41
    X2 = -45
    X3 = -49
    X4 = -53
    X5 = -87
    Y0 = -20
    Y1 = 13
    Y2 = 17
    Y3 = 24
    Y4 = 28
    Y5 = 60
    UP_Start_0 = carla.Location(x=X0, y=Y2)
    UP_Start_1 = carla.Location(x=X0, y=Y1)
    UP_End_0 = carla.Location(x=X0, y=Y3)
    UP_End_1 = carla.Location(x=X0, y=Y4)
    DOWN_Start_0 = carla.Location(x=X5, y=Y3)
    DOWN_Start_1 = carla.Location(x=X5, y=Y4)
    DOWN_End_0 = carla.Location(x=X5, y=Y2)
    DOWN_End_1 = carla.Location(x=X5, y=Y1)
    LEFT_Start_0 = carla.Location(x=X3, y=Y0)
    LEFT_Start_1 = carla.Location(x=X4, y=Y0)
    LEFT_End_0 = carla.Location(x=X2, y=Y0)
    LEFT_End_1 = carla.Location(x=X1, y=Y0)
    RIGHT_Start_0 = carla.Location(x=X2, y=Y5)
    RIGHT_Start_1 = carla.Location(x=X1, y=Y5)
    RIGHT_End_0 = carla.Location(x=X3, y=Y5)
    RIGHT_End_1 = carla.Location(x=X4, y=Y5)
    yaw = 0
    if initial_location == 'up':
        yaw = 180
        if movement == 'straight' or movement == 'straight_lane0':
            spawn_location = UP_Start_0
            target_location = DOWN_End_0
        elif movement == 'straight_lane1':
            spawn_location = UP_Start_1
            target_location = DOWN_End_1
        elif movement == 'turn_right':
            spawn_location = UP_Start_1
            target_location = LEFT_End_1
        elif movement == 'turn_left':
            spawn_location = UP_Start_0
            target_location = RIGHT_End_0
        else:
            raise ValueError(f"Invalid movement: '{movement}'. Must be one of ['straight', 'turn_right', 'turn_left'].")
    elif initial_location == 'down':
        yaw = 0
        if movement == 'straight' or movement == 'straight_lane0':
            spawn_location = DOWN_Start_0
            target_location = UP_End_0
        elif movement == 'straight_lane1':
            spawn_location = DOWN_Start_1
            target_location = UP_End_1
        elif movement == 'turn_right':
            spawn_location = DOWN_Start_1
            target_location = RIGHT_End_1
        elif movement == 'turn_left':
            spawn_location = DOWN_Start_0
            target_location = LEFT_End_0
        else:
            raise ValueError(f"Invalid movement: '{movement}'. Must be one of ['straight', 'turn_right', 'turn_left'].")
    elif initial_location == 'left':
        yaw = 90
        if movement == 'straight' or movement == 'straight_lane0':
            spawn_location = LEFT_Start_0
            target_location = RIGHT_End_0
        elif movement == 'straight_lane1':
            spawn_location = LEFT_Start_1
            target_location = RIGHT_End_1
        elif movement == 'turn_right':
            spawn_location = LEFT_Start_1
            target_location = DOWN_End_1
        elif movement == 'turn_left':
            spawn_location = LEFT_Start_0
            target_location = UP_End_0
        else:
            raise ValueError(f"Invalid movement: '{movement}'. Must be one of ['straight', 'turn_right', 'turn_left'].")
    elif initial_location == 'right':
        yaw = -90
        if movement == 'straight' or movement == 'straight_lane0':
            spawn_location = RIGHT_Start_0
            target_location = LEFT_End_0
        elif movement == 'straight_lane1':
            spawn_location = RIGHT_Start_1
            target_location = LEFT_End_1
        elif movement == 'turn_right':
            spawn_location = RIGHT_Start_1
            target_location = UP_End_1
        elif movement == 'turn_left':
            spawn_location = RIGHT_Start_0
            target_location = DOWN_End_0
        else:
            raise ValueError(f"Invalid movement: '{movement}'. Must be one of ['straight', 'turn_right', 'turn_left'].")
    else:
        raise ValueError(f"Invalid initial_location: '{initial_location}'. Must be one of ['up', 'down', 'left', 'right'].")
    return (spawn_location, target_location, yaw)
