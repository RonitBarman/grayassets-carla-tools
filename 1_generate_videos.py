"""Step 1: edit the settings below, then run `python 1_generate_videos.py`."""
from helpers.pipeline import ROOT, resolve

# ── Generation settings ──────────────────────────────────────────────────────
MODE = 'separate'                  # separate, continuous, agent, pedestrians
NUM_SAMPLES = 5                    # per seed AND lighting time
SEEDS = [1]
TIMES = ['12:00']                   # use [None] for fixed default lighting
VEHICLE_TYPES = ['vehicle.tesla.model3']
COLORS = ['White', 'Black', 'Gray']
COLOR_MAP = {'White': '255,255,255', 'Black': '0,0,0', 'Gray': '128,128,128'}
DIRECTIONS = ['up', 'down', 'left', 'right']
SPEED_RANGE = (5, 20)               # m/s
LANES = [0]
LANE_RANDOM_OFFSET = False
LANE_OFFSET_RANGE = 0.5
DISABLE_TRAFFIC_LIGHTS = True
DISABLE_WEATHER = True
GRID_VIDEO = False

# CARLA stays outside this repository. Set START_SERVER=False to use a running server.
CARLA_ROOT = ROOT.parent / 'grayassets_carla'
START_SERVER = True
HOST = 'localhost'
PORT = 2000
TOWN = 'Town10HD_Opt'

# Agent / pedestrian settings (used only by those modes).
MOVEMENTS = ['straight_lane0', 'straight_lane1', 'turn_right', 'turn_left']
AGENT_MODE = 'behavioragent'        # behavioragent or basicagent
BEHAVIOR = 'normal'
MAX_DURATION = 60                  # seconds; agent capture can finish early
ARRIVAL_RADIUS = 1.0               # meters
PEDESTRIAN_COUNT = 20              # pedestrians mode only; seed = SEED + sample index

# Continuous mode records one long set of videos per seed/time combination.
WITH_FOLLOWERS = True
WITH_OPPOSITE = True
BUFFER_DURATIONS = [1]

# Leave this automatic, or give the experiment a fixed name to reuse its outputs.
EXPERIMENT_NAME = f'{MODE}_{NUM_SAMPLES}samples'
OUTPUT_ROOT = ROOT / 'output'
DATASET_DIR = resolve(OUTPUT_ROOT) / EXPERIMENT_NAME


def main():
    from helpers.generation import generate
    print(f'Dataset: {DATASET_DIR}')
    print(f'{MODE}: {NUM_SAMPLES} samples × {len(SEEDS)} seeds × {len(TIMES)} lighting times')
    generate(globals())


if __name__ == '__main__':
    main()
