"""RTP/SRTP replay and loopback packet capture."""
import os
import re
import time
import subprocess
import signal
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

def _eligible_mp4s(target_dir):
    pat = re.compile('video_camera\\d+\\.mp4$')
    out = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f.endswith('.mp4') and pat.match(f):
                out.append(os.path.join(root, f))
    return sorted(out)

def _rel_subdir(path, root):
    return os.path.relpath(os.path.dirname(path), root)

def _ensure_dirs(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)

def _run(cmd, **kw):
    defaults = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'check': False}
    defaults.update(kw)
    return subprocess.run(cmd, **defaults)

def extract_sorted_by_time(pcap_in, port, csv_out):
    with tempfile.NamedTemporaryFile(prefix='sorted_', suffix='.pcap', delete=False) as tf:
        pcap_sorted = tf.name
    try:
        rc = _run(['reordercap', pcap_in, pcap_sorted])
        if rc.returncode != 0:
            print('[tshark] Warning: reordercap failed; falling back to unsorted pcap.')
            pcap_sorted = pcap_in
        tshark_cmd = ['tshark', '-r', pcap_sorted, '-Y', f'ip.dst==127.0.0.1 && udp.dstport=={port}', '-T', 'fields', '-e', 'frame.time_relative', '-e', 'frame.len', '-e', 'data.len', '-E', 'header=y', '-E', 'separator=,', '-n']
        with open(csv_out, 'w') as f:
            _run(tshark_cmd, stdout=f)
    finally:
        try:
            if pcap_sorted != pcap_in and os.path.exists(pcap_sorted):
                os.remove(pcap_sorted)
        except Exception:
            pass

def _process_one(file_path, target_dir, packets_dir_raw, packets_dir_size, port, encrypted=False):
    base_name = os.path.splitext(file_path)[0]
    rel = _rel_subdir(file_path, target_dir)
    out_raw_dir = os.path.join(packets_dir_raw, rel)
    out_size_dir = os.path.join(packets_dir_size, rel)
    _ensure_dirs(out_raw_dir, out_size_dir)
    filename_stem = os.path.basename(base_name)
    pcap_file_raw = os.path.join(out_raw_dir, f'{filename_stem}_pkts_1x.pcap')
    csv_file_size = os.path.join(out_size_dir, f'{filename_stem}_pktsize_1x.csv')
    if os.path.exists(pcap_file_raw) and os.path.exists(csv_file_size):
        print(f'[port {port}] Skipping existing: {file_path}')
        return 'skipped'
    tcpdump_cmd = ['sudo', 'tcpdump', '-i', 'lo', '-n', '-U', '-w', pcap_file_raw, 'udp', 'dst', 'port', str(port), 'and', 'dst', 'host', '127.0.0.1']
    tcp = subprocess.Popen(tcpdump_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print(f'[port {port}] tcpdump PID {tcp.pid} → {pcap_file_raw}')
    try:
        time.sleep(1)
        if not encrypted:
            ffmpeg_cmd = ['ffmpeg', '-nostdin', '-re', '-i', file_path, '-an', '-vcodec', 'copy', '-f', 'rtp', f'rtp://127.0.0.1:{port}']
        else:
            SRTP_B64 = 'w0bqf9t3x2Z3Rk2C0zq1yCw0v2c3m8y0m3q2r1s0t9u8v7w6x5y4z3=='
            ffmpeg_cmd = ['ffmpeg', '-nostdin', '-re', '-i', file_path, '-an', '-vcodec', 'copy', '-f', 'rtp', f'srtp://127.0.0.1:{port}&srtp_out_suite=AES_CM_128_HMAC_SHA1_80&srtp_out_params={SRTP_B64}']
        rc = _run(ffmpeg_cmd)
        if rc.returncode != 0:
            print(f'[port {port}] FFmpeg failed: {file_path}')
            return 'ffmpeg_error'
        time.sleep(1)
    finally:
        try:
            os.killpg(tcp.pid, signal.SIGINT)
            tcp.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(tcp.pid, signal.SIGKILL)
            tcp.wait(timeout=1)
    time.sleep(1)
    extract_sorted_by_time(pcap_file_raw, port, csv_file_size)
    print(f'[port {port}] Done: {file_path} → {csv_file_size}')
    return 'ok'

def stream_parallel(target_dir, subfolder='', workers=4, start_port=5004, encrypted=False, folder_prefix='', output_root=None):
    """
    Process eligible videos in `target_dir` using `workers` parallel lanes.
    Each lane gets a dedicated even RTP port: start_port, start_port+2, ...
    """
    if not os.path.isdir(target_dir):
        print(f"Error: Directory '{target_dir}' does not exist.")
        return
    packets_dir_raw = os.path.join(output_root, f'{folder_prefix}{workers}workers_packets_raw') if output_root else f'{target_dir}/../{subfolder}/{folder_prefix}{workers}workers_packets_raw'
    packets_dir_size = os.path.join(output_root, 'no_rtpinfo', f'{folder_prefix}{workers}workers_packets_size') if output_root else f'{target_dir}/../{subfolder}/no_rtpinfo/{folder_prefix}{workers}workers_packets_size'
    _ensure_dirs(packets_dir_raw, packets_dir_size)
    files = _eligible_mp4s(target_dir)
    if not files:
        print('No matching files found.')
        return
    ports = [start_port + 2 * i for i in range(workers)]
    groups = [[] for _ in range(workers)]
    for idx, f in enumerate(files):
        groups[idx % workers].append(f)
    print(f'Found {len(files)} files. Running with {workers} parallel workers.')
    for i, (p, g) in enumerate(zip(ports, groups)):
        print(f'  Worker {i}: port {p}, {len(g)} files')

    def worker(port, group_files):
        results = {'ok': 0, 'skipped': 0, 'ffmpeg_error': 0}
        for fp in group_files:
            status = _process_one(fp, target_dir, packets_dir_raw, packets_dir_size, port, encrypted=encrypted)
            results[status] = results.get(status, 0) + 1
        return (port, results)
    summaries = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, port, group) for port, group in zip(ports, groups)]
        for fut in as_completed(futs):
            summaries.append(fut.result())
    print('\nSummary per port:')
    for port, res in sorted(summaries):
        print(f'  {port}: {res}')
