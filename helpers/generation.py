"""Scenario loops used by step 1; all user settings remain in that script."""
import math
import random
import sys
from pathlib import Path


def lane_offset(settings):
    if not settings['LANE_RANDOM_OFFSET']:
        return 0
    spread = settings['LANE_OFFSET_RANGE']
    return max(-spread, min(spread, round(random.gauss(mu=0, sigma=spread), 2)))


def generate(settings):
    s = settings
    if s['MODE'] not in ('separate', 'multi_vehicle', 'continuous', 'agent', 'pedestrians'):
        raise ValueError('MODE must be separate, multi_vehicle, continuous, agent, or pedestrians')
    if s['NUM_SAMPLES'] < 1:
        raise ValueError('NUM_SAMPLES must be positive')
    if s['MODE'] == 'multi_vehicle':
        from helpers.multi_vehicle import validate_settings
        validate_settings(s['CARS_PER_SAMPLE'], s['DELTA_SECONDS'])
    if s['MODE'] in ('agent', 'pedestrians'):
        api = Path(s['CARLA_ROOT']) / 'PythonAPI' / 'carla'
        if str(api) not in sys.path:
            sys.path.insert(0, str(api))
    from helpers.server import start_carla_server, stop_carla_server
    from helpers.spawn import get_spawn_config
    from helpers.capture import gen_car_moving_videos_and_save_trajectory_updated
    Path(s['DATASET_DIR']).mkdir(parents=True, exist_ok=True)
    for seed in s['SEEDS']:
        for lighting in s['TIMES']:
            process = start_carla_server(s['CARLA_ROOT'], s['PORT']) if s['START_SERVER'] else None
            try:
                folder = Path(f'seed{seed}') / (f'sun_at_{lighting}' if lighting is not None else 'fixed_weather')
                if s['MODE'] == 'continuous':
                    continuous(s, seed, lighting, folder)
                    continue
                random.seed(seed)
                for index in range(s['NUM_SAMPLES']):
                    if s['MODE'] == 'multi_vehicle':
                        from helpers.multi_vehicle import generate_sample
                        generate_sample(s, index, lighting, folder)
                        continue
                    vehicle = random.choice(s['VEHICLE_TYPES'])
                    # Preserve the different random-selection order in the legacy modes.
                    if s['MODE'] == 'separate':
                        direction = random.choice(s['DIRECTIONS'])
                    speed = random.uniform(*s['SPEED_RANGE'])
                    color_name = random.choice(s['COLORS'])
                    color = s['COLOR_MAP'][color_name]
                    lane = random.choice(s['LANES'])
                    offset = lane_offset(s)
                    common = dict(vehicle_type=vehicle, vehicle_color_name=color_name,
                        vehicle_color=color, lane=lane, lane_random_offset=offset,
                        folder_name=str(folder), index=index, time_str=lighting,
                        disable_traffic_lights=s['DISABLE_TRAFFIC_LIGHTS'],
                        disable_weather=s['DISABLE_WEATHER'],
                        combine_videos2gridview_flag=s['GRID_VIDEO'],
                        output_root=str(s['DATASET_DIR']), host=s['HOST'], port=s['PORT'])
                    if s['MODE'] == 'separate':
                        loc, vel, yaw, duration = get_spawn_config(direction, speed, lane, offset)
                        gen_car_moving_videos_and_save_trajectory_updated(
                            duration=math.ceil(duration), velocity=(vel.x, vel.y),
                            spawn_pos=(loc.x, loc.y), yaw=yaw, **common)
                    else:
                        from helpers import agents
                        direction = random.choice(s['DIRECTIONS'])
                        movement = random.choice(s['MOVEMENTS'])
                        loc, target, yaw = agents.get_spawn_target_config(direction, movement)
                        capture = agents.gen_car_moving_videos_and_save_trajectory_from_drive_to_xy
                        if s['MODE'] == 'pedestrians':
                            capture = agents.gen_car_moving_videos_and_save_trajectory_from_drive_to_xy_withPedestrian
                            common.update(pedestrian_num=s['PEDESTRIAN_COUNT'], pedestrian_seed=seed + index)
                        capture(duration=s['MAX_DURATION'], spawn_pos=(loc.x, loc.y),
                            target_pos=(target.x, target.y), speed_mps=speed, yaw=yaw,
                            town=s['TOWN'], fps=30, behavior=s['BEHAVIOR'],
                            arrival_radius=s['ARRIVAL_RADIUS'], direction=direction,
                            movement=movement, agent_mode=s['AGENT_MODE'], **common)
            finally:
                if process is not None:
                    stop_carla_server(process)


def continuous(s, seed, lighting, folder):
    from helpers.continuous import CARLASimulator
    from helpers import scheduling
    from helpers.weather import get_sun_angles
    from helpers.encoding import combine_videos2gridview
    import carla
    client = carla.Client(s['HOST'], s['PORT'])
    client.set_timeout(60)
    world = client.get_world()
    if world.get_map().name.split('/')[-1] != s['TOWN']:
        world = client.load_world(s['TOWN'])
    sim = CARLASimulator(str(folder), output_root=str(s['DATASET_DIR']),
        host=s['HOST'], port=s['PORT'], disable_traffic_lights=s['DISABLE_TRAFFIC_LIGHTS'],
        disable_weather=s['DISABLE_WEATHER'])
    if lighting is not None:
        world.set_weather(carla.WeatherParameters(**get_sun_angles('Los Angeles', '2025-10-01', lighting)))
    schedule = scheduling.schedule_random_vehicles_with_color
    extras = dict(vehicle_color_name_list=s['COLORS'], vehicle_color_map=s['COLOR_MAP'],
        lane_list=s['LANES'], lane_random_offset_flag=s['LANE_RANDOM_OFFSET'],
        lane_random_offset_range=s['LANE_OFFSET_RANGE'])
    if s['WITH_OPPOSITE']:
        schedule = scheduling.schedule_random_vehicles_with_following_and_opposite_vehicles
        extras = dict(allow_followers=s['WITH_FOLLOWERS'])
    elif s['WITH_FOLLOWERS']:
        schedule = scheduling.schedule_random_vehicles_with_followingvehicles
        extras = {}
    duration = schedule(sim, s['NUM_SAMPLES'], s['VEHICLE_TYPES'], s['DIRECTIONS'],
        s['SPEED_RANGE'], s['BUFFER_DURATIONS'], seed, 1.0, str(folder), **extras)
    sim.run_simulation(duration)
    if s['GRID_VIDEO']:
        combine_videos2gridview(str(folder), str(s['DATASET_DIR']))
