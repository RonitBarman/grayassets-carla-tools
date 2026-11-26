"""Step 1: edit the settings below, then run `python 1_generate_videos.py`."""
from helpers.pipeline import ROOT, resolve

# ── Generation settings ──────────────────────────────────────────────────────
MODE = 'separate'                 # separate sample clips or one continuous recording
DRIVING_MODE = 'agent'    # fixed_velocity or agent (straight/turning routes)
NUM_SAMPLES = 3                    # per seed AND lighting time
SEEDS = [10]
TIMES = ['12:00']                   # use [None] for fixed default lighting
VEHICLE_TYPES = ['vehicle.tesla.model3']
COLORS = ['White', 'Black', 'Gray']
COLOR_MAP = {'White': '255,255,255', 'Black': '0,0,0', 'Gray': '128,128,128'}
DIRECTIONS = ['up', 'down', 'left', 'right']
SPEED_RANGE = (5, 20)               # m/s
LANES = [0] # 0, 1 - only for fixed_velocity mode
LANE_RANDOM_OFFSET = False
LANE_OFFSET_RANGE = 0.5
DISABLE_TRAFFIC_LIGHTS = True
DISABLE_WEATHER = True
GRID_VIDEO = False

# Camera positions are (x, y, z) in meters; rotations are (pitch, yaw, roll)
# in degrees. Each camera independently chooses a position, then a rotation.
# With variation disabled, the first option in each list is used.
VARIED_CAMERA = True
CAMERA_CONFIGS = [
    {'positions': [(-60, 24, 10), (-61, 24, 10), (-59, 24, 10)],
     'rotations': [(-60, 0, 0), (-57, -3, 0), (-63, 3, 0)]},
    {'positions': [(-40, 24, 10), (-41, 24, 10), (-39, 24, 10)],
     'rotations': [(-60, 180, 0), (-57, 177, 0), (-63, 183, 0)]},
    {'positions': [(-50, 30, 10), (-50, 29, 10), (-50, 31, 10)],
     'rotations': [(-60, 270, 0), (-57, 267, 0), (-63, 273, 0)]},
    {'positions': [(-50, 10, 10), (-50, 9, 10), (-50, 11, 10)],
     'rotations': [(-60, 90, 0), (-57, 87, 0), (-63, 93, 0)]},
]

# Scene content is independent of recording and driving mode.
CARS_PER_SAMPLE = 3                # any positive integer; feasibility/hardware determine limits
DELTA_SECONDS = 1.0                # minimum gap between entrances AND between exits
MIN_OVERLAP_SECONDS = 2.0          # continuous time ALL cars are in camera coverage

# CARLA stays outside this repository. Set START_SERVER=False to use a running server.
CARLA_ROOT = ROOT.parent / 'grayassets_carla'
START_SERVER = True
HOST = 'localhost'
PORT = 2000
TOWN = 'Town10HD_Opt'

# Agent settings (used when DRIVING_MODE is agent).
MOVEMENTS = ['straight_lane0', 'straight_lane1', 'turn_right', 'turn_left']
AGENT_MODE = 'behavioragent'        # behavioragent or basicagent
BEHAVIOR = 'normal'
MAX_DURATION = 60                  # maximum seconds per agent after its spawn
ARRIVAL_RADIUS = 1.0               # meters
# Pedestrians work with either driving mode and any number of vehicles.
PEDESTRIAN_COUNT = 0               # 0 disables them; try 20 to add seeded walkers

# Continuous mode records NUM_SAMPLES sequential groups in one shared clip.
# Each group contains CARS_PER_SAMPLE cars and satisfies the same constraints.
BUFFER_DURATIONS = [1]             # seconds between groups (at least DELTA_SECONDS)

# Leave this automatic, or give the experiment a fixed name to reuse its outputs.
EXPERIMENT_NAME = (
    f'{MODE}_{DRIVING_MODE}'
    + (f'_{AGENT_MODE}_{BEHAVIOR}' if DRIVING_MODE == 'agent' else '')
    + f'_{CARS_PER_SAMPLE}cars_{PEDESTRIAN_COUNT}pedestrians'
    + f'_delta{DELTA_SECONDS:g}_overlap{MIN_OVERLAP_SECONDS:g}_{NUM_SAMPLES}samples'
    + ('_variedcamera' if VARIED_CAMERA else '')
)
OUTPUT_ROOT = ROOT / 'output'
DATASET_DIR = resolve(OUTPUT_ROOT) / EXPERIMENT_NAME


def main():
    from helpers.generation import generate
    print(f'Dataset: {DATASET_DIR}')
    print(f'{MODE}: {NUM_SAMPLES} samples × {len(SEEDS)} seeds × {len(TIMES)} lighting times')
    generate(globals())


if __name__ == '__main__':
    main()
