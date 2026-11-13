import os, re, time, subprocess, math, signal, shlex

from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

import tempfile

from mininet.net import Mininet

from mininet.node import Host

from mininet.link import TCLink

from mininet.topo import Topo

from mininet.cli import CLI

from mininet.log import setLogLevel

import threading

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

def stream_parallel(target_dir, workers=4, start_port=5004, encrypted=False, folder_prefix='', *, output_root=None):
    """
    Process eligible videos in `target_dir` using `workers` parallel lanes.
    Each lane gets a dedicated even RTP port: start_port, start_port+2, ...
    """
    if not os.path.isdir(target_dir):
        print(f"Error: Directory '{target_dir}' does not exist.")
        return
    packets_dir_raw = os.path.join(output_root, f'{folder_prefix}{workers}workers_packets_raw')
    packets_dir_size = os.path.join(output_root, 'no_rtpinfo', f'{folder_prefix}{workers}workers_packets_size')
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

def set_netem_conditions(avg_delay_ms=100, jitter_ms=20, loss_percent=1.0, interface='lo'):
    """
    Set network emulation conditions using `tc netem` on the specified interface.

    Args:
        avg_delay_ms (int): Average delay in milliseconds.
        jitter_ms (int): Jitter range in milliseconds (stddev of delay).
        loss_percent (float): Packet loss percentage.
        interface (str): Network interface to apply (default: 'lo').
    """
    try:
        subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'], stderr=subprocess.DEVNULL)
        cmd = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'root', 'netem', 'delay', f'{avg_delay_ms}ms', f'{jitter_ms}ms', 'distribution', 'normal', 'loss', f'{loss_percent}%']
        print(f"[tc] Applying: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print('[tc] Network emulation applied.')
    except subprocess.CalledProcessError as e:
        print(f'[tc] Error applying netem: {e}')

def clear_netem(interface='lo'):
    subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', interface, 'root'])
    print('[tc] Network emulation cleared.')

class MininetwifiNetworkEmulator:
    """
    Mininet-based network emulator for per-port bandwidth, delay, jitter, and loss control.
    """

    def __init__(self):
        self.net = None
        self.hosts = {}
        self.links = {}
        self.port_configs = {}
        self.emulation_active = False
        setLogLevel('warning')

    def _gen_ports(self, port_start: int, port_nums: int):
        """Generate even RTP ports: start, start+2, ... (length=port_nums)."""
        return [port_start + 2 * i for i in range(port_nums)]

    def _parse_bandwidth(self, bw_str):
        """Convert bandwidth string to Mbps for Mininet."""
        if isinstance(bw_str, (int, float)):
            return bw_str
        bw_str = bw_str.lower()
        if bw_str.endswith('mbit') or bw_str.endswith('mbps'):
            return float(bw_str.replace('mbit', '').replace('mbps', ''))
        elif bw_str.endswith('kbit') or bw_str.endswith('kbps'):
            return float(bw_str.replace('kbit', '').replace('kbps', '')) / 1000
        elif bw_str.endswith('gbit') or bw_str.endswith('gbps'):
            return float(bw_str.replace('gbit', '').replace('gbps', '')) * 1000
        else:
            return float(bw_str)

    def setup_network_emulation(self, port_start=5004, port_nums=3, default_rate='5mbit', per_port_rate=None, delay_ms=0, jitter_ms=0, loss_percent=0.0, per_port_netem=None):
        """
        Set up Mininet network with per-port network conditions.
        
        Args:
            port_start: Starting port number
            port_nums: Number of ports to configure
            default_rate: Default bandwidth limit
            per_port_rate: Dict of {port: rate} overrides
            delay_ms: Default delay in milliseconds
            jitter_ms: Default jitter in milliseconds  
            loss_percent: Default packet loss percentage
            per_port_netem: Dict of per-port network condition overrides
        """
        if self.emulation_active:
            self.cleanup_network_emulation()
        ports = self._gen_ports(port_start, port_nums)
        per_port_rate = per_port_rate or {}
        per_port_netem = per_port_netem or {}
        print(f'[Mininet] Setting up network emulation for ports: {ports}')

        class PerPortTopo(Topo):

            def build(self):
                switch = self.addSwitch('s1')
                for i, port in enumerate(ports):
                    host_name = f'h{port}'
                    port_netem = per_port_netem.get(port, {})
                    bw = self._parse_bandwidth(port_netem.get('rate', per_port_rate.get(port, default_rate)))
                    delay = f"{port_netem.get('delay_ms', delay_ms)}ms"
                    jitter = port_netem.get('jitter_ms', jitter_ms)
                    loss = port_netem.get('loss_percent', loss_percent)
                    host = self.addHost(host_name, ip=f'127.0.0.{i + 2}/8')
                    link_params = {'bw': bw, 'delay': delay, 'loss': loss, 'use_htb': True}
                    if jitter > 0:
                        link_params['jitter'] = f'{jitter}ms'
                    self.addLink(host, switch, **link_params)

            def _parse_bandwidth(self, bw_str):
                """Convert bandwidth string to Mbps for Mininet."""
                if isinstance(bw_str, (int, float)):
                    return bw_str
                bw_str = bw_str.lower()
                if bw_str.endswith('mbit') or bw_str.endswith('mbps'):
                    return float(bw_str.replace('mbit', '').replace('mbps', ''))
                elif bw_str.endswith('kbit') or bw_str.endswith('kbps'):
                    return float(bw_str.replace('kbit', '').replace('kbps', '')) / 1000
                elif bw_str.endswith('gbit') or bw_str.endswith('gbps'):
                    return float(bw_str.replace('gbit', '').replace('gbps', '')) * 1000
                else:
                    return float(bw_str)
        topo = PerPortTopo()
        self.net = Mininet(topo=topo, link=TCLink, autoSetMacs=True)
        self.net.start()
        self.port_configs = {port: {'rate': per_port_netem.get(port, {}).get('rate', per_port_rate.get(port, default_rate)), 'delay_ms': per_port_netem.get(port, {}).get('delay_ms', delay_ms), 'jitter_ms': per_port_netem.get(port, {}).get('jitter_ms', jitter_ms), 'loss_percent': per_port_netem.get(port, {}).get('loss_percent', loss_percent)} for port in ports}
        self._apply_port_specific_rules(ports)
        self.emulation_active = True
        print(f'[Mininet] Network emulation configured for {len(ports)} ports')
        return ports

    def _apply_port_specific_rules(self, ports):
        """Apply port-specific traffic control rules using tc on the loopback interface."""
        try:
            subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', 'lo', 'root'], stderr=subprocess.DEVNULL, check=False)
            subprocess.run(['sudo', 'tc', 'qdisc', 'add', 'dev', 'lo', 'root', 'handle', '1:', 'htb', 'default', '999'], check=True)
            subprocess.run(['sudo', 'tc', 'class', 'add', 'dev', 'lo', 'parent', '1:', 'classid', '1:999', 'htb', 'rate', '1000mbit', 'ceil', '1000mbit'], check=True)
            for port in ports:
                config = self.port_configs[port]
                subprocess.run(['sudo', 'tc', 'class', 'add', 'dev', 'lo', 'parent', '1:', 'classid', f'1:{port}', 'htb', 'rate', str(config['rate']), 'ceil', str(config['rate'])], check=True)
                if config['delay_ms'] or config['jitter_ms'] or config['loss_percent']:
                    netem_cmd = ['sudo', 'tc', 'qdisc', 'add', 'dev', 'lo', 'parent', f'1:{port}', 'handle', f'{port}:', 'netem']
                    if config['delay_ms']:
                        netem_cmd.extend(['delay', f"{config['delay_ms']}ms"])
                        if config['jitter_ms']:
                            netem_cmd.extend([f"{config['jitter_ms']}ms", 'distribution', 'normal'])
                    if config['loss_percent']:
                        netem_cmd.extend(['loss', f"{config['loss_percent']}%"])
                    subprocess.run(netem_cmd, check=True)
                else:
                    subprocess.run(['sudo', 'tc', 'qdisc', 'add', 'dev', 'lo', 'parent', f'1:{port}', 'handle', f'{port}:', 'fq_codel'], check=True)
                subprocess.run(['sudo', 'tc', 'filter', 'add', 'dev', 'lo', 'protocol', 'ip', 'parent', '1:', 'prio', '1', 'flower', 'ip_proto', 'udp', 'dst_port', str(port), 'action', 'pass', 'classid', f'1:{port}'], check=True)
            print(f'[Mininet] Applied port-specific tc rules for ports: {ports}')
        except subprocess.CalledProcessError as e:
            print(f'[Mininet] Error applying tc rules: {e}')

    def cleanup_network_emulation(self):
        """Clean up Mininet network and tc rules."""
        if self.net:
            print('[Mininet] Stopping network emulation...')
            self.net.stop()
            self.net = None
        try:
            subprocess.run(['sudo', 'tc', 'qdisc', 'del', 'dev', 'lo', 'root'], stderr=subprocess.DEVNULL, check=False)
            print('[Mininet] Cleared tc rules on loopback interface')
        except Exception as e:
            print(f'[Mininet] Warning: Could not clear tc rules: {e}')
        self.emulation_active = False
        self.port_configs = {}
        print('[Mininet] Network emulation cleanup complete')

_network_emulator = MininetwifiNetworkEmulator()

def _signal_handler(signum, frame):
    """Handle interrupt signals to ensure proper cleanup."""
    print(f'\n[Signal] Received signal {signum}, cleaning up...')
    global _network_emulator
    _network_emulator.cleanup_network_emulation()
    exit(0)

def clear_per_port_caps(interface='lo'):
    """Clear network emulation using Mininet."""
    global _network_emulator
    _network_emulator.cleanup_network_emulation()
    print(f'[Mininet] Cleared network emulation on {interface}')

def set_per_port_caps(interface='lo', port_start=5004, port_nums=3, default_rate='5mbit', per_port_rate=None, delay_ms=0, jitter_ms=0, loss_percent=0.0, per_port_netem=None, default_class_rate='100000mbit'):
    """
    Create per-port bandwidth caps using Mininet network emulation.
    
    Args:
        interface: Network interface (maintained for compatibility)
        port_start: Starting port number
        port_nums: Number of ports to configure
        default_rate: Default bandwidth limit for each port
        per_port_rate: Dict of {port: rate} overrides
        delay_ms: Default delay in milliseconds
        jitter_ms: Default jitter in milliseconds
        loss_percent: Default packet loss percentage
        per_port_netem: Dict of per-port network condition overrides
        default_class_rate: Unused in Mininet implementation
    
    Returns:
        List of configured ports
    """
    global _network_emulator
    return _network_emulator.setup_network_emulation(port_start=port_start, port_nums=port_nums, default_rate=default_rate, per_port_rate=per_port_rate, delay_ms=delay_ms, jitter_ms=jitter_ms, loss_percent=loss_percent, per_port_netem=per_port_netem)
