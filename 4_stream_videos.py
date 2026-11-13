"""Step 4: loopback RTP/SRTP replay, capture, and optional per-port shaping."""
from helpers.pipeline import dataset_dir, stream_outputs, require_directory

INPUT_DIR = None                   # None follows step 1
WORKERS = 64
START_PORT = 5004                  # even RTP ports; RTCP uses the adjacent port
ENCRYPTED = False                  # SRTP instead of RTP
NETWORK_CONTROL = True
BANDWIDTH_LIMITS = ['50mbit']       # one replay pass per bandwidth
DELAY_MS = 0
JITTER_MS = 0
LOSS_PERCENT = 0
PER_PORT_RATE = {}                 # e.g. {5004: '10mbit'}
PER_PORT_NETEM = {}                # e.g. {5004: {'rate': '5mbit', 'delay_ms': 20}}


def output_directories():
    return stream_outputs(dataset_dir(INPUT_DIR), WORKERS, BANDWIDTH_LIMITS,
        DELAY_MS, JITTER_MS, LOSS_PERCENT, ENCRYPTED, NETWORK_CONTROL)


def main():
    from helpers.loopback import stream_parallel
    from helpers.shaping import set_per_port_caps, clear_per_port_caps
    source = require_directory(dataset_dir(INPUT_DIR))
    for output in output_directories():
        print(f"Capture: {output['raw']}")
        try:
            if NETWORK_CONTROL:
                set_per_port_caps(port_start=START_PORT, port_nums=WORKERS,
                    default_rate=output['rate'], delay_ms=DELAY_MS, jitter_ms=JITTER_MS,
                    loss_percent=LOSS_PERCENT, per_port_rate=PER_PORT_RATE, per_port_netem=PER_PORT_NETEM)
            stream_parallel(source, workers=WORKERS, start_port=START_PORT,
                encrypted=ENCRYPTED, folder_prefix=output['prefix'], output_root=str(output['root']))
        finally:
            if NETWORK_CONTROL:
                clear_per_port_caps('lo')


if __name__ == '__main__':
    main()
