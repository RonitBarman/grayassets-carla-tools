"""Alternative step 4: wired Mininet emulation (not the WiFi implementation)."""
from helpers.pipeline import dataset_dir, stream_outputs, require_directory

INPUT_DIR = None
WORKERS = 1
START_PORT = 5004
ENCRYPTED = False
BANDWIDTH_LIMITS = ['50mbit']
DELAY_MS = 0
JITTER_MS = 0
LOSS_PERCENT = 0
PER_PORT_RATE = {}
PER_PORT_NETEM = {}


def output_directories():
    return stream_outputs(dataset_dir(INPUT_DIR), WORKERS, BANDWIDTH_LIMITS,
        DELAY_MS, JITTER_MS, LOSS_PERCENT, ENCRYPTED, True, backend='mininet')


def main():
    import os
    if os.geteuid() != 0:
        raise SystemExit('Run this Mininet step with sudo and the full path to your CARLA-environment Python.')
    from helpers import mininet
    source = require_directory(dataset_dir(INPUT_DIR))
    for output in output_directories():
        print(f"Mininet capture: {output['raw']}")
        try:
            mininet.set_per_port_caps(port_start=START_PORT, port_nums=WORKERS,
                default_rate=output['rate'], delay_ms=DELAY_MS, jitter_ms=JITTER_MS,
                loss_percent=LOSS_PERCENT, per_port_rate=PER_PORT_RATE, per_port_netem=PER_PORT_NETEM)
            mininet.stream_parallel(source, workers=WORKERS, start_port=START_PORT,
                encrypted=ENCRYPTED, folder_prefix=output['prefix'], output_root=str(output['root']))
        finally:
            mininet.clear_per_port_caps('lo')


if __name__ == '__main__':
    main()
