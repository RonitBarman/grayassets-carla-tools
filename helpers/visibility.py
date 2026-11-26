"""Predict and verify simultaneous coverage by the four static camera frustums."""
import ast
import csv
from functools import lru_cache
import math


def camera_models(poses=None):
    from helpers.cameras import DEFAULT_CAMERA_POSES
    from helpers.geometry import build_camera_frustum
    return [build_camera_frustum(position, *rotation,
                120, 1280, 720, near_clip=.1, far_clip=1000)
            for position, rotation in (DEFAULT_CAMERA_POSES if poses is None else poses)]


def actor_fov_states(actor, cameras):
    from helpers.geometry import get_brand_id, vehicle_bbox_corners_world, classify_bbox_vs_frustum
    transform = actor.get_transform()
    loc = transform.location
    brand = get_brand_id(actor)
    if brand is not None:
        corners = vehicle_bbox_corners_world((loc.x, loc.y, loc.z), transform.rotation.yaw, brand)
    else:
        import numpy as np
        corners = np.array([(v.x, v.y, v.z) for v in actor.bounding_box.get_world_vertices(transform)])
    return [classify_bbox_vs_frustum(corners, camera) for camera in cameras]


def longest_window(flags):
    best = None
    start = None
    for frame, visible in enumerate(list(flags) + [False]):
        if visible and start is None:
            start = frame
        elif not visible and start is not None:
            if best is None or frame - start > best[1] - best[0]:
                best = (start, frame)
            start = None
    return best


def predict_visible_window(vehicle, fps=30, camera_poses=None):
    from helpers.cameras import DEFAULT_CAMERA_POSES
    poses = DEFAULT_CAMERA_POSES if camera_poses is None else camera_poses
    return _predict_window(vehicle['vehicle_type'], vehicle['spawn_x'], vehicle['spawn_y'],
                           vehicle['vx'], vehicle['vy'], vehicle['yaw'], vehicle['duration'], fps, poses)


@lru_cache(maxsize=1024)
def _predict_window(vehicle_type, x, y, vx, vy, yaw, duration, fps, poses):
    from helpers.geometry import BRAND_ID_MAP, vehicle_bbox_corners_world, classify_bbox_vs_frustum

    if vehicle_type not in BRAND_ID_MAP:
        raise ValueError(f'Camera-overlap planning has no bounding-box model for {vehicle_type}. '
                         'Use Model 3, Nissan Patrol or Mercedes Sprinter, or set MIN_OVERLAP_SECONDS=0.')
    cameras = camera_models(poses)
    best = None
    start = None
    count = math.ceil(duration * fps)
    for frame in range(count + 1):
        visible = False
        if frame < count:
            # The capture labels and sensor image are both sampled after a tick.
            t = (frame + 1) / fps
            corners = vehicle_bbox_corners_world((x + vx*t, y + vy*t, .3), yaw, BRAND_ID_MAP[vehicle_type])
            visible = any(classify_bbox_vs_frustum(corners, camera) > 0 for camera in cameras)
        if visible and start is None:
            start = frame
        elif not visible and start is not None:
            if best is None or frame - start > best[1] - best[0]:
                best = (start, frame)
            start = None
    return best


def measure_visible_overlap(trajectory, cars, fps=30, vehicle_indices=None):
    """Longest continuous run where each car is in at least one camera FOV.

    Positive FOV states include partial visibility. Frustum labels do not model
    occlusion. Bounds are frame indices with an exclusive end, like lifetimes.
    """
    visible = {}
    expected = set(range(cars)) if vehicle_indices is None else set(vehicle_indices)
    with open(trajectory, newline='') as stream:
        for row in csv.DictReader(stream):
            vehicle_index = int(row['vehicle_index'])
            if vehicle_index not in expected:
                continue
            states = ast.literal_eval(row['fov_states'])
            if any(state in (1, 2) for state in states):
                visible.setdefault(int(row['frame']), set()).add(vehicle_index)
    best_start = best_end = 0
    start = previous = None
    for frame in sorted(visible):
        if visible[frame] != expected:
            start = previous = None
            continue
        if previous is None or frame != previous + 1:
            start = frame
        previous = frame
        if frame + 1 - start > best_end - best_start:
            best_start, best_end = start, frame + 1
    frames = best_end - best_start
    return dict(overlap_start_frame=best_start, overlap_end_frame=best_end,
                overlap_frames=frames, overlap_seconds=frames / fps)
