import math

def get_spawn_config(*args, **kwargs):
    import carla
    return _get_spawn_config(carla, *args, **kwargs)

def _get_spawn_config(carla, initial_location: str, speed: float, lane: int=0, lane_random_offset: float=0):
    """
    Converts semantic spawn settings into CARLA-compatible location, velocity, and yaw.

    Args:
        initial_location (str): One of ['up', 'down', 'left', 'right']
        speed (float): Scalar speed in m/s

    Returns:
        Tuple[carla.Location, carla.Vector3D, float]: (spawn_location, velocity_vector, yaw)
    """
    if lane == 1:
        if initial_location == 'up':
            lane_offset = -3.6
        elif initial_location == 'down':
            lane_offset = +3.6
        elif initial_location == 'left':
            lane_offset = -3.6
        elif initial_location == 'right':
            lane_offset = +3.6
    elif lane == 0:
        lane_offset = 0
    mapping = {'up': {'location': carla.Location(x=-40 + 15 + 5 + speed, y=17 + lane_offset + lane_random_offset, z=0.3), 'velocity': carla.Vector3D(x=-speed, y=0.0, z=0.0), 'yaw': 180.0}, 'down': {'location': carla.Location(x=-60 - 25 - 5 - speed, y=24.5 + lane_offset + lane_random_offset, z=0.3), 'velocity': carla.Vector3D(x=+speed, y=0.0, z=0.0), 'yaw': 0.0}, 'left': {'location': carla.Location(x=-50 + 1 + lane_offset + lane_random_offset, y=10 - 15 - 5 - speed, z=0.3), 'velocity': carla.Vector3D(x=0.0, y=+speed, z=0.0), 'yaw': 90.0}, 'right': {'location': carla.Location(x=-50 + 1 + 3.6 + lane_offset + lane_random_offset, y=30 + 25 + 5 + speed, z=0.3), 'velocity': carla.Vector3D(x=0.0, y=-speed, z=0.0), 'yaw': -90.0}}
    if initial_location not in mapping:
        raise ValueError(f"Invalid initial_location: '{initial_location}'. Must be one of {list(mapping.keys())}.")
    if initial_location == 'up' or initial_location == 'down':
        min_duration = (max(mapping['up']['location'].x, mapping['down']['location'].x) - min(mapping['up']['location'].x, mapping['down']['location'].x)) / speed
    elif initial_location == 'left' or initial_location == 'right':
        min_duration = (max(mapping['left']['location'].y, mapping['right']['location'].y) - min(mapping['left']['location'].y, mapping['right']['location'].y)) / speed
    min_duration = math.ceil(min_duration)
    return (mapping[initial_location]['location'], mapping[initial_location]['velocity'], mapping[initial_location]['yaw'], min_duration)
