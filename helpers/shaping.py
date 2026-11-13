"""Linux per-port HTB/netem traffic control."""
import subprocess
import shlex

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

def _sh(cmd, check=True, **popen_kw):
    """Run a shell command with nice logging."""
    if isinstance(cmd, list):
        s = ' '.join((shlex.quote(x) for x in cmd))
    else:
        s = cmd
    print('[tc]', s)
    return subprocess.run(s, shell=True, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **popen_kw)

def _gen_ports(port_start: int, port_nums: int):
    """Generate even RTP ports: start, start+2, ... (length=port_nums)."""
    return [port_start + 2 * i for i in range(port_nums)]

def clear_per_port_caps(interface='lo'):
    """Clear any root qdisc on the interface."""
    try:
        _sh(f'sudo tc qdisc del dev {interface} root', check=False)
    finally:
        print(f'[tc] Cleared root qdisc on {interface}')

def set_per_port_caps(interface='lo', port_start=5004, port_nums=3, default_rate='5mbit', per_port_rate=None, delay_ms: int=0, jitter_ms: int=0, loss_percent: float=0.0, per_port_netem=None, default_class_rate='100000mbit'):
    """
    Create per-port bandwidth caps using HTB + (optional) netem on UDP dst ports.
    - interface: e.g., 'lo' or 'eth0'
    - ports: generated as port_start, port_start+2, ... for port_nums items
    - default_rate: applied to all ports unless per_port_rate / per_port_netem overrides
    - per_port_rate: {port:int -> 'RATE'} to customize individual ports
    - per_port_netem: {port:int -> dict(delay_ms=.., jitter_ms=.., loss_percent=.., rate='..')}
    """
    ports = _gen_ports(port_start, port_nums)
    per_port_rate = per_port_rate or {}
    per_port_netem = per_port_netem or {}
    _sh(f'sudo tc qdisc del dev {interface} root', check=False)
    _sh(f'sudo tc qdisc add dev {interface} root handle 1: htb default 999')
    _sh(f'sudo tc class add dev {interface} parent 1: classid 1:999 htb rate {default_class_rate} ceil {default_class_rate}')
    for p in ports:
        rate = per_port_netem.get(p, {}).get('rate', per_port_rate.get(p, default_rate))
        _sh(f'sudo tc class add dev {interface} parent 1: classid 1:{p} htb rate {rate} ceil {rate}')
        d_ms = per_port_netem.get(p, {}).get('delay_ms', delay_ms)
        j_ms = per_port_netem.get(p, {}).get('jitter_ms', jitter_ms)
        l_pc = per_port_netem.get(p, {}).get('loss_percent', loss_percent)
        if d_ms or j_ms or l_pc:
            ne = ['sudo', 'tc', 'qdisc', 'add', 'dev', interface, 'parent', f'1:{p}', 'handle', f'{p}:', 'netem']
            if d_ms:
                ne += ['delay', f'{d_ms}ms']
                if j_ms:
                    ne += [f'{j_ms}ms', 'distribution', 'normal']
            if l_pc:
                ne += ['loss', f'{l_pc}%']
            _sh(ne)
        else:
            _sh(f'sudo tc qdisc add dev {interface} parent 1:{p} handle {p}: fq_codel')
        _sh(f'sudo tc filter add dev {interface} protocol ip parent 1: prio 1 flower ip_proto udp dst_port {p} action pass classid 1:{p}')
    print(f'[tc] Configured per-port caps on {interface}: {ports}')
    return ports
