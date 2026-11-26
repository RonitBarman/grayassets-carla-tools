# grayassets-carla-tools

Generate CARLA traffic videos and trajectories, replay videos over RTP/SRTP,
capture packets, and extract frame sizes and packet timing data.

## Requirements

- CARLA 0.9.15 and its Python API. Agent driving also needs the navigation agents
  in `<CARLA_ROOT>/PythonAPI/carla`.
- Python with NumPy, pandas, pvlib and tqdm. The `carla` Conda environment uses
  Python 3.8.20.
- FFmpeg/ffprobe, tcpdump, tshark, reordercap and Linux `tc`.
- Sudo access for packet capture and network shaping. Mininet and Mininet-WiFi
  backends also require their respective system dependencies.

## Run the pipeline

Edit settings at the top of each numbered script, then run:

```bash
conda activate carla
cd /path/to/grayassets-carla-tools
python 1_generate_videos.py
python 2_copy_ground_truth.py
python 3_extract_frame_sizes.py
python 4_stream_videos.py
python 5_extract_packet_fields.py
python 6_add_delay_jitter.py
```

| Step | Output |
| --- | --- |
| 1. Generate videos | Four camera MP4s, trajectories, poses and schedules per recording |
| 2. Copy ground truth | All CSVs copied recursively to `<dataset>_GroundTruth` |
| 3. Extract frame sizes | Headerless `pkt_size,pict_type` rows in `<dataset>_<workers>workers_framesizes` |
| 4. Replay and capture | PCAPs and packet-size CSVs under `<dataset>_packet_transmission` |
| 5. Extract packet fields | CSVs under `<capture>_<workers>workers_pktsize_rtpinfo_force` |
| 6. Add delay/jitter | CSVs under `<input>_added_delay<delay>_jitter<jitter>` |

Leave `INPUT_DIR = None` to follow the preceding steps' settings automatically.
Set it to a path to process a different dataset. Paths are relative to the
repository, regardless of the shell's working directory. Shared code lives in
`helpers/`; run the scripts directly without installing a package.

Choose settings before starting: later steps use the scripts' current settings.
`EXPERIMENT_NAME` builds the dataset folder name automatically, or accepts a name
you supply. Outputs preserve scenario subdirectories. Verified recordings with
matching settings are skipped on reruns. Use a fresh experiment name after
changing settings or to retry an incomplete recording.

## Generation settings

Edit `1_generate_videos.py`. Defaults produce three separate samples per
seed/lighting pair, each with three fixed-velocity cars and no pedestrians.

| Setting | Meaning |
| --- | --- |
| `MODE` | `separate` creates individual clips; `continuous` records sequential vehicle groups in one clip |
| `NUM_SAMPLES` | Clips or groups per seed/lighting pair |
| `DRIVING_MODE` | `fixed_velocity` for straight paths, or `agent` for navigation |
| `AGENT_MODE` / `BEHAVIOR` | `behavioragent` or `basicagent`; BehaviorAgent style |
| `CARS_PER_SAMPLE` | Cars per clip/group; any positive integer |
| `PEDESTRIAN_COUNT` | Requested walkers; `0` disables them |
| `MOVEMENTS` | Agent routes: `straight_lane0`, `straight_lane1`, `turn_left`, `turn_right` |
| `DELTA_SECONDS` | Minimum gap between vehicle spawns and between removals |
| `MIN_OVERLAP_SECONDS` | Minimum continuous time every car in a group is within camera coverage; `0` disables the constraint |
| `BUFFER_DURATIONS` | Gap choices between continuous groups, each enforced to be at least delta |

Recording mode, driving mode, car count and pedestrians can be combined freely.
For three turning cars with walkers, for example:

```python
DRIVING_MODE = 'agent'
AGENT_MODE = 'behavioragent'
CARS_PER_SAMPLE = 3
PEDESTRIAN_COUNT = 20
MOVEMENTS = ['turn_left', 'turn_right']
```

`SEEDS`, `TIMES`, vehicle types/colors, `DIRECTIONS` and `SPEED_RANGE` control
scenario selection. `LANES` and lane offsets apply to fixed-velocity driving;
agent lanes follow `MOVEMENTS`. Use one approach direction for following traffic,
or `['up', 'down']` / `['left', 'right']` for opposing traffic.

Set `CARLA_ROOT` to the simulator installation. Step 1 starts and stops its own
server by default; set `START_SERVER = False` to use a running server. `TOWN`
selects the map; the supplied routes use `Town10HD_Opt`.

Agents finish at their destination or planner completion. Removals remain
staggered when cars finish together. `MAX_DURATION` limits driving time per car;
a timeout fails the recording. Continuous groups wait for the preceding group
to finish. Walkers have road crossing disabled. BehaviorAgent's pedestrian
avoidance is disabled; vehicle avoidance remains active. Spawned walker counts
are reported in the visibility summary.

### Camera poses

`CAMERA_CONFIGS` contains position and rotation choices for each of four cameras:

```python
{'positions': [(-60, 24, 10), (-61, 24, 10), (-59, 24, 10)],
 'rotations': [(-60, 0, 0), (-57, -3, 0), (-63, 3, 0)]}
```

Positions are `(x, y, z)` in meters; rotations are `(pitch, yaw, roll)` in degrees.
Each list can have any nonzero number of options. Defaults include three
positions and three rotations per camera.

- `VARIED_CAMERA = True`: each camera independently chooses a position, then a
  rotation, for each recording. Choices use the scenario seed.
- `VARIED_CAMERA = False` (default): use the first position and rotation.

Poses stay fixed throughout each recording. Variation adds `_variedcamera` to
the automatic experiment name. Videos use 1280×720 resolution, 30 FPS, 120° FOV,
and H.264 encoding with BGR24 input, the ultrafast preset and GOP 10.

### Visibility and feasibility

Overlap planning and FOV labels use the selected camera poses. Every car must
be in at least one view during the required overlap; partial coverage counts.
Labels measure geometric coverage without accounting for occlusion.

Fixed-velocity planning uses bounding-box models for Model 3, Patrol and Sprinter.
Agent planning first observes solo routes to estimate timing. The planner tries
up to 100 scenes per group. Geometry, timing and congestion determine feasible
car counts. Recorded timing and overlap are checked afterward; failed recordings
raise an error and retain their outputs.

## Recording files

| File | Contents |
| --- | --- |
| `video_camera1.mp4` … `video_camera4.mp4` | Camera recordings; `GRID_VIDEO` also enables a combined view |
| `camera_poses.csv` | Camera index, world position and rotation |
| `vehicle_schedule.csv` | Vehicle/route settings, group index, planned and actual spawn/removal times |
| `all_vehicle_trajectories.csv` | One row per active car per frame: IDs, position, velocity, rotation and four FOV states |
| `visibility_summary.csv` | Required/measured overlap per group, pass/fail and pedestrian counts |
| `recording_complete.json` | Settings and completion metadata for resuming |

`vehicle_index` identifies a car within a recording; `group_index` identifies its
continuous group. Trajectory frame numbers match video frames.

## Packet processing

Step 3 excludes grid videos; `CAMERA_ONLY` optionally restricts filenames further.
Step 4 replays videos in real time with stream copy over RTP or SRTP. Workers use
even UDP ports starting at 5004. Defaults enable 50 Mbit/s per-port bandwidth
shaping. Edit bandwidth, delay, jitter, loss and worker settings in that script.
Steps 4–6 process every entry in `BANDWIDTH_LIMITS`.

Step 5 optionally sorts captures with reordercap and forces RTP decoding on the
configured worker ports. Its columns are `frame.number`, `frame.time_epoch`,
`frame.time_relative`, `frame.len`, `rtp.seq`, `rtp.timestamp`, `rtp.marker`.
Relative time starts at the first usable epoch in each capture.

Step 6 adds fixed delay plus uniform jitter in `[-JITTER_MS/2, +JITTER_MS/2]`,
converted to seconds. If a candidate timestamp is at or before the preceding
result, it uses that result plus the input inter-packet interval. It removes
`frame.time_epoch` and formats timestamps to nine decimal places. This changes
CSV data only.

For wired Mininet or Mininet-WiFi, replace step 4 with
`4_stream_videos_mininet.py` or `4_stream_videos_wifi.py` and set `STREAM_SCRIPT`
in step 5 accordingly. Run these backends with sudo and the environment's full
Python path. WiFi supports WPA2/WPA3 and monitor capture; encrypted traffic may
have blank IP/RTP fields.
