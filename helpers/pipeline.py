"""Shared paths for the numbered scripts; reading settings never runs a step."""
from functools import lru_cache
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]


def resolve(path):
    """Relative settings paths are relative to the repository, not the shell."""
    return (ROOT / Path(path).expanduser()).resolve()


@lru_cache(maxsize=None)
def step(filename):
    return runpy.run_path(str(ROOT / filename))


def dataset_dir(override=None):
    return resolve(override) if override is not None else step('1_generate_videos.py')['DATASET_DIR']


def require_directory(path):
    path = Path(path)
    if not path.is_dir():
        raise FileNotFoundError(f'{path}\nRun the preceding step first, or change INPUT_DIR at the top of this script.')
    return str(path)


def network_prefix(rate, delay, jitter, loss, encrypted=False, shaped=True, backend='loopback', **wifi):
    """The original settings-derived names, shared by streaming and extraction."""
    if backend == 'wifi':
        prefix = 'mininetwifi_' + ('srtp_' if encrypted else 'rtp_')
    else:
        prefix = ('mininet_' if backend == 'mininet' else '') + ('encrypted_' if encrypted else '')
    if shaped:
        prefix += f'bw{rate}_delay{delay}_jitter{jitter}_loss{loss}_'
    if backend == 'wifi':
        if wifi.get('use_wmediumd'):
            prefix += f"wmediumd_pathloss{wifi['path_loss_exp']}_"
        prefix += f"wifi_{wifi['encryption_type']}_" if wifi.get('wifi_encrypted') else 'wifi_unencrypted_'
    return prefix


def stream_outputs(source, workers, rates, delay, jitter, loss, encrypted, shaped,
                   backend='loopback', **wifi):
    source = Path(source)
    root = source.parent / f'{source.name}_packet_transmission'
    outputs = []
    for rate in rates:
        prefix = network_prefix(rate, delay, jitter, loss, encrypted, shaped, backend, **wifi)
        outputs.append({
            'rate': rate, 'prefix': prefix, 'root': root,
            'raw': root / f'{prefix}{workers}workers_packets_raw',
            'sizes': root / 'no_rtpinfo' / f'{prefix}{workers}workers_packets_size',
        })
    return outputs


def capture_outputs(stream_script, override=None):
    settings = step(stream_script)
    if override is not None:
        return [resolve(override)]
    return [item['raw'] for item in settings['output_directories']()]


def packet_outputs(stream_script, workers, override=None):
    return [Path(f'{source}_{workers}workers_pktsize_rtpinfo_force')
            for source in capture_outputs(stream_script, override)]
