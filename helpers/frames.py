"""Per-video ffprobe frame extraction."""
import os

import re

import subprocess

from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

def _eligible_mp4s(target_dir, use_camera_regex=False):
    """
    Find .mp4s under target_dir.
    - If use_camera_regex=True, keep only files matching r"video_camera\\d+\\.mp4$"
    - Always exclude files ending with "_gridview.mp4"
    """
    cam_pat = re.compile('video_camera\\d+\\.mp4$')
    out = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if not f.endswith('.mp4'):
                continue
            if f.endswith('_gridview.mp4'):
                continue
            if use_camera_regex and (not cam_pat.match(f)):
                continue
            out.append(os.path.join(root, f))
    return sorted(out)

def _rel_subdir(path, root):
    return os.path.relpath(os.path.dirname(path), root)

def _ensure_dirs(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)

def _process_one_ffprobe(video_path, target_dir, framesizes_root):
    """
    For a single MP4:
      - Compute output CSV path under <target_dir>_framesizes/<relative_subdir>/
      - Skip if output exists (non-empty)
      - Run ffprobe to extract pkt_size,pict_type for v:0
    Returns one of: "ok", "skipped", "ffprobe_error"
    """
    base_name = os.path.splitext(video_path)[0]
    relative_subdir = _rel_subdir(video_path, target_dir)
    output_subdir = os.path.join(framesizes_root, relative_subdir)
    _ensure_dirs(output_subdir)
    filename_stem = os.path.basename(base_name)
    output_file = os.path.join(output_subdir, f'{filename_stem}_frame_typesize.csv')
    if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
        return 'skipped'
    try:
        result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'frame=pkt_size,pict_type', '-of', 'csv=p=0', video_path], capture_output=True, text=True, check=True)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        with open(output_file, 'w') as f:
            f.write('\n'.join(lines))
        print(f'  Processed: {output_file}')
        return 'ok'
    except subprocess.CalledProcessError as e:
        print(f'Error (ffprobe) on {video_path}: {e}')
        try:
            if os.path.exists(output_file) and os.path.getsize(output_file) == 0:
                os.remove(output_file)
        except Exception:
            pass
        return 'ffprobe_error'

def extract_frame_typesize_parallel(target_dir, workers=8, use_camera_regex=False, output_root=None):
    """
    Parallel version of frame type/size extraction using ThreadPoolExecutor.
    - Mirrors your RTP/SRTP script’s worker/port grouping style (round-robin assignment).
    - Creates sibling output directory: <target_dir>_framesizes
    - Skips existing non-empty outputs.
    - Summarizes results per worker.
    """
    if not os.path.isdir(target_dir):
        print(f"Error: Directory '{target_dir}' does not exist.")
        return
    framesizes_dir = output_root or f'{target_dir}_{workers}workers_framesizes'
    _ensure_dirs(framesizes_dir)
    files = _eligible_mp4s(target_dir, use_camera_regex=use_camera_regex)
    if not files:
        print('No matching files found.')
        return
    groups = [[] for _ in range(workers)]
    for idx, f in enumerate(files):
        groups[idx % workers].append(f)
    print(f'Found {len(files)} files. Running with {workers} parallel workers.')
    for i, g in enumerate(groups):
        print(f'  Worker {i}: {len(g)} files')

    def worker(group_files, wid):
        results = {'ok': 0, 'skipped': 0, 'ffprobe_error': 0}
        for fp in group_files:
            status = _process_one_ffprobe(fp, target_dir, framesizes_dir)
            results[status] = results.get(status, 0) + 1
        return (wid, results)
    summaries = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, group, wid) for wid, group in enumerate(groups)]
        for fut in as_completed(futs):
            summaries.append(fut.result())
    print('\nSummary per worker:')
    summaries.sort(key=lambda x: x[0])
    total = {'ok': 0, 'skipped': 0, 'ffprobe_error': 0}
    for wid, res in summaries:
        print(f'  Worker {wid}: {res}')
        for k, v in res.items():
            total[k] = total.get(k, 0) + v
    print('\nGrand total:', total)
    print(f"\nAll .mp4 files in '{target_dir}' and subfolders processed!")
