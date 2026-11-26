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


def validate_generation(settings):
    from helpers.multi_vehicle import validate_settings
    from helpers.cameras import validate_camera_configs
    s = settings
    validate_camera_configs(s['CAMERA_CONFIGS'], s['VARIED_CAMERA'])
    if s['MODE'] not in ('separate', 'continuous'):
        raise ValueError('MODE controls recording only: separate or continuous. '
                         'Use DRIVING_MODE, CARS_PER_SAMPLE and PEDESTRIAN_COUNT for scene content.')
    if s['DRIVING_MODE'] not in ('fixed_velocity', 'agent'):
        raise ValueError('DRIVING_MODE must be fixed_velocity or agent')
    for key, minimum in [('NUM_SAMPLES', 1), ('PEDESTRIAN_COUNT', 0)]:
        value = s[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f'{key} must be an integer >= {minimum}')
    validate_settings(s['CARS_PER_SAMPLE'], s['DELTA_SECONDS'], s['MIN_OVERLAP_SECONDS'])
    if s['AGENT_MODE'] not in ('behavioragent', 'basicagent'):
        raise ValueError('AGENT_MODE must be behavioragent or basicagent')
    if s['BEHAVIOR'] not in ('normal', 'cautious', 'aggressive'):
        raise ValueError('BEHAVIOR must be normal, cautious or aggressive')
    if not math.isfinite(s['MAX_DURATION']) or s['MAX_DURATION'] <= 0:
        raise ValueError('MAX_DURATION must be positive and finite')
    for key in ['VEHICLE_TYPES', 'COLORS', 'DIRECTIONS', 'LANES', 'SEEDS', 'TIMES']:
        if not s[key]:
            raise ValueError(f'{key} must not be empty')
    if s['DRIVING_MODE'] == 'agent' and not s['MOVEMENTS']:
        raise ValueError('MOVEMENTS must not be empty for agent driving')
    if s['MODE'] == 'continuous' and (not s['BUFFER_DURATIONS'] or
            any(not math.isfinite(x) or x < 0 for x in s['BUFFER_DURATIONS'])):
        raise ValueError('BUFFER_DURATIONS must contain nonnegative finite seconds')


def generate(settings):
    s = settings
    validate_generation(s)
    if s['DRIVING_MODE'] == 'agent':
        api = Path(s['CARLA_ROOT']) / 'PythonAPI' / 'carla'
        if str(api) not in sys.path:
            sys.path.insert(0, str(api))
    from helpers.server import start_carla_server, stop_carla_server
    from helpers.multi_vehicle import generate_sample
    Path(s['DATASET_DIR']).mkdir(parents=True, exist_ok=True)
    for seed in s['SEEDS']:
        for lighting in s['TIMES']:
            process = start_carla_server(s['CARLA_ROOT'], s['PORT']) if s['START_SERVER'] else None
            try:
                folder = Path(f'seed{seed}') / (f'sun_at_{lighting}' if lighting is not None else 'fixed_weather')
                random.seed(seed)
                if s['MODE'] == 'continuous':
                    generate_sample(s, 0, lighting, folder, seed=seed, groups=s['NUM_SAMPLES'])
                else:
                    for index in range(s['NUM_SAMPLES']):
                        generate_sample(s, index, lighting, folder, seed=seed)
            finally:
                if process is not None:
                    stop_carla_server(process)
