"""Legacy yaw-only boxes and geometric frustum classification."""
import math
import numpy as np

def rot_z_deg(yaw_deg: float) -> np.ndarray:
    """Yaw-only rotation (vehicle local -> world)."""
    y = math.radians(yaw_deg)
    return np.array([[math.cos(y), -math.sin(y), 0.0], [math.sin(y), math.cos(y), 0.0], [0.0, 0.0, 1.0]], dtype=float)

def _rot_ue_from_euler(pitch_deg, yaw_deg, roll_deg):
    """Unreal/Carla convention: yaw about Z, pitch about Y, roll about X (degrees).
       Returns R_wc (world <- camera)."""
    p = math.radians(pitch_deg)
    y = math.radians(yaw_deg)
    r = math.radians(roll_deg)
    Rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    Ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    Rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx

def _corner_rays_from_hfov(hfov_deg, W, H):
    """Corner direction vectors in camera coords (forward +X, right +Y, up +Z)."""
    a = math.tan(math.radians(hfov_deg) / 2.0)
    vfov = 2.0 * math.atan(a * (H / W))
    b = math.tan(vfov / 2.0)
    rays = [np.array([1, -a, b], float), np.array([1, a, b], float), np.array([1, a, -b], float), np.array([1, -a, -b], float)]
    rays = [r / np.linalg.norm(r) for r in rays]
    return rays

def _plane_from_rays(r1, r2, center_dir=np.array([1.0, 0.0, 0.0])):
    """Plane through origin spanned by rays r1,r2. Returns normal n oriented so n·center_dir >= 0."""
    n = np.cross(r1, r2)
    if np.dot(n, center_dir) < 0:
        n = -n
    return n / (np.linalg.norm(n) + 1e-12)

def build_camera_frustum(cam_pos_xyz, pitch_deg, yaw_deg, roll_deg, hfov_deg, width_px, height_px, near_clip=0.001, far_clip=np.inf):
    """
    Returns a dict with:
      C (3,), R_cw (3x3), planes (list of 4 normals in cam coords: left,right,top,bottom),
      near_clip, far_clip.
    """
    R_wc = _rot_ue_from_euler(-pitch_deg, yaw_deg, roll_deg)
    R_cw = R_wc.T
    C = np.array(cam_pos_xyz, float)
    tl, tr, br, bl = _corner_rays_from_hfov(hfov_deg, width_px, height_px)
    n_left = _plane_from_rays(tl, bl)
    n_right = _plane_from_rays(br, tr)
    n_top = _plane_from_rays(tr, tl)
    n_bottom = _plane_from_rays(bl, br)
    return {'C': C, 'R_cw': R_cw, 'planes': [n_left, n_right, n_top, n_bottom], 'near': float(near_clip), 'far': float(far_clip)}

def classify_bbox_vs_frustum(world_corners, cam_model):
    """
    Returns state: 0 (out), 1 (partial), 2 (fully in).
    cam_model: dict from build_camera_frustum.
    """
    C = cam_model['C']
    Rcw = cam_model['R_cw']
    planes = cam_model['planes']
    nearv = cam_model['near']
    farv = cam_model['far']
    Xc = (Rcw @ (world_corners - C).T).T
    x = Xc[:, 0]
    side_inside = []
    for n in planes:
        side_inside.append(Xc @ n >= 0.0)
    side_inside = np.stack(side_inside, axis=1)
    near_inside = x >= nearv
    far_inside = x <= farv if np.isfinite(farv) else np.ones_like(x, dtype=bool)
    all_inside = side_inside.all(axis=1) & near_inside & far_inside
    if all_inside.all():
        return 2
    for k in range(side_inside.shape[1]):
        if (~side_inside[:, k]).all():
            return 0
    if (~near_inside).all():
        return 0
    if np.isfinite(farv) and (~far_inside).all():
        return 0
    return 1

BRAND_DB = {0: {'name': 'Mercedes Sprinter', 'offset': np.array([0.0105, -0.0041, 1.2882], float), 'dims': np.array([5.915, 1.988, 2.561], float)}, 1: {'name': 'Nissan Patrol', 'offset': np.array([0.0573, -0.0001, 0.9351], float), 'dims': np.array([4.605, 1.932, 1.855], float)}, 2: {'name': 'Tesla Model3', 'offset': np.array([-0.0292, 0.0, 0.7359], float), 'dims': np.array([4.792, 2.163, 1.488], float)}}

BRAND_ID_MAP = {'vehicle.mercedes.sprinter': 0, 'vehicle.nissan.patrol': 1, 'vehicle.tesla.model3': 2}

def get_brand_id(vehicle):
    """
    Given a CARLA vehicle actor, return the brand_id (0/1/2) that
    matches the BRAND_DB spec.
    """
    bp_id = vehicle.type_id
    return BRAND_ID_MAP.get(bp_id, None)

def vehicle_bbox_corners_world(pos_xyz, yaw_deg, brand_id):
    """
    Build the 8 OBB corners for the vehicle bounding box in WORLD coordinates.
    pos_xyz: vehicle Transform.location (actor origin) in world (meters)
    yaw_deg: vehicle yaw (deg). If you have per-timestep yaw, pass that.
    brand_id: 0/1/2 (Sprinter/Patrol/Model3)
    """
    spec = BRAND_DB[int(brand_id)]
    L, W, H = spec['dims']
    off = spec['offset']
    Rv = rot_z_deg(yaw_deg)
    center_world = np.asarray(pos_xyz, float) + Rv @ off
    hx, hy, hz = (L / 2.0, W / 2.0, H / 2.0)
    local_corners = np.array([[sx, sy, sz] for sx in (+hx, -hx) for sy in (+hy, -hy) for sz in (+hz, -hz)], float)
    world_corners = (Rv @ local_corners.T).T + center_world
    return world_corners
