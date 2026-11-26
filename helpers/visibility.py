"""Predict and verify simultaneous coverage by the four static camera frustums."""
import ast
import csv
from functools import lru_cache
import math


def predict_visible_window(vehicle, fps=30):
    return _predict_window(vehicle['vehicle_type'], vehicle['spawn_x'], vehicle['spawn_y'],
                           vehicle['vx'], vehicle['vy'], vehicle['yaw'], vehicle['duration'], fps)


@lru_cache(maxsize=1024)
def _predict_window(vehicle_type, x, y, vx, vy, yaw, duration, fps):
    from helpers.cameras import camera_transforms
    from helpers.geometry import BRAND_ID_MAP, build_camera_frustum, vehicle_bbox_corners_world, classify_bbox_vs_frustum

    if vehicle_type not in BRAND_ID_MAP:
        raise ValueError(f'Camera-overlap planning has no bounding-box model for {vehicle_type}. '
                         'Use Model 3, Nissan Patrol or Mercedes Sprinter, or set MIN_OVERLAP_SECONDS=0.')
    cameras = [build_camera_frustum((tf.location.x, tf.location.y, tf.location.z),
                tf.rotation.pitch, tf.rotation.yaw, tf.rotation.roll,
                120, 1280, 720, near_clip=.1, far_clip=1000)
               for tf in camera_transforms()]
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


def measure_visible_overlap(trajectory, cars, fps=30):
    """Longest continuous run where each car is in at least one camera FOV.

    Positive FOV states include partial visibility. Frustum labels do not model
    occlusion. Bounds are frame indices with an exclusive end, like lifetimes.
    """
    visible = {}
    with open(trajectory, newline='') as stream:
        for row in csv.DictReader(stream):
            states = ast.literal_eval(row['fov_states'])
            if any(state in (1, 2) for state in states):
                visible.setdefault(int(row['frame']), set()).add(int(row['vehicle_index']))
    expected = set(range(cars))
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
