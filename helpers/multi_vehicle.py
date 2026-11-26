"""Short multi-car samples with staggered spawn and despawn events."""
import csv
import math
from pathlib import Path
import random


def validate_settings(cars, delta_seconds):
    if isinstance(cars, bool) or not isinstance(cars, int) or not 1 <= cars <= 3:
        raise ValueError('CARS_PER_SAMPLE must be an integer from 1 to 3')
    if (isinstance(delta_seconds, bool) or not isinstance(delta_seconds, (int, float))
            or not math.isfinite(delta_seconds) or delta_seconds <= 0):
        raise ValueError('DELTA_SECONDS must be finite and greater than zero')


def stagger_schedule(durations, delta_seconds, fps=30):
    """Return exact frame bounds, separating both event sequences by delta.

    Different traversal durations can otherwise cause simultaneous exits even
    with staggered starts. Later cars start only when BOTH gaps are satisfied.
    Delta is rounded up to whole frames. Large deltas can eliminate overlap.
    """
    validate_settings(len(durations), delta_seconds)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('fps must be positive and finite')
    gap = math.ceil(delta_seconds * fps)
    schedule = []
    for duration in durations:
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Vehicle durations must be positive and finite')
        length = math.ceil(duration * fps)
        start = 0
        if schedule:
            previous_start, previous_end = schedule[-1]
            start = max(previous_start + gap, previous_end + gap - length)
        schedule.append((start, start + length))
    return schedule


def generate_sample(settings, index, lighting, parent_folder):
    import carla
    from helpers.continuous import CARLASimulator
    from helpers.encoding import combine_videos2gridview
    from helpers.generation import lane_offset
    from helpers.spawn import get_spawn_config
    from helpers.weather import get_sun_angles

    s = settings
    cars, delta = s['CARS_PER_SAMPLE'], s['DELTA_SECONDS']
    validate_settings(cars, delta)
    folder = Path(parent_folder) / f'index{index:04d}_{cars}cars_delta{delta:g}'
    output = Path(s['DATASET_DIR']) / folder
    if output.exists():
        print(f'Skipping existing sample: {output}')
        # Still consume this sample's choices so later samples retain their seed.
    vehicles = []
    directions = list(dict.fromkeys(s['DIRECTIONS']))
    available = directions.copy()
    for vehicle_index in range(cars):
        vehicle_type = random.choice(s['VEHICLE_TYPES'])
        # Prefer separate approaches to avoid spawning into another car's space.
        if not available:
            available = directions.copy()
        direction = random.choice(available)
        available.remove(direction)
        speed = random.uniform(*s['SPEED_RANGE'])
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('Multi-vehicle speeds must be positive and finite')
        color = random.choice(s['COLORS'])
        lane = random.choice(s['LANES'])
        offset = lane_offset(s)
        loc, vel, yaw, duration = get_spawn_config(direction, speed, lane, offset)
        vehicles.append(dict(vehicle_index=vehicle_index, vehicle_type=vehicle_type,
            vehicle_color_name=color, vehicle_color=s['COLOR_MAP'][color],
            direction=direction, lane=lane, lane_random_offset=offset,
            spawn_x=loc.x, spawn_y=loc.y, vx=vel.x, vy=vel.y, yaw=yaw,
            duration=duration))
    if output.exists():
        return
    fps = 30
    schedule = stagger_schedule([v['duration'] for v in vehicles], delta, fps)
    client = carla.Client(s['HOST'], s['PORT'])
    client.set_timeout(60)
    world = client.get_world()
    if world.get_map().name.split('/')[-1] != s['TOWN']:
        world = client.load_world(s['TOWN'])
    sim = CARLASimulator(str(folder), output_root=str(s['DATASET_DIR']),
        host=s['HOST'], port=s['PORT'], fps=fps,
        disable_traffic_lights=s['DISABLE_TRAFFIC_LIGHTS'],
        disable_weather=s['DISABLE_WEATHER'], maintain_velocity=True, record_fov=True)
    try:
        if lighting is not None:
            world.set_weather(carla.WeatherParameters(**get_sun_angles('Los Angeles', '2025-10-01', lighting)))
        rows = []
        for spec, (start, end) in zip(vehicles, schedule):
            sim.schedule_vehicle(vehicle_type=spec['vehicle_type'],
                velocity=(spec['vx'], spec['vy']), spawn_pos=(spec['spawn_x'], spec['spawn_y']),
                spawn_yaw=spec['yaw'], spawn_time=start / fps, duration=(end - start) / fps,
                vehicle_color_name=spec['vehicle_color_name'], vehicle_color=spec['vehicle_color'],
                spawn_frame=start, despawn_frame=end, vehicle_index=spec['vehicle_index'])
            rows.append(dict(spec, spawn_frame=start, despawn_frame=end,
                spawn_time=start / fps, despawn_time=end / fps))
        with (output / 'vehicle_schedule.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f'Multi-vehicle sample: {output}')
        for row in rows:
            print(f"  Car {row['vehicle_index']}: enters {row['spawn_time']:.3f}s, exits {row['despawn_time']:.3f}s")
    except Exception:
        sim._cleanup()
        raise
    # Include the last scheduled despawn tick before closing the shared videos.
    sim.run_simulation((max(end for _, end in schedule) + 2) / fps)
    if s['GRID_VIDEO']:
        combine_videos2gridview(str(folder), str(s['DATASET_DIR']))
