"""Short multi-car samples with staggered spawn and despawn events."""
import csv
from itertools import permutations
import math
from pathlib import Path
import random


def validate_settings(cars, delta_seconds, min_overlap_seconds=0):
    if isinstance(cars, bool) or not isinstance(cars, int) or not 1 <= cars <= 3:
        raise ValueError('CARS_PER_SAMPLE must be an integer from 1 to 3')
    if (isinstance(delta_seconds, bool) or not isinstance(delta_seconds, (int, float))
            or not math.isfinite(delta_seconds) or delta_seconds <= 0):
        raise ValueError('DELTA_SECONDS must be finite and greater than zero')
    if (isinstance(min_overlap_seconds, bool) or not isinstance(min_overlap_seconds, (int, float))
            or not math.isfinite(min_overlap_seconds) or min_overlap_seconds < 0):
        raise ValueError('MIN_OVERLAP_SECONDS must be finite and nonnegative')


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


class NoFeasibleSchedule(ValueError):
    """A set of sampled routes cannot meet the visibility and delta constraints."""


def stagger_visible_schedule(durations, windows, delta_seconds, min_overlap_seconds, fps=30):
    """Schedule known per-car visible windows, expressed as relative frame bounds.

    Try at most six vehicle orders. For a common visible interval [0, required),
    each spawn must lie in [required - visible_end, -visible_start]. Propagating
    the entrance/exit gap lower bounds finds a feasible schedule for each order,
    without changing speeds or extending cars beyond their routes. Finally shift
    all events equally so the first spawn is frame zero. Return original car order.
    """
    validate_settings(len(durations), delta_seconds, min_overlap_seconds)
    baseline = stagger_schedule(durations, delta_seconds, fps)
    if min_overlap_seconds == 0:
        return baseline
    if len(windows) != len(durations):
        raise ValueError('Each car needs one visible window')
    lengths = [end - start for start, end in baseline]
    required = math.ceil(min_overlap_seconds * fps)
    gap = math.ceil(delta_seconds * fps)
    for window, length in zip(windows, lengths):
        if window is None:
            raise NoFeasibleSchedule('A sampled route never enters camera coverage')
        start, end = window
        if not 0 <= start < end <= length:
            raise ValueError('Visible windows must be within the vehicle lifetime')
        if end - start < required:
            raise NoFeasibleSchedule('A sampled car is not visible long enough')
    best = None
    for order in permutations(range(len(durations))):
        starts = [0] * len(order)
        previous = None
        for car in order:
            visible_start, visible_end = windows[car]
            start = required - visible_end
            if previous is not None:
                start = max(start, starts[previous] + gap,
                            starts[previous] + lengths[previous] + gap - lengths[car])
            if start > -visible_start:
                break
            starts[car] = start
            previous = car
        else:
            shift = -min(starts)
            candidate = [(start + shift, start + shift + length)
                         for start, length in zip(starts, lengths)]
            key = (max(end for _, end in candidate), tuple(start for start, _ in candidate))
            if best is None or key < best[0]:
                best = (key, candidate)
    if best is None:
        raise NoFeasibleSchedule('Camera overlap and staggered entrances/exits cannot both fit these routes')
    return best[1]


def choose_vehicles(settings):
    from helpers.generation import lane_offset
    from helpers.spawn import get_spawn_config

    s = settings
    vehicles = []
    directions = list(dict.fromkeys(s['DIRECTIONS']))
    available = directions.copy()
    for vehicle_index in range(s['CARS_PER_SAMPLE']):
        vehicle_type = random.choice(s['VEHICLE_TYPES'])
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
    return vehicles


def generate_sample(settings, index, lighting, parent_folder):
    import carla
    from helpers.continuous import CARLASimulator
    from helpers.encoding import combine_videos2gridview
    from helpers.weather import get_sun_angles
    from helpers.visibility import predict_visible_window, measure_visible_overlap

    s = settings
    cars, delta = s['CARS_PER_SAMPLE'], s['DELTA_SECONDS']
    minimum = s['MIN_OVERLAP_SECONDS']
    validate_settings(cars, delta, minimum)
    folder = Path(parent_folder) / f'index{index:04d}_{cars}cars_delta{delta:g}_overlap{minimum:g}'
    output = Path(s['DATASET_DIR']) / folder
    fps = 30
    # Rejected plans cost no rendering. Keep a small margin for suspension/physics
    # near FOV boundaries, and verify the actual recorded overlap afterward.
    for attempt in range(100):
        vehicles = choose_vehicles(s)
        if minimum == 0:
            schedule = stagger_schedule([v['duration'] for v in vehicles], delta, fps)
            windows = None
            break
        try:
            if min(v['duration'] for v in vehicles) < minimum:
                raise NoFeasibleSchedule('The requested overlap exceeds a vehicle lifetime')
            windows = [predict_visible_window(v, fps) for v in vehicles]
            schedule = stagger_visible_schedule([v['duration'] for v in vehicles], windows,
                delta, minimum + .25, fps)
            break
        except NoFeasibleSchedule:
            continue
    else:
        raise ValueError(f'No feasible multi-vehicle plan after 100 attempts: '
                         f'MIN_OVERLAP_SECONDS={minimum}, DELTA_SECONDS={delta}. '
                         'Reduce either value or allow slower/longer routes.')
    if output.exists():
        if minimum:
            if not (output / 'visibility_summary.csv').is_file():
                raise ValueError(f'Existing sample {output} is incomplete; '
                                 'use a new experiment name before rerunning.')
            observed = measure_visible_overlap(output / 'all_vehicle_trajectories.csv', cars, fps)
            if observed['overlap_frames'] < math.ceil(minimum * fps):
                raise ValueError(f'Existing sample {output} does not meet MIN_OVERLAP_SECONDS; '
                                 'use a new experiment name before rerunning.')
        print(f'Skipping existing sample: {output}')
        return
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
            row = dict(spec, spawn_frame=start, despawn_frame=end,
                       spawn_time=start / fps, despawn_time=end / fps)
            if windows is not None:
                visible_start, visible_end = windows[spec['vehicle_index']]
                row.update(predicted_visible_start_frame=start + visible_start,
                           predicted_visible_end_frame=start + visible_end)
            rows.append(row)
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
    observed = measure_visible_overlap(output / 'all_vehicle_trajectories.csv', cars, fps)
    observed.update(required_overlap_seconds=minimum,
                    constraint_met=observed['overlap_frames'] >= math.ceil(minimum * fps),
                    planning_attempts=attempt + 1)
    with (output / 'visibility_summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(observed))
        writer.writeheader()
        writer.writerow(observed)
    if not observed['constraint_met']:
        raise ValueError(f"Recorded camera overlap was {observed['overlap_seconds']:.3f}s, "
                         f'below the required {minimum:g}s. Sample retained at {output}; '
                         'use a fresh experiment name to retry.')
    print(f"All cars visible together for {observed['overlap_seconds']:.3f}s "
          f'(required {minimum:g}s)')
    if s['GRID_VIDEO']:
        combine_videos2gridview(str(folder), str(s['DATASET_DIR']))
