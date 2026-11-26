# grayassets-carla-tools

## Run the pipeline

Use your existing CARLA environment and run the steps in order:

```bash
conda activate carla
cd /home/rbarman/CARLA_work_2026/grayassets-carla-tools
python 1_generate_videos.py
python 2_copy_ground_truth.py
python 3_extract_frame_sizes.py
python 4_stream_videos.py
python 5_extract_packet_fields.py
python 6_add_delay_jitter.py
```

The supplied step 1 settings generate **three multi-vehicle samples**, each with
three cars and four camera videos: 12 videos total. Change `NUM_SAMPLES`, `SEEDS`, `TIMES`,
vehicle types, colors, directions and speed range directly in `1_generate_videos.py`.
`NUM_SAMPLES` is per seed/time pair. Step 1 starts CARLA by default; set
`START_SERVER = False` to use a running simulator. `CARLA_ROOT` points to the
original sibling installation. Fixed-velocity capture uses the server's current
map; use Town10HD_Opt for the supplied intersection coordinates.

## What each script does

| Script | Settings you edit | Result |
| --- | --- | --- |
| `1_generate_videos.py` | Scenario, seed, lighting, sample count, generation mode | Four camera videos and trajectory CSV per scenario, or continuous recordings |
| `2_copy_ground_truth.py` | Usually nothing | Recursive CSV copy into `<dataset>_GroundTruth` |
| `3_extract_frame_sizes.py` | Worker count, camera filename filter | Headerless frame-size/type CSVs under `<dataset>_<workers>workers_framesizes` |
| `4_stream_videos.py` | Bandwidths, delay, jitter, loss, RTP/SRTP, worker count | PCAP and packet-size CSVs in settings-named experiment folders |
| `5_extract_packet_fields.py` | Extraction worker count, sorting | Packet/RTP CSVs under `<capture>_<workers>workers_pktsize_rtpinfo_force` |
| `6_add_delay_jitter.py` | Synthetic delay, jitter, seed | Transformed CSVs under `<input>_added_delay<delay>_jitter<jitter>` |

Shared implementations live in `helpers/`, grouped by purpose. They are local
helper files, not an installable application. Run the numbered scripts directly;
there is no `pip install -e .` step.

## Automatic folders and handoff between steps

With the supplied settings, step 1 writes to:

```text
output/separate_5samples/
  seed1/sun_at_12:00/
    index0000_vehicle.tesla.model3_.../
      video_camera1.mp4 ... video_camera4.mp4
      vehicle_trajectory.csv
    index0001_.../
    ...
```

Leave `INPUT_DIR = None` in subsequent steps. They read the earlier scripts'
settings automatically; they never execute earlier steps when reading settings.
They recursively process all scenario subdirectories. Paths are relative to the
repository even when a script is launched from another working directory.

Step 4 derives both its location and prefix from the dataset and network settings:

```text
output/separate_5samples_packet_transmission/
  bw50mbit_delay0_jitter0_loss0_64workers_packets_raw/
  no_rtpinfo/bw50mbit_delay0_jitter0_loss0_64workers_packets_size/
```

Step 5 automatically finds those captures and uses step 4's port/worker settings
for RTP decoding. Step 6 automatically finds step 5's CSV outputs. Multiple values
in `BANDWIDTH_LIMITS` are handled by all three steps. There is no stream-root or
prefix to type, and no need to reproduce the generated folder names in another
file. Set `INPUT_DIR` only when processing data from a different experiment.

Configure the experiment **before** running the steps. If you change step 1's
experiment name/sample count or step 4's network settings afterward, later steps
will follow the new settings, not search for an older run. Use `EXPERIMENT_NAME`
to choose a fixed dataset name when desired. Existing scenario folders are skipped
as in the original; a fresh experiment name starts a fresh dataset.

## Generation and network variants

Set `MODE` in step 1:

- `'separate'`: one fixed-velocity vehicle per scenario.
- `'multi_vehicle'`: 1–3 fixed-velocity cars in each sample, recorded together by
  the four cameras. `CARS_PER_SAMPLE` sets the count and `DELTA_SECONDS` staggers
  their entrances and exits. See the example below.
- `'continuous'`: one long recording per seed/time, with scheduled traffic;
  `WITH_FOLLOWERS` and `WITH_OPPOSITE` control overlapping traffic. The sample
  count is the number of primary events; additional vehicles can be scheduled.
- `'agent'`: waypoint routes with `AGENT_MODE` set to `'behavioragent'` or
  `'basicagent'`; four approaches and straight/turning movements.
- `'pedestrians'`: agent mode with seeded walkers and controllers; road crossing
  and the vehicle's pedestrian-avoidance manager follow the original behavior.

For example, edit these settings in `1_generate_videos.py`, then run the same
six numbered scripts:

```python
MODE = 'multi_vehicle'
CARS_PER_SAMPLE = 3
DELTA_SECONDS = 1.0
MIN_OVERLAP_SECONDS = 2.0
NUM_SAMPLES = 5
```

This creates five samples, each containing three scheduled cars and four shared
camera MP4s. There are never more than three scheduled cars in one sample.
Delta is a **minimum time gap in seconds**, rounded up to whole simulation frames:
both spawn times and despawn times are separated by at least delta. When vehicles
have different travel durations, a later start may be delayed further to keep
the exits staggered too. Different approach directions are chosen where possible.
The gap controls actor spawn/despawn events; individual camera FOV entry/exit
times also depend on the direction and speed of each car.

`MIN_OVERLAP_SECONDS` requires a continuous interval where **every car is inside
at least one of the four camera FOVs**, possibly different views for different
cars. Merely being spawned outside camera coverage does not count. Partial FOV
inclusion counts as visible, using the same geometric FOV labels as ground truth;
these labels do not measure occlusion. The minimum is rounded up to whole frames.
Set it to `0` to disable this constraint and use the original stagger-only plan.

The planner checks projected camera coverage, tries vehicle entrance orders, and
resamples infeasible vehicle choices before recording (at most 100 plans per
sample). It preserves the sampled speeds/travel durations rather than keeping
cars spawned after their routes solely to inflate overlap. If no plan fits, it
raises an error explaining that the minimum, delta or speed range must change.
Large deltas and short/fast routes can make an overlap request infeasible.
Coverage planning currently uses the existing box models for Tesla Model 3,
Nissan Patrol and Mercedes Sprinter.

After recording, actual FOV labels verify the longest continuous shared-visible
interval. If it is too short, generation fails explicitly; the sample is retained
for inspection and is not silently accepted or skipped on rerun. Use a fresh
experiment name to retry a failed sample. Changing the minimum automatically
changes the default experiment and sample folder names, preserving older outputs.

Multi-vehicle sample folders contain `video_camera1.mp4` through
`video_camera4.mp4`, `vehicle_schedule.csv`, `all_vehicle_trajectories.csv`, and
`visibility_summary.csv`.
The schedule records each car's parameters and exact spawn/despawn frames/times.
With overlap enabled, it also records predicted visible-frame bounds. The summary
records the requested minimum, measured overlap bounds/duration, and pass/fail.
Trajectories have one row per active car per frame, with a stable `vehicle_index`
for joining the schedule, CARLA actor ID, position/velocity, rotation and four
FOV states. Multi-vehicle lifecycle/control changes occur before the rendering
tick, and camera frame IDs are matched to that tick so labels describe the encoded
scene. The final frame includes all scheduled despawns. Folder names include the
car count, delta and minimum overlap; downstream paths follow automatically.

For the optional network backends, replace step 4 with
`4_stream_videos_mininet.py` (wired Mininet) or `4_stream_videos_wifi.py`
(Mininet-WiFi). Change `STREAM_SCRIPT` at the top of step 5 to the file you ran;
step 6 follows automatically. Each backend keeps its own Python settings and
implementation. WiFi settings include WPA2/WPA3, propagation and monitor capture.
Encrypted monitor traffic will generally have blank IP/RTP fields.

## Dependencies and behavior

Use CARLA 0.9.15, NumPy, pandas, pvlib and tqdm from the existing CARLA environment.
Agent mode also uses the navigation agents under the external installation's
`PythonAPI/carla`. ffmpeg, ffprobe, tcpdump, tshark, reordercap and Linux tc must be
available. Optional backends need Mininet or Mininet-WiFi and their system tools.
The network steps require suitable privileges; for Mininet and WiFi, run with sudo
and the full path to the environment's Python interpreter.

The capture, encoding, CSV schemas and timestamp calculations retain the original
algorithms: four 1280×720 cameras at 30 FPS/120° FOV, BGR24 H.264 ultrafast/GOP 10,
even RTP ports beginning at 5004, original packet-field columns, per-file relative
time and uniform synthetic jitter with legacy ordering correction. Step 6 removes
`frame.time_epoch` and formats timestamps to nine decimals; it changes CSVs only.

Network cleanup now runs in `finally`. Managed generation stops only its owned
server and refuses to start over an occupied port. The continuous recorder creates
its output directory before opening encoders, and the WiFi sniffer reads its
propagation setting from the emulator instance. These are small execution fixes;
they do not change the data contracts.
