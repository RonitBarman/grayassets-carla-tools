"""PCAP sorting, RTP decoding and packet CSV extraction."""
import os

import re

import csv

import math

import subprocess

import tempfile

from concurrent.futures import ThreadPoolExecutor, as_completed

def _run(cmd, **kwargs):
    """
    Wrapper around subprocess.run.
    IMPORTANT: don't use capture_output=True when passing stdout/stderr.
    """
    return subprocess.run(cmd, text=True, **kwargs)

def _eligible_pcaps(target_dir):
    out = []
    for root, _, files in os.walk(target_dir):
        for f in files:
            if f.lower().endswith(('.pcap', '.pcapng')):
                out.append(os.path.join(root, f))
    return sorted(out)

def _rel_subdir(path, root):
    return os.path.relpath(os.path.dirname(path), root)

def _ensure_dirs(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)

def _csv_has_rows(csv_path):
    """Return True if CSV has >= 2 lines (header + at least 1 data row)."""
    if not os.path.exists(csv_path):
        return False
    try:
        with open(csv_path, 'r') as f:
            lines = [ln for ln in (next(f, ''), next(f, '')) if ln.strip()]
            return len(lines) >= 2
    except Exception:
        return False

def _tshark_dump_fields(pcap_src, csv_out, extra_fields=None, extra_args=None):
    base_fields = ['frame.number', 'frame.time_epoch', 'frame.len']
    if extra_fields:
        base_fields += list(extra_fields)
    tshark_cmd = ['tshark', '-r', pcap_src, '-T', 'fields', '-E', 'header=y', '-E', 'separator=,', '-E', 'occurrence=f', '-n']
    if extra_args:
        tshark_cmd += list(extra_args)
    for f in base_fields:
        tshark_cmd += ['-e', f]
    with open(csv_out, 'w') as f_out:
        proc = subprocess.run(tshark_cmd, text=True, stdout=f_out, stderr=subprocess.DEVNULL)
    return (proc.returncode == 0, proc.returncode)

def _add_time_relative_inplace(csv_path):
    """
    Read csv_path, compute time_relative = time_epoch - first_time_epoch,
    and write back with a new 'frame.time_relative' column at index 2.
    """
    rows = []
    with open(csv_path, newline='') as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return
    header = rows[0]
    try:
        idx_epoch = header.index('frame.time_epoch')
    except ValueError:
        return
    if 'frame.time_relative' not in header:
        insert_at = idx_epoch + 1
        header.insert(insert_at, 'frame.time_relative')
        first_epoch = None
        for r in rows[1:]:
            if idx_epoch < len(r) and r[idx_epoch].strip():
                try:
                    first_epoch = float(r[idx_epoch])
                    break
                except ValueError:
                    pass
        if first_epoch is None:
            with open(csv_path, 'w', newline='') as f:
                csv.writer(f).writerows(rows)
            return
        for i in range(1, len(rows)):
            r = rows[i]
            while len(r) < len(header) - 1:
                r.append('')
            try:
                t = float(r[idx_epoch]) if r[idx_epoch] else math.nan
                rel = t - first_epoch if not math.isnan(t) else ''
                r.insert(insert_at, f'{rel:.6f}' if rel != '' else '')
            except Exception:
                r.insert(insert_at, '')
        with open(csv_path, 'w', newline='') as f:
            csv.writer(f).writerows([header] + rows[1:])

def _process_one_pcap(pcap_path, target_dir, out_root, use_reordercap=True, extra_fields=None, extra_args=None):
    """
    Returns: "ok" | "skipped" | "tshark_error"
    """
    base = os.path.splitext(pcap_path)[0]
    relative_subdir = _rel_subdir(pcap_path, target_dir)
    output_subdir = os.path.join(out_root, relative_subdir)
    _ensure_dirs(output_subdir)
    stem = os.path.basename(base)
    csv_out = os.path.join(output_subdir, f'{stem}_pktlen.csv')
    if _csv_has_rows(csv_out):
        return 'skipped'
    pcap_sorted = pcap_path
    tmp_created = False
    if use_reordercap:
        with tempfile.NamedTemporaryFile(prefix='sorted_', suffix='.pcap', delete=False) as tf:
            pcap_sorted = tf.name
            tmp_created = True
        rc = _run(['reordercap', pcap_path, pcap_sorted], stderr=subprocess.PIPE)
        if rc.returncode != 0:
            pcap_sorted = pcap_path
            tmp_created = False
    try:
        ok, _ = _tshark_dump_fields(pcap_sorted, csv_out, extra_fields, extra_args)
        if not _csv_has_rows(csv_out):
            if pcap_sorted != pcap_path:
                ok2, _ = _tshark_dump_fields(pcap_path, csv_out, extra_fields, extra_args)
                ok = ok or ok2
        if not _csv_has_rows(csv_out):
            return 'tshark_error'
        _add_time_relative_inplace(csv_out)
        return 'ok'
    finally:
        if tmp_created and os.path.exists(pcap_sorted):
            try:
                os.remove(pcap_sorted)
            except Exception:
                pass

def extract_pkt_lengths_parallel(target_dir, workers=8, use_reordercap=True, extra_fields=None, extra_args=None, output_root=None):
    """
    NO FILTERS. Processes every frame and writes:
    frame.number, frame.time_epoch, frame.time_relative, frame.len, data.len, ip.len, udp.length, tcp.len, ...
    Output dir: <target_dir>_<workers>workers_pktlens
    """
    if not os.path.isdir(target_dir):
        print(f"Error: Directory '{target_dir}' does not exist.")
        return
    out_root = output_root or f'{target_dir}_{workers}workers_pktsize_rtpinfo_force'
    _ensure_dirs(out_root)
    files = _eligible_pcaps(target_dir)
    if not files:
        print('No .pcap files found.')
        return
    groups = [[] for _ in range(workers)]
    for idx, f in enumerate(files):
        groups[idx % workers].append(f)
    print(f'Found {len(files)} pcaps. Running with {workers} parallel workers.')
    for i, g in enumerate(groups):
        print(f'  Worker {i}: {len(g)} files')

    def worker(group_files, wid):
        results = {'ok': 0, 'skipped': 0, 'tshark_error': 0}
        for fp in group_files:
            status = _process_one_pcap(fp, target_dir, out_root, use_reordercap=use_reordercap, extra_fields=extra_fields, extra_args=extra_args)
            results[status] = results.get(status, 0) + 1
        return (wid, results)
    summaries = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(worker, group, wid) for wid, group in enumerate(groups)]
        for fut in as_completed(futs):
            summaries.append(fut.result())
    print('\nSummary per worker:')
    summaries.sort(key=lambda x: x[0])
    total = {'ok': 0, 'skipped': 0, 'tshark_error': 0}
    for wid, res in summaries:
        print(f'  Worker {wid}: {res}')
        for k, v in res.items():
            total[k] = total.get(k, 0) + v
    print('\nGrand total:', total)
    print(f"\nAll pcaps in '{target_dir}' and subfolders processed!")
