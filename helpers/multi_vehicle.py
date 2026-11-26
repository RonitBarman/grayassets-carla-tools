"""Short multi-car samples with staggered spawn and despawn events."""
import csv
import math
from pathlib import Path
import random


def validate_settings(cars, delta_seconds, min_overlap_seconds=0):
    if isinstance(cars, bool) or not isinstance(cars, int) or cars < 1:
        raise ValueError('CARS_PER_SAMPLE must be a positive integer')
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


def candidate_orders(lengths, windows):
    """At most 24 deterministic orderings, regardless of vehicle count.

    Rotations preserve exhaustive coverage for the old 1–3 car case. For larger
    scenes this is a bounded heuristic; sampled plans can be rejected/resampled.
    """
    base = list(range(len(lengths)))
    seen = set()
    orders = [sorted(base, key=key, reverse=reverse)
              for key in [lambda i: lengths[i], lambda i: windows[i][0],
                          lambda i: windows[i][1], lambda i: windows[i][1]-windows[i][0]]
              for reverse in [False, True]]
    for sequence in [base, base[::-1]]:
        orders.extend(sequence[k:] + sequence[:k] for k in range(min(len(base), 8)))
    for order in orders:
        order = tuple(order)
        if order not in seen:
            seen.add(order)
            yield order


def stagger_visible_schedule(durations, windows, delta_seconds, min_overlap_seconds, fps=30):
    """Schedule known per-car visible windows, expressed as relative frame bounds.

    Try a bounded set of useful vehicle orders (no factorial enumeration). For a common visible interval [0, required),
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
    for order in candidate_orders(lengths, windows):
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
    for index in range(s['CARS_PER_SAMPLE']):
        vehicle_type = random.choice(s['VEHICLE_TYPES'])
        if not available:
            available = directions.copy()
        direction = random.choice(available)
        available.remove(direction)
        speed = random.uniform(*s['SPEED_RANGE'])
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('Vehicle speeds must be positive and finite')
        color = random.choice(s['COLORS'])
        lane = random.choice(s['LANES'])
        offset = lane_offset(s)
        spec = dict(vehicle_index=index, vehicle_type=vehicle_type,
            vehicle_color_name=color, vehicle_color=s['COLOR_MAP'][color],
            direction=direction, lane=lane, lane_random_offset=offset, speed=speed,
            driving_mode=s['DRIVING_MODE'], movement='straight', target_x='', target_y='')
        if s['DRIVING_MODE'] == 'agent':
            from helpers.agents import get_spawn_target_config
            movement = random.choice(s['MOVEMENTS'])
            loc, target, yaw = get_spawn_target_config(direction, movement)
            spec.update(movement=movement, target_x=target.x, target_y=target.y,
                        spawn_x=loc.x, spawn_y=loc.y, yaw=yaw, vx=0, vy=0,
                        duration=s['MAX_DURATION'])
        else:
            loc, vel, yaw, duration = get_spawn_config(direction, speed, lane, offset)
            spec.update(spawn_x=loc.x, spawn_y=loc.y, vx=vel.x, vy=vel.y, yaw=yaw, duration=duration)
        vehicles.append(spec)
    return vehicles


def configure_environment(world, settings, lighting):
    import carla
    from helpers.weather import get_sun_angles
    if settings['DISABLE_TRAFFIC_LIGHTS']:
        for light in world.get_actors().filter('traffic.traffic_light*'):
            light.set_state(carla.TrafficLightState.Green)
            light.freeze(True)
    if settings['DISABLE_WEATHER']:
        world.set_weather(carla.WeatherParameters(cloudiness=0, precipitation=0,
            precipitation_deposits=0, wind_intensity=0, sun_azimuth_angle=0, sun_altitude_angle=90))
    if lighting is not None:
        world.set_weather(carla.WeatherParameters(**get_sun_angles('Los Angeles', '2025-10-01', lighting)))


def plan_group(settings, world, planner, cache, camera_poses):
    from helpers.visibility import camera_models, predict_visible_window
    from helpers.driving import calibrate_route
    s = settings
    minimum = s['MIN_OVERLAP_SECONDS']
    cameras = camera_models(camera_poses) if s['DRIVING_MODE'] == 'agent' else None
    for attempt in range(100):
        vehicles = choose_vehicles(s)
        windows = []
        try:
            for spec in vehicles:
                if s['DRIVING_MODE'] == 'agent':
                    key = tuple(spec[k] for k in ['vehicle_type','spawn_x','spawn_y','target_x','target_y','yaw','speed'])
                    if key not in cache:
                        print(f"Calibrating {spec['direction']} / {spec['movement']} at {spec['speed']:.2f} m/s", flush=True)
                        try:
                            cache[key] = calibrate_route(world, spec, s, planner, cameras)
                        except NoFeasibleSchedule as exc:
                            cache[key] = (None, str(exc))
                        if cache[key][0] is not None:
                            print(f'  Route duration: {cache[key][0]:.2f}s', flush=True)
                    duration, window = cache[key]
                    if duration is None:
                        raise NoFeasibleSchedule(window)
                    spec['duration'] = duration
                else:
                    window = predict_visible_window(spec, camera_poses=camera_poses) if minimum else None
                windows.append(window)
            durations = [v['duration'] for v in vehicles]
            schedule = stagger_visible_schedule(durations, windows, s['DELTA_SECONDS'],
                minimum + .25 if minimum else 0)
            return dict(vehicles=vehicles, schedule=schedule, windows=windows, attempts=attempt + 1)
        except NoFeasibleSchedule:
            continue
    raise ValueError(f'No feasible scene after 100 plans: cars={s["CARS_PER_SAMPLE"]}, '
        f'MIN_OVERLAP_SECONDS={minimum}, DELTA_SECONDS={s["DELTA_SECONDS"]}. '
        'Adjust the count, timing, routes or speed range; there is no fixed vehicle cap.')


def generate_sample(settings, index, lighting, parent_folder, *, seed=1, groups=1):
    import carla
    from helpers.continuous import CARLASimulator
    from helpers.encoding import combine_videos2gridview
    from helpers.visibility import measure_visible_overlap
    from helpers.driving import AgentDriver
    from helpers.cameras import select_camera_poses
    s = settings
    fps = 30
    cars, delta, minimum = s['CARS_PER_SAMPLE'], s['DELTA_SECONDS'], s['MIN_OVERLAP_SECONDS']
    validate_settings(cars, delta, minimum)
    name = (f'index{index:04d}_{s["DRIVING_MODE"]}_{cars}cars_{s["PEDESTRIAN_COUNT"]}pedestrians'
            f'_delta{delta:g}_overlap{minimum:g}')
    if s['MODE'] == 'continuous':
        name = f'continuous_{groups}groups_' + name
    folder = Path(parent_folder) / name
    output = Path(s['DATASET_DIR']) / folder
    if output.exists():
        # Only a successful, verified recording is resumable.
        import json
        marker = output / 'recording_complete.json'
        if not marker.is_file():
            raise ValueError(f'Existing recording is incomplete: {output}; use a new experiment name')
        saved = json.loads(marker.read_text())
        if saved['settings'] != scene_settings(s, groups, seed):
            raise ValueError(f'Existing recording settings differ: {output}; use a new experiment name')
        for group in range(groups):
            indices = range(group*cars, (group+1)*cars)
            overlap = measure_visible_overlap(output/'all_vehicle_trajectories.csv', cars, fps, indices)
            if overlap['overlap_frames'] < math.ceil(minimum*fps):
                raise ValueError(f'Existing recording fails the overlap constraint: {output}')
        # Skipping still consumes planning choices, preserving later sample seeds.
        random.setstate(tuple_state(saved['random_state_after']))
        print(f'Skipping verified recording: {output}')
        return
    camera_poses = select_camera_poses(s['CAMERA_CONFIGS'], s['VARIED_CAMERA'])
    client = carla.Client(s['HOST'], s['PORT'])
    client.set_timeout(60)
    world = client.get_world()
    if world.get_map().name.split('/')[-1] != s['TOWN']:
        world = client.load_world(s['TOWN'])
    configure_environment(world, s, lighting)
    planner = None
    if s['DRIVING_MODE'] == 'agent':
        from agents.navigation.global_route_planner import GlobalRoutePlanner
        planner = GlobalRoutePlanner(world.get_map(), 2.0)
    cache = {}
    plans = []
    group_start = 0
    gap_before = 0
    scene_budget = 0
    for group in range(groups):
        plan = plan_group(s, world, planner, cache, camera_poses)
        plan['group'] = group
        plan['start'] = group_start
        plan['gap_before'] = gap_before
        plans.append(plan)
        group_start += max(end for _, end in plan['schedule'])
        buffer = random.choice(s['BUFFER_DURATIONS']) if s['MODE'] == 'continuous' else 0
        gap_before = math.ceil(max(buffer, delta)*fps)
        group_start += gap_before
        # Allow agent completion and staggered ready-to-exit queues to run later
        # than calibration estimates, including continuous groups delayed by traffic.
        scene_budget += (max(start for start, _ in plan['schedule']) +
            math.ceil(s['MAX_DURATION']*fps if s['DRIVING_MODE']=='agent' else max(v['duration'] for v in plan['vehicles'])*fps)
            + math.ceil((cars*delta + max(buffer,delta))*fps) + 2)
    sim = CARLASimulator(str(folder), output_root=str(s['DATASET_DIR']),
        host=s['HOST'], port=s['PORT'], fps=fps,
        disable_traffic_lights=s['DISABLE_TRAFFIC_LIGHTS'], disable_weather=s['DISABLE_WEATHER'],
        maintain_velocity=True, record_fov=True, pedestrian_count=s['PEDESTRIAN_COUNT'],
        pedestrian_seed=seed+index, exit_delta_frames=math.ceil(delta*fps),
        group_gap_frames=math.ceil(delta*fps) if s['MODE']=='continuous' else 0,
        stop_when_empty=True, camera_poses=camera_poses)
    rows = []
    try:
        write_csv(output/'camera_poses.csv', [dict(camera_index=i, x=p[0], y=p[1], z=p[2],
            pitch=r[0], yaw=r[1], roll=r[2]) for i, (p, r) in enumerate(camera_poses, 1)])
        configure_environment(world, s, lighting)
        for plan in plans:
            for spec, (start, end), window in zip(plan['vehicles'],plan['schedule'],plan['windows']):
                vehicle_index = plan['group']*cars + spec['vehicle_index']
                start += plan['start']; end += plan['start']
                factory = None
                if s['DRIVING_MODE']=='agent':
                    factory = lambda actor, spec=spec: AgentDriver(actor, spec, s, planner)
                sim.schedule_vehicle(vehicle_type=spec['vehicle_type'], velocity=(spec['vx'],spec['vy']),
                    spawn_pos=(spec['spawn_x'],spec['spawn_y']), spawn_yaw=spec['yaw'],
                    spawn_time=start/fps, duration=(end-start)/fps,
                    vehicle_color_name=spec['vehicle_color_name'], vehicle_color=spec['vehicle_color'],
                    spawn_frame=start, despawn_frame=end, vehicle_index=vehicle_index,
                    group_index=plan['group'], driver_factory=factory, timeout_frames=math.ceil(s['MAX_DURATION']*fps),
                    group_gap_frames=plan['gap_before'])
                rows.append(dict(spec, vehicle_index=vehicle_index, group_index=plan['group'],
                    spawn_frame=start, despawn_frame=end, spawn_time=start/fps, despawn_time=end/fps,
                    predicted_visible_start_frame=start+window[0] if window else '',
                    predicted_visible_end_frame=start+window[1] if window else ''))
        write_csv(output/'vehicle_schedule.csv', rows)
    except Exception:
        sim._cleanup()
        raise
    try:
        sim.run_simulation(scene_budget/fps)
    finally:
        actual = {cfg['vehicle_index']:cfg for cfg in sim.vehicle_configs}
        for row in rows:
            cfg = actual[row['vehicle_index']]
            row['actual_spawn_frame'] = cfg.get('actual_spawn_frame','')
            row['actual_despawn_frame'] = cfg.get('actual_despawn_frame','')
            row['actual_spawn_time'] = cfg['actual_spawn_frame']/fps if 'actual_spawn_frame' in cfg else ''
            row['actual_despawn_time'] = cfg['actual_despawn_frame']/fps if 'actual_despawn_frame' in cfg else ''
        write_csv(output/'vehicle_schedule.csv', rows)
    summaries = []
    for plan in plans:
        group = plan['group']
        overlap = measure_visible_overlap(output/'all_vehicle_trajectories.csv', cars, fps,
                                         range(group*cars,(group+1)*cars))
        overlap.update(group_index=group,required_overlap_seconds=minimum,
            constraint_met=overlap['overlap_frames'] >= math.ceil(minimum*fps), planning_attempts=plan['attempts'],
            pedestrians_requested=s['PEDESTRIAN_COUNT'],pedestrians_spawned=sim.pedestrians.count if sim.pedestrians else 0)
        summaries.append(overlap)
    write_csv(output/'visibility_summary.csv',summaries)
    for key in ['actual_spawn_frame','actual_despawn_frame']:
        events = sorted(row[key] for row in rows)
        if any(not isinstance(e,int) for e in events) or any(b-a<math.ceil(delta*fps) for a,b in zip(events,events[1:])):
            raise ValueError(f'Recorded {key} events violated delta; recording retained at {output}')
    if not all(row['constraint_met'] for row in summaries):
        raise ValueError(f'Recorded camera overlap did not meet {minimum:g}s in every group. '
                         f'Recording retained at {output}; use a fresh experiment name to retry.')
    if s['GRID_VIDEO']:
        combine_videos2gridview(str(folder),str(s['DATASET_DIR']))
    import json
    (output/'recording_complete.json').write_text(json.dumps(dict(settings=scene_settings(s,groups,seed),
        random_state_after=random.getstate()),indent=2)+'\n')
    print(f'Completed recording: {output}')
    print('Shared visible seconds per group:',[row['overlap_seconds'] for row in summaries])


def write_csv(path, rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader();writer.writerows(rows)


def tuple_state(value):
    return tuple(tuple_state(x) for x in value) if isinstance(value,list) else value


def scene_settings(settings,groups,seed):
    keys=['MODE','DRIVING_MODE','AGENT_MODE','BEHAVIOR','CARS_PER_SAMPLE','PEDESTRIAN_COUNT',
          'DELTA_SECONDS','MIN_OVERLAP_SECONDS','VEHICLE_TYPES','COLORS','COLOR_MAP','DIRECTIONS',
          'SPEED_RANGE','LANES','LANE_RANDOM_OFFSET','LANE_OFFSET_RANGE','MOVEMENTS','MAX_DURATION',
          'ARRIVAL_RADIUS','TOWN','DISABLE_TRAFFIC_LIGHTS','DISABLE_WEATHER','GRID_VIDEO','BUFFER_DURATIONS','TIMES',
          'VARIED_CAMERA','CAMERA_CONFIGS']
    # Normalize tuples to JSON lists so saved and live settings compare equally.
    import json
    result = json.loads(json.dumps(dict((key,settings[key]) for key in keys),sort_keys=True))
    result.update(groups=groups,seed=seed)
    return result
