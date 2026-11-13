from mn_wifi.net import Mininet_wifi
from mn_wifi.node import Station, AP
from mn_wifi.link import wmediumd, adhoc
from mn_wifi.wmediumdConnector import interference
from mn_wifi.topo import Topo
from mn_wifi.cli import CLI
from mininet.log import setLogLevel

import os, re, time, subprocess, math, signal, shlex, sys

from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path

import tempfile

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

def extract_sorted_by_time(pcap_in, port, csv_out, dst_ip='127.0.0.1'):
    """
    Extract packet data from pcap, sorted by timestamp.

    Args:
        pcap_in: Input pcap file
        port: UDP destination port to filter
        csv_out: Output CSV file
        dst_ip: Destination IP address to filter (default: 127.0.0.1)
    """
    with tempfile.NamedTemporaryFile(prefix='sorted_', suffix='.pcap', delete=False) as tf:
        pcap_sorted = tf.name
    try:
        rc = _run(['reordercap', pcap_in, pcap_sorted])
        if rc.returncode != 0:
            print('[tshark] Warning: reordercap failed; falling back to unsorted pcap.')
            pcap_sorted = pcap_in
        tshark_cmd = ['tshark', '-r', pcap_sorted, '-Y', f'ip.dst=={dst_ip} && udp.dstport=={port}', '-T', 'fields', '-e', 'frame.time_relative', '-e', 'frame.len', '-e', 'data.len', '-E', 'header=y', '-E', 'separator=,', '-n']
        with open(csv_out, 'w') as f:
            _run(tshark_cmd, stdout=f)
    finally:
        try:
            if pcap_sorted != pcap_in and os.path.exists(pcap_sorted):
                os.remove(pcap_sorted)
        except Exception:
            pass

def extract_802_11_frames(pcap_in, port, csv_out, dst_ip='10.0.0.1', wlan_da=None, wlan_sa=None):
    """
    Extract 802.11 frame data from monitor mode capture, sorted by timestamp.
    This extracts encrypted 802.11 data frames showing the full frame size including encryption overhead.

    Args:
        pcap_in: Input pcap file (captured in monitor mode with radiotap headers)
        port: UDP destination port to filter (used to filter by IP layer inside 802.11)
        csv_out: Output CSV file
        dst_ip: Destination IP address to filter
        wlan_da: Destination MAC address to filter
        wlan_sa: Source MAC address to filter
    """
    with tempfile.NamedTemporaryFile(prefix='sorted_', suffix='.pcap', delete=False) as tf:
        pcap_sorted = tf.name
    try:
        rc = _run(['reordercap', pcap_in, pcap_sorted])
        if rc.returncode != 0:
            print('[tshark] Warning: reordercap failed; falling back to unsorted pcap.')
            pcap_sorted = pcap_in
        base_filter = 'wlan.fc.type == 2'
        if wlan_da:
            base_filter += f' && wlan.da == {wlan_da}'
        if wlan_sa:
            base_filter += f' && wlan.sa == {wlan_sa}'
        tshark_cmd = ['tshark', '-r', pcap_sorted, '-Y', base_filter, '-T', 'fields', '-e', 'frame.time_relative', '-e', 'frame.len', '-E', 'header=y', '-E', 'separator=,', '-n']
        print(f'[tshark] Extracting encrypted 802.11 data frames from {pcap_sorted}')
        with open(csv_out, 'w') as f:
            _run(tshark_cmd, stdout=f)
    finally:
        try:
            if pcap_sorted != pcap_in and os.path.exists(pcap_sorted):
                os.remove(pcap_sorted)
        except Exception:
            pass

def _process_one(file_path, target_dir, packets_dir_raw, packets_dir_size, port, SRTP_encrypted_flag=False, network_emulator=None, receiver_ip='127.0.0.1', use_wifi=False):
    """
    Process one video file: stream and capture packets.

    Args:
        file_path: Path to video file
        target_dir: Target directory
        packets_dir_raw: Output directory for raw packets
        packets_dir_size: Output directory for packet size CSVs
        port: Port number for streaming
        encrypted: Whether to use SRTP encryption
        network_emulator: MininetwifiNetworkEmulator instance (for WiFi mode)
        receiver_ip: IP address of receiver (10.0.0.1 for WiFi, 127.0.0.1 for loopback)
        use_wifi: Whether to use WiFi network (True) or loopback (False)
    """
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
    if use_wifi and network_emulator and network_emulator.emulation_active:
        receiver_sta = network_emulator.receiver_station
        sender_config = network_emulator.port_configs.get(port)
        if not sender_config:
            print(f'[port {port}] Error: No sender station configured for this port')
            return 'config_error'
        sender_sta = sender_config['station_obj']
        use_monitor = network_emulator.capture_monitor_mode and network_emulator.monitor_interface
        if use_monitor:
            receiver_intf = network_emulator.monitor_interface
            print(f'\n[port {port}] Using sniffer station monitor interface: {receiver_intf} (captures over-the-air 802.11 frames)')
            capture_on_station = False
        else:
            print(f'[port {port}] Using standard mode (captures decrypted packets inside receiver station)')
            result = receiver_sta.cmd('ip link show')
            import re
            match = re.search('(\\w+-wlan\\d+):', result)
            if match:
                receiver_intf = match.group(1)
                print(f'[port {port}] Using wireless interface: {receiver_intf} (captures decrypted packets)')
            else:
                match = re.search('(\\S+-eth\\d+)(?:@\\S+)?:', result)
                if match:
                    receiver_intf = match.group(1)
                    print(f'[port {port}] Using ethernet interface: {receiver_intf} (simple host mode)')
                else:
                    print(f'[port {port}] Error: Could not find network interface on receiver')
                    print(f'[port {port}] Available interfaces: {result}')
                    return 'interface_error'
            capture_on_station = True
        if use_monitor:
            print(f'[port {port}] Using sniffer station monitor interface: {receiver_intf}')
            sniffer_sta = network_emulator.sniffer_station
            if not sniffer_sta:
                print(f'[port {port}] ⚠️  No sniffer station available, falling back to standard capture')
                use_monitor = False
                capture_on_station = True
                receiver_intf = 'sta0-wlan0' if 'sta0-wlan0' in network_emulator.receiver_station.cmd('ip link show') else 'wlan0'
            else:
                tcpdump_cmd = f'tcpdump -i {receiver_intf} -n -U -w {pcap_file_raw} -y IEEE802_11_RADIO >/tmp/snf1.tcpdump.log 2>&1'
                print(f'\n[port {port}] Starting monitor capture on sniffer station → {pcap_file_raw}')
                print(f'[port {port}] tcpdump command: {tcpdump_cmd}')
                sniffer_sta.cmd(f'{tcpdump_cmd} &')
                time.sleep(2)
                tcpdump_check = sniffer_sta.cmd('pgrep -f tcpdump')
                if tcpdump_check.strip():
                    print(f'[port {port}] ✅ tcpdump is running on sniffer station (PID {tcpdump_check.strip()})')
                else:
                    print(f'[port {port}] ⚠️  tcpdump may not be running on sniffer station')
        else:
            tcpdump_cmd = f"tcpdump -i {receiver_intf} -n -U -w {pcap_file_raw} 'udp dst port {port} and dst host {receiver_ip}'"
            print(f'[port {port}] Starting tcpdump on receiver {receiver_intf} → {pcap_file_raw}')
            receiver_sta.cmd(f'{tcpdump_cmd} &')
            time.sleep(2)
        try:
            if not SRTP_encrypted_flag:
                print(f'[port {port}] Streaming with RTP')
                ffmpeg_cmd = f'ffmpeg -nostdin -re -i {file_path} -an -vcodec copy -f rtp rtp://{receiver_ip}:{port}'
            else:
                print(f'[port {port}] Streaming with SRTP')
                SRTP_B64 = 'w0bqf9t3x2Z3Rk2C0zq1yCw0v2c3m8y0m3q2r1s0t9u8v7w6x5y4z3=='
                ffmpeg_cmd = f"ffmpeg -nostdin -re -i {file_path} -an -vcodec copy -f rtp 'srtp://{receiver_ip}:{port}?srtp_out_suite=AES_CM_128_HMAC_SHA1_80&srtp_out_params={SRTP_B64}'"
            print(f"[port {port}] Streaming from {sender_config['station']} to {receiver_ip}:{port}")
            result = sender_sta.cmd(ffmpeg_cmd)
            time.sleep(1)
        finally:
            print(f'[port {port}] Stopping tcpdump...')
            if use_monitor:
                sniffer_sta = network_emulator.sniffer_station
                if sniffer_sta:
                    sniffer_sta.cmd('killall tcpdump 2>/dev/null')
                    print(f'[port {port}] ✅ Stopped tcpdump on sniffer station')
            else:
                receiver_sta.cmd('killall tcpdump 2>/dev/null')
            time.sleep(2)
    else:
        tcpdump_cmd = ['sudo', 'tcpdump', '-i', 'lo', '-n', '-U', '-w', pcap_file_raw, 'udp', 'dst', 'port', str(port), 'and', 'dst', 'host', '127.0.0.1']
        tcp = subprocess.Popen(tcpdump_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        print(f'[port {port}] tcpdump PID {tcp.pid} → {pcap_file_raw}')
        try:
            time.sleep(1)
            if not SRTP_encrypted_flag:
                print(f'[port {port}] Streaming with RTP')
                ffmpeg_cmd = ['ffmpeg', '-nostdin', '-re', '-i', file_path, '-an', '-vcodec', 'copy', '-f', 'rtp', f'rtp://127.0.0.1:{port}']
            else:
                print(f'[port {port}] Streaming with SRTP')
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
    if use_wifi and network_emulator and network_emulator.capture_monitor_mode and network_emulator.monitor_interface:
        print(f'[port {port}] Extracting 802.11 frames from monitor mode capture')
        try:
            sender_mac = sender_config.get('mac') if sender_config else None
        except Exception:
            sender_mac = None
        extract_802_11_frames(pcap_file_raw, port, csv_file_size, dst_ip=receiver_ip, wlan_sa=sender_mac)
        print(f'[port {port}] Done: {file_path} → {csv_file_size} (802.11 frames)')
    else:
        print(f'[port {port}] Extracting IP packets from standard mode capture')
        extract_sorted_by_time(pcap_file_raw, port, csv_file_size, dst_ip=receiver_ip)
        print(f'[port {port}] Done: {file_path} → {csv_file_size}')
    return 'ok'

def stream_parallel(target_dir, workers=4, start_port=5004, SRTP_encrypted_flag=False, folder_prefix='', network_emulator=None, use_wifi=False, *, output_root=None):
    """
    Process eligible videos in `target_dir` using `workers` parallel lanes.
    Each lane gets a dedicated even RTP port: start_port, start_port+2, ...

    Args:
        target_dir: Directory containing video files
        workers: Number of parallel workers
        start_port: Starting port number
        encrypted: Whether to use SRTP encryption
        folder_prefix: Prefix for output folders
        network_emulator: MininetwifiNetworkEmulator instance (for WiFi mode)
        use_wifi: Whether to use WiFi network (True) or loopback (False)
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
    if use_wifi and network_emulator and network_emulator.emulation_active:
        receiver_ip = network_emulator.receiver_ip
        print(f'[WiFi Mode] Receiver IP: {receiver_ip}')
    else:
        receiver_ip = '127.0.0.1'
        print(f'[Loopback Mode] Receiver IP: {receiver_ip}')
    print(f'Found {len(files)} files. Running with {workers} parallel workers.')
    for i, (p, g) in enumerate(zip(ports, groups)):
        print(f'  Worker {i}: port {p}, {len(g)} files')

    def worker(port, group_files):
        results = {'ok': 0, 'skipped': 0, 'ffmpeg_error': 0, 'config_error': 0, 'interface_error': 0}
        for fp in group_files:
            status = _process_one(fp, target_dir, packets_dir_raw, packets_dir_size, port, SRTP_encrypted_flag=SRTP_encrypted_flag, network_emulator=network_emulator, receiver_ip=receiver_ip, use_wifi=use_wifi)
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
    Mininet-WiFi-based network emulator with REAL wireless stations, APs, propagation models, and encryption.
    """

    def __init__(self):
        self.net = None
        self.stations = {}
        self.aps = {}
        self.port_configs = {}
        self.emulation_active = False
        self.encryption_config = None
        self.receiver_station = None
        self.receiver_ip = None
        self.monitor_interface = None
        self.capture_monitor_mode = False
        self.sniffer_station = None

    def _gen_ports(self, port_start: int, port_nums: int):
        """Generate even RTP ports: start, start+2, ... (length=port_nums)."""
        return [port_start + 2 * i for i in range(port_nums)]

    def _parse_bandwidth(self, bw_str):
        """Convert bandwidth string to Mbps for Mininet-WiFi."""
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

    def setup_network_emulation(self, port_start=5004, port_nums=3, default_rate='5mbit', per_port_rate=None, delay_ms=0, jitter_ms=0, loss_percent=0.0, per_port_netem=None, wifi_encrypted_flag=False, encryption_type='none', encryption_key='mininetwifi12345678', propagation_model='logDistance', path_loss_exp=3.5, tx_power=20, distance_base=10, use_wmediumd=False, capture_monitor_mode=False, ap_position=(10, 10, 0), receiver_position=(10, 10, 0), sniffer_position=(10, 10, 0), station_positions=None):
        """
        Set up REAL Mininet-WiFi network with wireless stations, APs, propagation models, and encryption.

        Args:
            port_start: Starting port number
            port_nums: Number of ports/stations to configure
            default_rate: Default bandwidth limit
            per_port_rate: Dict of {port: rate} overrides
            delay_ms: Default delay in milliseconds
            jitter_ms: Default jitter in milliseconds
            loss_percent: Default packet loss percentage
            per_port_netem: Dict of per-port network condition overrides
            wifi_encrypted_flag: Enable WPA2/WPA3 link-layer encryption (requires AP mode)
            encryption_type: WiFi encryption type ('none', 'wpa2', 'wpa3')
            encryption_key: WiFi encryption key/passphrase (min 8 chars for WPA)
            propagation_model: Propagation model ('logDistance', 'ITU', 'friis')
            path_loss_exp: Path loss exponent for logDistance model
            tx_power: Transmission power in dBm
            distance_base: Base distance for station positioning (meters)
            use_wmediumd: Whether to use wmediumd for realistic propagation (requires wmediumd installed)
            capture_monitor_mode: Capture 802.11 frames in monitor mode (shows encryption overhead)
            ap_position: Position of Access Point as (x, y, z) tuple in meters
            receiver_position: Position of receiver station in ad-hoc mode as (x, y, z) tuple
            sniffer_position: Position of sniffer station as (x, y, z) tuple in meters
            station_positions: List of (x, y, z) tuples for sender station positions (None = auto circle arrangement)
        """
        if self.emulation_active:
            self.cleanup_network_emulation()
        ports = self._gen_ports(port_start, port_nums)
        per_port_rate = per_port_rate or {}
        per_port_netem = per_port_netem or {}
        self.capture_monitor_mode = capture_monitor_mode
        self.use_wmediumd = use_wmediumd
        use_ap_mode = wifi_encrypted_flag and encryption_type != 'none'
        if capture_monitor_mode and (not use_ap_mode):
            print(f'[Mininet-WiFi] ⚠️  Warning: capture_monitor_mode requires AP mode with encryption')
            print(f'[Mininet-WiFi] ⚠️  Setting capture_monitor_mode=False')
            self.capture_monitor_mode = False
            capture_monitor_mode = False
        print(f'[Mininet-WiFi] Setting up REAL WiFi network emulation')
        print(f'[Mininet-WiFi] Ports: {ports}')
        print(f"[Mininet-WiFi] Network mode: {('AP with encryption' if use_ap_mode else 'Ad-hoc (IBSS)')}")
        if use_ap_mode:
            print(f'[Mininet-WiFi] Link-layer encryption: {encryption_type.upper()}')
            if capture_monitor_mode:
                print(f'[Mininet-WiFi] Capture mode: Monitor (captures over-the-air 802.11 frames)')
            else:
                print(f'[Mininet-WiFi] Capture mode: Standard (decrypted IP packets)')
        print(f'[Mininet-WiFi] Use wmediumd: {use_wmediumd}')
        if use_wmediumd:
            print(f'[Mininet-WiFi] Propagation model: {propagation_model} (exp={path_loss_exp})')
        from mn_wifi.net import Mininet_wifi
        from mn_wifi.node import Station, OVSKernelAP
        from mininet.node import Host
        from mininet.link import TCLink
        self.encryption_config = {'type': encryption_type, 'key': encryption_key}
        print(f'[Mininet-WiFi] Creating WiFi network with {port_nums} sender(s) + 1 receiver' + (f' + 1 AP' if use_ap_mode else '') + '...')
        try:
            if use_wmediumd:
                from mn_wifi.link import wmediumd
                from mn_wifi.wmediumdConnector import interference
                print(f'[Mininet-WiFi] Initializing Mininet_wifi with wmediumd and interference model...')
                self.net = Mininet_wifi(link=wmediumd, wmediumd_mode=interference)
                print(f'[Mininet-WiFi] ✅ Mininet_wifi initialized with wmediumd')
            else:
                print(f'[Mininet-WiFi] Initializing Mininet_wifi WITHOUT wmediumd (simpler mode)...')
                self.net = Mininet_wifi()
                print(f'[Mininet-WiFi] ✅ Mininet_wifi initialized')
            if use_ap_mode:
                print(f'[Mininet-WiFi] Creating WiFi network with AP mode and {encryption_type.upper()} encryption...')
            else:
                print(f'[Mininet-WiFi] Creating WiFi stations in ad-hoc mode (no encryption)...')
            distance_base_val = distance_base

            def _norm3(pos):
                if isinstance(pos, (list, tuple)):
                    if len(pos) == 1:
                        return _norm3(pos[0])
                    if len(pos) >= 3:
                        return (float(pos[0]), float(pos[1]), float(pos[2]))
                    if len(pos) == 2:
                        return (float(pos[0]), float(pos[1]), 0.0)
                if isinstance(pos, (str, bytes)):
                    parts = [p.strip() for p in str(pos).split(',')]
                    nums = [float(p) for p in parts if p != '']
                    if len(nums) >= 3:
                        return (nums[0], nums[1], nums[2])
                    if len(nums) == 2:
                        return (nums[0], nums[1], 0.0)
                return (10.0, 10.0, 0.0)
            ap_x, ap_y, ap_z = _norm3(ap_position)
            rx_x, rx_y, rx_z = _norm3(receiver_position)
            if use_ap_mode:
                print(f'[Mininet-WiFi] Creating Access Point ap1 at position ({ap_x},{ap_y},{ap_z})')
                ap_params = {'ssid': 'mininet-wifi-encrypted', 'mode': 'g', 'channel': '1', 'position': f'{ap_x},{ap_y},{ap_z}', 'range': 100}
                if encryption_type == 'wpa2':
                    ap_params.update({'encrypt': 'wpa2', 'passwd': encryption_key})
                    print(f'[Mininet-WiFi]   Configuring AP with WPA2 encryption')
                elif encryption_type == 'wpa3':
                    ap_params.update({'encrypt': 'wpa3', 'passwd': encryption_key})
                    print(f'[Mininet-WiFi]   Configuring AP with WPA3 encryption')
                ap1 = self.net.addAccessPoint('ap1', **ap_params)
                self.aps['ap1'] = ap1
                print(f'[Mininet-WiFi] Creating receiver as wired host h0 at IP 10.0.0.1')
                print(f'[Mininet-WiFi]   (Wired connection to AP - no WiFi traffic from receiver)')
                h0 = self.net.addHost('h0', ip='10.0.0.1/24', mac='00:00:00:00:00:01')
                self.net.addLink(h0, ap1, cls=TCLink)
                self.receiver_station = h0
                self.receiver_ip = '10.0.0.1'
                print(f'[Mininet-WiFi] ✅ Wired receiver h0 connected to AP via TCLink')
            else:
                print(f'[Mininet-WiFi] Creating receiver station sta0 at IP 10.0.0.1, position ({rx_x},{rx_y},{rx_z})')
                sta0 = self.net.addStation('sta0', ip='10.0.0.1/24', mac='00:00:00:00:00:01', position=f'{rx_x},{rx_y},{rx_z}', range=50, mode='g', channel=1)
                self.stations['sta0'] = sta0
                self.receiver_station = sta0
                self.receiver_ip = '10.0.0.1'
            for i, port in enumerate(ports):
                sta_name = f'sta{i + 1}'
                sta_ip = f'10.0.0.{i + 2}/24'
                sta_mac = f'00:00:00:00:00:{i + 2:02x}'
                if station_positions and i < len(station_positions):
                    x, y, z = _norm3(station_positions[i])
                    cx, cy = (ap_x, ap_y) if use_ap_mode else (rx_x, rx_y)
                    distance = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
                else:
                    center_x, center_y = (ap_x, ap_y) if use_ap_mode else (rx_x, rx_y)
                    distance = distance_base_val + i * 5
                    angle = i * 360.0 / len(ports)
                    x = center_x + distance * math.cos(math.radians(angle))
                    y = center_y + distance * math.sin(math.radians(angle))
                    z = 0
                print(f'[Mininet-WiFi] Creating sender station {sta_name} at IP {sta_ip}, position ({x:.1f},{y:.1f},{z}), distance ~{distance:.1f}m')
                if use_ap_mode:
                    sta_params = {'ip': sta_ip, 'mac': sta_mac, 'position': f'{x},{y},{z}', 'range': 50}
                    if encryption_type in ['wpa2', 'wpa3']:
                        sta_params['passwd'] = encryption_key
                        sta_params['encrypt'] = encryption_type
                    sta = self.net.addStation(sta_name, **sta_params)
                else:
                    sta = self.net.addStation(sta_name, ip=sta_ip, mac=sta_mac, position=f'{x},{y},{z}', range=50, mode='g', channel=1)
                self.stations[sta_name] = sta
                self.port_configs[port] = {'rate': per_port_netem.get(port, {}).get('rate', per_port_rate.get(port, default_rate)), 'delay_ms': per_port_netem.get(port, {}).get('delay_ms', delay_ms), 'jitter_ms': per_port_netem.get(port, {}).get('jitter_ms', jitter_ms), 'loss_percent': per_port_netem.get(port, {}).get('loss_percent', loss_percent), 'station': sta_name, 'station_obj': sta, 'mac': sta_mac, 'ip': sta_ip.split('/')[0], 'position': (x, y, z), 'distance_from_receiver': distance}
            print(f'[Mininet-WiFi] All stations created with positions and wireless parameters')
            if capture_monitor_mode and use_ap_mode:
                snf_x, snf_y, snf_z = _norm3(sniffer_position)
                print(f'[Mininet-WiFi] Adding sniffer station for monitor mode capture at position ({snf_x},{snf_y},{snf_z})...')
                self._add_sniffer_station(sniffer_position=f'{snf_x},{snf_y},{snf_z}', sniffer_ip='10.0.0.250/24', encryption_type=encryption_type, encryption_key=encryption_key)
            if use_wmediumd:
                print(f'[Mininet-WiFi] Configuring propagation model: {propagation_model}')
                if propagation_model == 'logDistance':
                    self.net.setPropagationModel(model='logDistance', exp=path_loss_exp)
                elif propagation_model == 'ITU':
                    self.net.setPropagationModel(model='ITU', nFloors=1, lF=0)
                elif propagation_model == 'friis':
                    self.net.setPropagationModel(model='friis')
                else:
                    self.net.setPropagationModel(model='logDistance', exp=path_loss_exp)
                print(f'[Mininet-WiFi] ✅ Propagation model configured')
            print(f"[Mininet-WiFi] Configuring WiFi network with mininet-wifi's wireless management...")
            try:
                print(f'[Mininet-WiFi] Configuring wireless nodes...')
                self.net.configureWifiNodes()
                print(f'[Mininet-WiFi] ✅ Wireless nodes configured')
                print(f'[Mininet-WiFi] Building network (creates namespaces)...')
                self.net.build()
                print(f'[Mininet-WiFi] Starting network...')
                self.net.start()
                print(f'[Mininet-WiFi] ✅ Network started')
                if use_ap_mode:
                    try:
                        ap1 = self.net.get('ap1')
                        if ap1:
                            print(f'[Mininet-WiFi] Configuring OVS bridge on AP...')
                            ap1.cmd('ovs-vsctl add-port ap1 ap1-wlan1 2>&1')
                            ap1.cmd('ovs-ofctl del-flows ap1')
                            ap1.cmd('ovs-ofctl add-flow ap1 action=normal')
                            print(f"[Mininet-WiFi] ✅ Added ap1-wlan1 to OVS bridge 'ap1'")
                            print(f'[Mininet-WiFi] ✅ OVS bridge set to normal (MAC learning) mode')
                            print(f'[Mininet-WiFi] ✅ AP now bridges wired (ap1-eth1) and wireless (ap1-wlan1)')
                    except Exception as e:
                        print(f'[Mininet-WiFi] ⚠️  Warning during AP bridge config: {e}')
                        import traceback
                        traceback.print_exc()
                        pass
                print(f'[Mininet-WiFi] Waiting for wireless interfaces to initialize...')
                time.sleep(2)
                print(f'[Mininet-WiFi] Verifying wireless interfaces...')
                for sta_name, sta_obj in self.stations.items():
                    result = sta_obj.cmd('ip link show')
                    if f'{sta_name}-wlan0' in result or 'wlan0' in result:
                        print(f'[Mininet-WiFi] ✅ {sta_name} has wireless interface')
                    else:
                        print(f'[Mininet-WiFi] ⚠️  {sta_name} missing wireless interface')
                        print(f'[Mininet-WiFi] Available interfaces: {result}')
                if use_ap_mode:
                    print(f'[Mininet-WiFi] Waiting for stations to associate with encrypted AP...')
                    time.sleep(5)
                    print(f'[Mininet-WiFi] Verifying AP associations and encryption status...')
                    for sta_name, sta_obj in self.stations.items():
                        result = sta_obj.cmd('ip link show')
                        wlan_intf = None
                        if f'{sta_name}-wlan0' in result:
                            wlan_intf = f'{sta_name}-wlan0'
                        elif 'wlan0' in result:
                            wlan_intf = 'wlan0'
                        if wlan_intf:
                            link_status = sta_obj.cmd(f'iw dev {wlan_intf} link')
                            if 'Connected to' in link_status or 'mininet-wifi-encrypted' in link_status:
                                print(f'[Mininet-WiFi] ✅ {sta_name} associated with AP')
                                if encryption_type in ['wpa2', 'wpa3']:
                                    station_info = sta_obj.cmd(f'iw dev {wlan_intf} link')
                                    print(f'[Mininet-WiFi]    Link details: {station_info.strip()[:100]}...')
                                    wpa_status = sta_obj.cmd('wpa_cli status 2>/dev/null')
                                    if 'wpa_state=COMPLETED' in wpa_status:
                                        print(f'[Mininet-WiFi]    🔒 WPA handshake completed for {sta_name}')
                                        if 'key_mgmt=WPA2-PSK' in wpa_status:
                                            print(f'[Mininet-WiFi]    🔒 Encryption: WPA2-PSK active')
                                        elif 'key_mgmt=SAE' in wpa_status:
                                            print(f'[Mininet-WiFi]    🔒 Encryption: WPA3-SAE active')
                            else:
                                print(f'[Mininet-WiFi] ⚠️  {sta_name} not associated: {link_status.strip()}')
                else:
                    print(f'[Mininet-WiFi] Configuring stations for adhoc (IBSS) network...')
                    adhoc_ssid = 'adhoc-net'
                    adhoc_freq = '2412'
                    for sta_name, sta_obj in self.stations.items():
                        result = sta_obj.cmd('ip link show')
                        wlan_intf = None
                        if f'{sta_name}-wlan0' in result:
                            wlan_intf = f'{sta_name}-wlan0'
                        elif 'wlan0' in result:
                            wlan_intf = 'wlan0'
                        if wlan_intf:
                            print(f'[Mininet-WiFi] Configuring {sta_name}/{wlan_intf} for adhoc mode...')
                            sta_obj.cmd(f'ip link set {wlan_intf} down')
                            result = sta_obj.cmd(f'iw dev {wlan_intf} set type ibss')
                            if result:
                                print(f'[Mininet-WiFi]   Set type result: {result.strip()}')
                            sta_obj.cmd(f'ip link set {wlan_intf} up')
                            time.sleep(0.5)
                            result = sta_obj.cmd(f'iw dev {wlan_intf} ibss join {adhoc_ssid} {adhoc_freq}')
                            if result and result.strip():
                                print(f'[Mininet-WiFi]   Join result: {result.strip()}')
                            else:
                                print(f"[Mininet-WiFi]   ✅ Joined adhoc network '{adhoc_ssid}'")
                            link_status = sta_obj.cmd(f'iw dev {wlan_intf} link')
                            if 'IBSS' in link_status or adhoc_ssid in link_status:
                                print(f'[Mininet-WiFi] ✅ {sta_name} in adhoc mode')
                            else:
                                print(f'[Mininet-WiFi] ⚠️  {sta_name} adhoc status: {link_status.strip()}')
                        else:
                            print(f'[Mininet-WiFi] ⚠️  No wireless interface found for {sta_name}')
                    print(f'[Mininet-WiFi] Waiting for adhoc network to stabilize...')
                    time.sleep(3)
                self._apply_station_tc_rules()
                if capture_monitor_mode and use_ap_mode:
                    self._configure_sniffer_monitor_mode()
                print(f'[Mininet-WiFi] Testing wireless connectivity...')
                time.sleep(2)
                for port, config in self.port_configs.items():
                    sta_obj = config['station_obj']
                    sta_name = config['station']
                    success = False
                    for attempt in range(3):
                        result = sta_obj.cmd(f'ping -c 1 -W 2 {self.receiver_ip}')
                        if '1 received' in result or '1 packets transmitted, 1 received' in result:
                            print(f'[Mininet-WiFi] ✅ {sta_name} can reach receiver at {self.receiver_ip}')
                            success = True
                            break
                        time.sleep(1)
                    if not success:
                        print(f'[Mininet-WiFi] ⚠️  {sta_name} cannot reach receiver (tried 3 times)')
                print(f'\n[Mininet-WiFi] 📋 Network Node MAC Addresses:')
                print(f"[Mininet-WiFi] {'=' * 60}")
                if use_ap_mode and self.aps:
                    for ap_name, ap_obj in self.aps.items():
                        result = ap_obj.cmd('ip link show')
                        import re
                        mac_match = re.search(f'{ap_name}-wlan\\d+:.*?\\n\\s+link/ether ([0-9a-f:]+)', result, re.IGNORECASE | re.DOTALL)
                        if mac_match:
                            mac = mac_match.group(1)
                            print(f'[Mininet-WiFi]   {ap_name:8s} (Access Point) → MAC: {mac}')
                if use_ap_mode:
                    result = self.receiver_station.cmd('ip link show')
                    import re
                    mac_match = re.search('h0-eth\\d+(?:@\\S+)?:.*?\\n\\s+link/ether ([0-9a-f:]+)', result, re.IGNORECASE | re.DOTALL)
                    if not mac_match:
                        mac_match = re.search('eth\\d+(?:@\\S+)?:.*?\\n\\s+link/ether ([0-9a-f:]+)', result, re.IGNORECASE | re.DOTALL)
                    if mac_match:
                        mac = mac_match.group(1)
                        print(f'[Mininet-WiFi]   h0       (Receiver - Wired) → MAC: {mac} | IP: {self.receiver_ip}')
                for sta_name, sta_obj in self.stations.items():
                    result = sta_obj.cmd('ip link show')
                    import re
                    mac_match = re.search(f'{sta_name}-wlan\\d+:.*?\\n\\s+link/ether ([0-9a-f:]+)', result, re.IGNORECASE | re.DOTALL)
                    if not mac_match:
                        mac_match = re.search('wlan\\d+:.*?\\n\\s+link/ether ([0-9a-f:]+)', result, re.IGNORECASE | re.DOTALL)
                    if mac_match:
                        mac = mac_match.group(1)
                        role = 'Sender (WiFi)'
                        ip = 'N/A'
                        for port, cfg in self.port_configs.items():
                            if cfg['station'] == sta_name:
                                ip = cfg['ip']
                                break
                        print(f'[Mininet-WiFi]   {sta_name:8s} ({role:12s}) → MAC: {mac} | IP: {ip}')
                print(f"[Mininet-WiFi] {'=' * 60}\n")
                self.emulation_active = True
                print(f'[Mininet-WiFi] ✅ Real WiFi network emulation configured for {len(ports)} ports')
                if use_ap_mode:
                    if wifi_encrypted_flag:
                        print(f"[Mininet-WiFi] ✅ Link-layer encryption '{encryption_type.upper()}' is ACTIVE (AP mode)")
                    else:
                        print(f'[Mininet-WiFi] ℹ️  Running in AP mode (no link-layer encryption)')
                    if capture_monitor_mode:
                        print(f'[Mininet-WiFi] ✅ Capture mode: Monitor (captures over-the-air 802.11 frames)')
                    else:
                        print(f'[Mininet-WiFi] ℹ️  Capture mode: Standard (decrypted IP packets)')
                else:
                    print(f'[Mininet-WiFi] ℹ️  Running in ad-hoc mode (no link-layer encryption)')
                if use_wmediumd:
                    print(f"[Mininet-WiFi] ✅ Propagation model '{propagation_model}' is ACTIVE")
                else:
                    print(f'[Mininet-WiFi] ℹ️  Running without wmediumd (basic WiFi mode)')
                return ports
            except Exception as e:
                print(f'[Mininet-WiFi] ❌ Error during WiFi setup: {e}')
                print(f'[Mininet-WiFi] Falling back to simple host mode...')
                import traceback
                traceback.print_exc()
                self.cleanup_network_emulation()
                return self._setup_simple_host_network(ports, per_port_rate, per_port_netem, default_rate, delay_ms, jitter_ms, loss_percent)
        except Exception as e:
            print(f'[Mininet-WiFi] ❌ Error creating WiFi network: {e}')
            import traceback
            traceback.print_exc()
            raise

    def _setup_simple_host_network(self, ports, per_port_rate, per_port_netem, default_rate, delay_ms, jitter_ms, loss_percent):
        """
        Fallback: Create simple Mininet network with hosts instead of WiFi stations.
        This works when WiFi configuration fails.
        """
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, Host
        from mininet.link import TCLink
        print(f'[Mininet-Simple] Creating network with {len(ports)} hosts (no WiFi)')
        try:
            self.net = Mininet(link=TCLink, switch=OVSSwitch, controller=None, autoSetMacs=True)
            s1 = self.net.addSwitch('s1')
            print(f'[Mininet-Simple] Creating receiver host h0 at IP 10.0.0.1')
            h0 = self.net.addHost('h0', ip='10.0.0.1/24')
            self.net.addLink(h0, s1)
            self.receiver_station = h0
            self.receiver_ip = '10.0.0.1'
            for i, port in enumerate(ports):
                host_name = f'h{i + 1}'
                host_ip = f'10.0.0.{i + 2}/24'
                port_netem = per_port_netem.get(port, {})
                bw_mbps = self._parse_bandwidth(port_netem.get('rate', per_port_rate.get(port, default_rate)))
                delay_val = port_netem.get('delay_ms', delay_ms)
                jitter_val = port_netem.get('jitter_ms', jitter_ms)
                loss_val = port_netem.get('loss_percent', loss_percent)
                print(f'[Mininet-Simple] Creating sender host {host_name} at IP {host_ip}')
                h = self.net.addHost(host_name, ip=host_ip)
                link_params = {'bw': bw_mbps}
                if delay_val > 0:
                    link_params['delay'] = f'{delay_val}ms'
                if jitter_val > 0:
                    link_params['jitter'] = f'{jitter_val}ms'
                if loss_val > 0:
                    link_params['loss'] = loss_val
                self.net.addLink(h, s1, **link_params)
                self.port_configs[port] = {'rate': f'{bw_mbps}mbit', 'delay_ms': delay_val, 'jitter_ms': jitter_val, 'loss_percent': loss_val, 'station': host_name, 'station_obj': h, 'ip': host_ip.split('/')[0]}
                self.stations[host_name] = h
            print(f'[Mininet-Simple] Starting network...')
            self.net.start()
            s1.cmd('ovs-ofctl add-flow {} action=normal'.format(s1.name))
            print(f'[Mininet-Simple] Testing connectivity...')
            time.sleep(2)
            for port, config in self.port_configs.items():
                h_obj = config['station_obj']
                result = h_obj.cmd(f'ping -c 1 -W 1 {self.receiver_ip}')
                if '1 received' in result:
                    print(f"[Mininet-Simple] ✅ {config['station']} can reach receiver")
                else:
                    print(f"[Mininet-Simple] ⚠️  {config['station']} cannot reach receiver")
            self.emulation_active = True
            print(f'[Mininet-Simple] ✅ Simple host network ready for {len(ports)} ports')
            return ports
        except Exception as e:
            print(f'[Mininet-Simple] ❌ Error creating simple network: {e}')
            import traceback
            traceback.print_exc()
            raise

    def _configure_encryption(self, encryption_type, encryption_key):
        """Encryption is now configured directly in AP parameters."""
        print(f'[Mininet-WiFi] Encryption {encryption_type} configured via AP settings')

    def _apply_station_tc_rules(self):
        """Apply traffic control rules on each wireless station's interface."""
        print(f'[Mininet-WiFi] Applying per-station traffic control...')
        for port, config in self.port_configs.items():
            sta_obj = config['station_obj']
            sta_name = config['station']
            result = sta_obj.cmd('ip link show')
            wlan_intf = None
            possible_names = [f'{sta_name}-wlan0', 'wlan0', f'{sta_name}wlan0']
            for name in possible_names:
                if name in result:
                    wlan_intf = name
                    break
            if not wlan_intf:
                import re
                match = re.search('(\\S*wlan\\d+)', result)
                if match:
                    wlan_intf = match.group(1).rstrip(':')
            if not wlan_intf:
                print(f'[Mininet-WiFi] ⚠️  No wireless interface found for {sta_name}')
                print(f'[Mininet-WiFi] Available interfaces: {result}')
                continue
            print(f'[Mininet-WiFi] Configuring tc on {sta_name} interface {wlan_intf}')
            sta_obj.cmd(f'tc qdisc del dev {wlan_intf} root 2>/dev/null')
            bw_mbit = self._parse_bandwidth(config['rate'])
            delay = config['delay_ms']
            jitter = config['jitter_ms']
            loss = config['loss_percent']
            sta_obj.cmd(f'tc qdisc add dev {wlan_intf} root handle 1: htb default 10')
            sta_obj.cmd(f'tc class add dev {wlan_intf} parent 1: classid 1:10 htb rate {bw_mbit}mbit ceil {bw_mbit}mbit')
            if delay > 0 or jitter > 0 or loss > 0:
                netem_cmd = f'tc qdisc add dev {wlan_intf} parent 1:10 handle 10: netem'
                if delay > 0:
                    netem_cmd += f' delay {delay}ms'
                    if jitter > 0:
                        netem_cmd += f' {jitter}ms distribution normal'
                if loss > 0:
                    netem_cmd += f' loss {loss}%'
                result = sta_obj.cmd(netem_cmd)
                print(f'[Mininet-WiFi] Applied netem on {sta_name}/{wlan_intf}: delay={delay}ms jitter={jitter}ms loss={loss}%')
            else:
                sta_obj.cmd(f'tc qdisc add dev {wlan_intf} parent 1:10 handle 10: fq_codel')
        print(f'[Mininet-WiFi] ✅ Traffic control configured on all stations')

    def _preconfigure_mac80211_hwsim_for_monitor(self):
        """Pre-configure mac80211_hwsim with enough radios for stations + APs + monitor interface."""
        try:
            import subprocess
            num_radios_needed = len(self.stations) + len(self.aps) + 1
            print(f'[Mininet-WiFi]   Need {num_radios_needed} radios ({len(self.stations)} stations + {len(self.aps)} APs + 1 monitor)')
            result = subprocess.run(['lsmod'], capture_output=True, text=True)
            if 'mac80211_hwsim' in result.stdout:
                print(f'[Mininet-WiFi]   Unloading existing mac80211_hwsim...')
                subprocess.run(['modprobe', '-r', 'mac80211_hwsim'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1)
            print(f'[Mininet-WiFi]   Loading mac80211_hwsim with {num_radios_needed} radios...')
            subprocess.run(['modprobe', 'mac80211_hwsim', f'radios={num_radios_needed}'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)
            print(f'[Mininet-WiFi] ✅ mac80211_hwsim pre-configured for monitor mode')
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error pre-configuring mac80211_hwsim: {e}')

    def _create_monitor_interface_early(self):
        """Create monitor interface immediately after loading mac80211_hwsim.

        This must be called BEFORE configureWifiNodes() to ensure the PHY is claimed
        before Mininet-WiFi assigns all PHYs to stations.
        """
        try:
            import subprocess
            import re
            phy_result = subprocess.run(['iw', 'phy'], capture_output=True, text=True)
            all_phys = re.findall('Wiphy (phy\\d+)', phy_result.stdout)
            if not all_phys:
                print(f'[Mininet-WiFi] ⚠️  No PHYs found after loading mac80211_hwsim')
                self.monitor_interface = None
                return
            monitor_phy = all_phys[-1]
            print(f'[Mininet-WiFi]   Using PHY {monitor_phy} for monitor interface (last of {len(all_phys)} available)')
            monitor_name = 'mon0'
            print(f"[Mininet-WiFi]   Creating monitor interface '{monitor_name}' on {monitor_phy}...")
            subprocess.run(['iw', 'dev', monitor_name, 'del'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            result = subprocess.run(['iw', 'phy', monitor_phy, 'interface', 'add', monitor_name, 'type', 'monitor'], capture_output=True, text=True)
            if result.returncode != 0:
                print(f'[Mininet-WiFi] ⚠️  Failed to create monitor interface: {result.stderr}')
                self.monitor_interface = None
                return
            subprocess.run(['ip', 'link', 'set', monitor_name, 'up'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['iw', 'dev', monitor_name, 'set', 'channel', '1'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            check_result = subprocess.run(['ip', 'link', 'show', monitor_name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            if check_result.returncode == 0:
                self.monitor_interface = monitor_name
                print(f"[Mininet-WiFi] ✅ Monitor interface '{monitor_name}' created on {monitor_phy}")
                print(f'[Mininet-WiFi]    This interface will capture encrypted 802.11 frames')
            else:
                print(f'[Mininet-WiFi] ⚠️  Monitor interface creation failed - verification failed')
                self.monitor_interface = None
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error creating early monitor interface: {e}')
            import traceback
            traceback.print_exc()
            self.monitor_interface = None

    def _verify_monitor_interface(self):
        """Verify that the monitor interface is still operational after network start."""
        if not self.monitor_interface:
            print(f'[Mininet-WiFi] ⚠️  No monitor interface was created')
            return
        try:
            import subprocess
            check_result = subprocess.run(['ip', 'link', 'show', self.monitor_interface], capture_output=True, text=True)
            if check_result.returncode == 0:
                if 'UP' in check_result.stdout:
                    print(f"[Mininet-WiFi] ✅ Monitor interface '{self.monitor_interface}' is operational")
                else:
                    print(f'[Mininet-WiFi] ⚠️  Monitor interface exists but is DOWN, bringing it up...')
                    subprocess.run(['ip', 'link', 'set', self.monitor_interface, 'up'], check=False)
                    subprocess.run(['iw', 'dev', self.monitor_interface, 'set', 'channel', '1'], check=False)
            else:
                print(f"[Mininet-WiFi] ⚠️  Monitor interface '{self.monitor_interface}' disappeared!")
                self.monitor_interface = None
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error verifying monitor interface: {e}')

    def _add_sniffer_station(self, sniffer_position='30,5,0', sniffer_ip='10.0.0.250/24', encryption_type='none', encryption_key=''):
        """Add a dedicated sniffer station to the Mininet-WiFi topology.

        This sniffer station will be part of the simulated network and affected by
        propagation models, path loss, mobility, etc.

        Args:
            sniffer_position: Position of sniffer station (x,y,z)
            sniffer_ip: IP address for sniffer station
            encryption_type: Encryption type ('none', 'wpa2', 'wpa3')
            encryption_key: Encryption key/passphrase
        """
        try:
            print(f"[Mininet-WiFi] Adding sniffer station 'snf1' at position {sniffer_position}...")
            sniffer_params = {'ip': sniffer_ip, 'mac': '00:00:00:00:ff:ff', 'position': sniffer_position, 'range': 100}
            if encryption_type in ['wpa2', 'wpa3']:
                sniffer_params['passwd'] = encryption_key
                sniffer_params['encrypt'] = encryption_type
            snf = self.net.addStation('snf1', **sniffer_params)
            self.stations['snf1'] = snf
            self.sniffer_station = snf
            print(f"[Mininet-WiFi] ✅ Sniffer station 'snf1' added at {sniffer_position}")
            print(f'[Mininet-WiFi]    This station will be affected by propagation model and path loss')
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error adding sniffer station: {e}')
            import traceback
            traceback.print_exc()
            self.sniffer_station = None

    def _configure_sniffer_monitor_mode(self):
        """Configure the sniffer station for promiscuous packet capture.

        NOTE: With wmediumd in interference mode, a true monitor interface won't receive
        unicast frames between other stations. Instead, we keep the sniffer associated
        with the AP in managed mode and enable promiscuous mode for capturing.

        This must be called AFTER self.net.start() to ensure the wireless interface exists.
        """
        if not self.sniffer_station:
            print(f'[Mininet-WiFi] ⚠️  No sniffer station to configure')
            return
        try:
            print(f'[Mininet-WiFi] Configuring sniffer station for promiscuous capture...')
            snf = self.sniffer_station
            time.sleep(2)
            result = snf.cmd('ip link show')
            wlan_intf = None
            if 'snf1-wlan0' in result:
                wlan_intf = 'snf1-wlan0'
            elif 'wlan0' in result:
                wlan_intf = 'wlan0'
            else:
                print(f'[Mininet-WiFi] ⚠️  Could not find wireless interface on sniffer station')
                print(f'[Mininet-WiFi] Available interfaces: {result}')
                self.monitor_interface = None
                return
            print(f'[Mininet-WiFi]   Found sniffer wireless interface: {wlan_intf}')
            print(f'[Mininet-WiFi]   Creating monitor interface from {wlan_intf}...')
            monitor_intf = 'mon_snf1'
            snf.cmd(f'iw dev {monitor_intf} del 2>/dev/null')
            snf.cmd(f'iw dev {wlan_intf} interface add {monitor_intf} type monitor')
            time.sleep(0.5)
            snf.cmd(f'ip link set {monitor_intf} up')
            time.sleep(0.5)
            verify_result = snf.cmd(f'iw dev {monitor_intf} info')
            if 'type monitor' in verify_result:
                self.monitor_interface = monitor_intf
                print(f'[Mininet-WiFi] ✅ Sniffer monitor interface created: {monitor_intf}')
                print(f'[Mininet-WiFi]    Base interface {wlan_intf} stays associated with AP')
                print(f'[Mininet-WiFi]    Monitor interface {monitor_intf} captures all frames delivered to this radio')
                if getattr(self, 'use_wmediumd', False):
                    print(f'[Mininet-WiFi]    ⚠️  Note: Due to wmediumd limitations, may only capture broadcast/multicast + beacons')
            else:
                print(f'[Mininet-WiFi] ⚠️  Failed to create monitor interface')
                print(f'[Mininet-WiFi] Interface info: {verify_result}')
                self.monitor_interface = None
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error configuring sniffer monitor mode: {e}')
            import traceback
            traceback.print_exc()
            self.monitor_interface = None

    def _patch_wmediumd_config_for_sniffer(self):
        """Patch the wmediumd configuration to mark the sniffer station.

        This modifies the generated wmediumd config file to add sniffer support.
        Must be called BEFORE the network starts and wmediumd is launched.
        """
        if not self.sniffer_station:
            return
        try:
            import glob
            import re
            config_files = glob.glob('/tmp/mn_wmd_config_*.cfg')
            if not config_files:
                print(f'[Mininet-WiFi] ⚠️  No wmediumd config file found to patch')
                return
            config_file = max(config_files, key=lambda f: os.path.getmtime(f))
            print(f'[Mininet-WiFi] Patching wmediumd config: {config_file}')
            with open(config_file, 'r') as f:
                config_content = f.read()
            sniffer_mac = None
            if hasattr(self.sniffer_station, 'wintfs') and len(self.sniffer_station.wintfs) > 0:
                sniffer_intf = list(self.sniffer_station.wintfs.values())[0]
                sniffer_mac = sniffer_intf.mac
            if not sniffer_mac:
                print(f'[Mininet-WiFi] ⚠️  Could not find sniffer MAC address')
                return
            print(f'[Mininet-WiFi]   Sniffer MAC: {sniffer_mac}')
            ids_match = re.search('ids = \\[(.*?)\\];', config_content, re.DOTALL)
            if ids_match:
                ids_content = ids_match.group(1)
                mac_list = re.findall('"([0-9a-f:]+)"', ids_content)
                sniffer_index = None
                for i, mac in enumerate(mac_list):
                    if mac.lower() == sniffer_mac.lower():
                        sniffer_index = i
                        break
                if sniffer_index is not None:
                    print(f'[Mininet-WiFi]   Sniffer is node index {sniffer_index} in wmediumd config')
                    insert_pos = config_content.find('isnodeaps = (')
                    if insert_pos != -1:
                        num_nodes = len(mac_list)
                        sniffer_array = ['1' if i == sniffer_index else '0' for i in range(num_nodes)]
                        sniffer_line = f"\n\t/* Sniffer node configuration */\n\tisnodesniffers = ({', '.join(sniffer_array)});"
                        isnodeaps_end = config_content.find(');', insert_pos) + 2
                        config_content = config_content[:isnodeaps_end] + sniffer_line + config_content[isnodeaps_end:]
                        with open(config_file, 'w') as f:
                            f.write(config_content)
                        print(f'[Mininet-WiFi] ✅ Added sniffer configuration to wmediumd config')
                        print(f'[Mininet-WiFi]    ⚠️  Note: This requires wmediumd with sniffer support')
                        print(f"[Mininet-WiFi]    ⚠️  If wmediumd doesn't support 'isnodesniffers', sniffer will only see broadcast frames")
                else:
                    print(f'[Mininet-WiFi] ⚠️  Could not find sniffer MAC in wmediumd config')
            else:
                print(f'[Mininet-WiFi] ⚠️  Could not parse wmediumd config ids')
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error patching wmediumd config: {e}')
            import traceback
            traceback.print_exc()

    def set_sniffer_position(self, x, y, z=0):
        """Change the sniffer station's position in the simulation.

        This allows you to move the sniffer around to test how path loss and
        propagation models affect packet capture.

        Args:
            x: X coordinate in meters
            y: Y coordinate in meters
            z: Z coordinate in meters (default: 0)

        Returns:
            True if position was changed successfully, False otherwise

        Example:
            # Move sniffer to position (50, 30, 0)
            emulation.set_sniffer_position(50, 30, 0)
        """
        if not self.sniffer_station:
            print(f'[Mininet-WiFi] ⚠️  No sniffer station available to move')
            return False
        try:
            print(f'[Mininet-WiFi] Moving sniffer station to position ({x}, {y}, {z})')
            self.sniffer_station.setPosition(f'{x},{y},{z}')
            print(f'[Mininet-WiFi] ✅ Sniffer station moved to ({x}, {y}, {z})')
            print(f'[Mininet-WiFi]    Packet reception will now be affected by distance from this position')
            return True
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error moving sniffer station: {e}')
            import traceback
            traceback.print_exc()
            return False

    def _setup_monitor_interface(self):
        """Create a monitor mode interface to capture encrypted 802.11 frames.

        This should be called AFTER configureWifiNodes() has moved station phys to their namespaces.
        We'll use the last remaining phy in the root namespace (pre-configured by _preconfigure_mac80211_hwsim_for_monitor).
        """
        print(f'[Mininet-WiFi] Setting up monitor interface for encrypted packet capture...')
        try:
            import subprocess
            import re
            phy_result = subprocess.run(['iw', 'phy'], capture_output=True, text=True)
            all_phys = re.findall('Wiphy (phy\\d+)', phy_result.stdout)
            monitor_phy = None
            for phy in all_phys:
                dev_check = subprocess.run(['iw', 'phy', phy, 'info'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                if dev_check.returncode == 0:
                    intf_match = re.search('Interface (\\w+)', dev_check.stdout)
                    if intf_match:
                        monitor_phy = phy
                        print(f'[Mininet-WiFi]   Found available PHY: {monitor_phy}')
                        break
            if monitor_phy:
                monitor_name = 'mon0'
                print(f"[Mininet-WiFi]   Creating monitor interface '{monitor_name}' on {monitor_phy}...")
                subprocess.run(['iw', 'phy', monitor_phy, 'interface', 'add', monitor_name, 'type', 'monitor'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['ip', 'link', 'set', monitor_name, 'up'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(['iw', 'dev', monitor_name, 'set', 'channel', '1'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                check_result = subprocess.run(['ip', 'link', 'show', monitor_name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                if check_result.returncode == 0:
                    self.monitor_interface = monitor_name
                    print(f"[Mininet-WiFi] ✅ Monitor interface '{monitor_name}' created successfully")
                    print(f'[Mininet-WiFi]    This interface will capture encrypted 802.11 frames')
                else:
                    print(f'[Mininet-WiFi] ⚠️  Failed to create monitor interface - verification failed')
                    self.monitor_interface = None
            else:
                print(f'[Mininet-WiFi] ⚠️  No available PHY for monitor interface in root namespace')
                print(f'[Mininet-WiFi] ⚠️  Stations may have claimed all PHYs - check _preconfigure_mac80211_hwsim_for_monitor')
                self.monitor_interface = None
        except Exception as e:
            print(f'[Mininet-WiFi] ⚠️  Error setting up monitor interface: {e}')
            import traceback
            traceback.print_exc()
            self.monitor_interface = None

    def cleanup_network_emulation(self):
        """Clean up Mininet-WiFi network and tc rules."""
        if self.monitor_interface:
            print(f'[Mininet-WiFi] Removing monitor interface {self.monitor_interface}...')
            try:
                import subprocess
                subprocess.run(['ip', 'link', 'set', self.monitor_interface, 'down'], check=False, stderr=subprocess.DEVNULL)
                subprocess.run(['iw', 'dev', self.monitor_interface, 'del'], check=False, stderr=subprocess.DEVNULL)
            except Exception as e:
                print(f'[Mininet-WiFi] Warning: Error removing monitor interface: {e}')
            self.monitor_interface = None
        if self.net:
            print('[Mininet-WiFi] Stopping WiFi network emulation...')
            try:
                self.net.stop()
            except Exception as e:
                print(f'[Mininet-WiFi] Error stopping network: {e}')
            self.net = None
        self.emulation_active = False
        self.stations = {}
        self.aps = {}
        self.port_configs = {}
        self.encryption_config = None
        self.receiver_station = None
        self.receiver_ip = None
        self.capture_monitor_mode = False
        print('[Mininet-WiFi] ✅ Network emulation cleanup complete')

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

def set_per_port_caps(interface='lo', port_start=5004, port_nums=3, default_rate='5mbit', per_port_rate=None, delay_ms=0, jitter_ms=0, loss_percent=0.0, per_port_netem=None, default_class_rate='100000mbit', wifi_encrypted_flag=False, encryption_type='none', encryption_key='mininetwifi12345678', propagation_model='logDistance', path_loss_exp=3.5, tx_power=20, distance_base=10, use_wmediumd=False, capture_monitor_mode=False, ap_position=(10, 10, 0), receiver_position=(10, 10, 0), sniffer_position=(10, 10, 0), station_positions=None):
    """
    Create per-port bandwidth caps using Mininet-WiFi network emulation with REAL wireless features.

    Args:
        interface: Network interface (maintained for compatibility, unused in WiFi mode)
        port_start: Starting port number
        port_nums: Number of ports to configure
        default_rate: Default bandwidth limit for each port
        per_port_rate: Dict of {port: rate} overrides
        delay_ms: Default delay in milliseconds
        jitter_ms: Default jitter in milliseconds
        loss_percent: Default packet loss percentage
        per_port_netem: Dict of per-port network condition overrides
        default_class_rate: Unused in Mininet-WiFi implementation
        wifi_encrypted_flag: Enable WPA2/WPA3 link-layer encryption (requires AP mode)
        encryption_type: WiFi encryption type ('none', 'wpa2', 'wpa3')
        encryption_key: WiFi encryption key/passphrase (min 8 chars for WPA)
        propagation_model: Propagation model ('logDistance', 'ITU', 'friis')
        path_loss_exp: Path loss exponent for logDistance model (higher = more loss)
        tx_power: Transmission power in dBm
        distance_base: Base distance for station positioning (meters)
        use_wmediumd: Whether to use wmediumd for realistic propagation
        capture_monitor_mode: Capture 802.11 frames in monitor mode (shows encryption overhead)
        ap_position: Position of Access Point as (x, y, z) tuple in meters (default: (10, 10, 0))
        receiver_position: Position of receiver station in ad-hoc mode as (x, y, z) tuple (default: (10, 10, 0))
        sniffer_position: Position of sniffer station as (x, y, z) tuple in meters (default: (10, 10, 0))
        station_positions: List of (x, y, z) tuples for sender station positions.
                          If None, stations are arranged in a circle around AP/receiver (default: None)

    Returns:
        List of configured ports
    """
    global _network_emulator
    return _network_emulator.setup_network_emulation(port_start=port_start, port_nums=port_nums, default_rate=default_rate, per_port_rate=per_port_rate, delay_ms=delay_ms, jitter_ms=jitter_ms, loss_percent=loss_percent, per_port_netem=per_port_netem, wifi_encrypted_flag=wifi_encrypted_flag, encryption_type=encryption_type, encryption_key=encryption_key, propagation_model=propagation_model, path_loss_exp=path_loss_exp, tx_power=tx_power, distance_base=distance_base, use_wmediumd=use_wmediumd, capture_monitor_mode=capture_monitor_mode, ap_position=ap_position, receiver_position=receiver_position, sniffer_position=sniffer_position, station_positions=station_positions)
