"""The four static intersection viewpoints in the reference capture."""


def camera_transforms():
    import carla
    return [
        carla.Transform(carla.Location(x=-60, y=24, z=10), carla.Rotation(pitch=-60, yaw=0)),
        carla.Transform(carla.Location(x=-40, y=24, z=10), carla.Rotation(pitch=-60, yaw=180)),
        carla.Transform(carla.Location(x=-50, y=30, z=10), carla.Rotation(pitch=-60, yaw=270)),
        carla.Transform(carla.Location(x=-50, y=10, z=10), carla.Rotation(pitch=-60, yaw=90)),
    ]
