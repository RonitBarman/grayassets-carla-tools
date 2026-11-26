"""Camera pose selection and conversion to CARLA transforms."""
import math
import random

DEFAULT_CAMERA_POSES = (
    ((-60, 24, 10), (-60, 0, 0)),
    ((-40, 24, 10), (-60, 180, 0)),
    ((-50, 30, 10), (-60, 270, 0)),
    ((-50, 10, 10), (-60, 90, 0)),
)


def validate_camera_configs(configs, varied):
    if not isinstance(varied, bool):
        raise ValueError('VARIED_CAMERA must be True or False')
    if len(configs) != 4:
        raise ValueError('CAMERA_CONFIGS needs one entry for each of the four cameras')
    for index, config in enumerate(configs, 1):
        for key in ('positions', 'rotations'):
            options = config.get(key, [])
            if not options:
                raise ValueError(f'Camera {index} needs at least one {key} option')
            for option in options:
                if len(option) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                          or not math.isfinite(v) for v in option):
                    raise ValueError(f'Camera {index} {key} options must be finite numeric triples')


def select_camera_poses(configs, varied=False):
    validate_camera_configs(configs, varied)
    return tuple((tuple(random.choice(config['positions']) if varied else config['positions'][0]),
                  tuple(random.choice(config['rotations']) if varied else config['rotations'][0]))
                 for config in configs)


def camera_transforms(poses=None):
    import carla
    return [carla.Transform(carla.Location(x=position[0], y=position[1], z=position[2]),
                            carla.Rotation(pitch=rotation[0], yaw=rotation[1], roll=rotation[2]))
            for position, rotation in (DEFAULT_CAMERA_POSES if poses is None else poses)]
