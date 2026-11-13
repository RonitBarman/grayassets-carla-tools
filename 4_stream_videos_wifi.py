"""Alternative step 4: Mininet-WiFi replay, including optional encrypted 802.11 capture."""
from helpers.pipeline import dataset_dir, stream_outputs, require_directory

INPUT_DIR = None
WORKERS = 1
START_PORT = 5004
ENCRYPTED = False                  # application-layer SRTP
BANDWIDTH_LIMITS = ['50mbit']
DELAY_MS = 0
JITTER_MS = 0
LOSS_PERCENT = 0
PER_PORT_RATE = {}
PER_PORT_NETEM = {}

# Link-layer encryption is independent of SRTP.
WIFI_ENCRYPTED = True
ENCRYPTION_TYPE = 'wpa2'            # wpa2 / wpa3
ENCRYPTION_KEY = 'mininetwifi12345678'
CAPTURE_MONITOR_MODE = True         # encrypted 802.11 frames; False = decrypted receiver traffic
USE_WMEDIUMD = False
PROPAGATION_MODEL = 'logDistance'
PATH_LOSS_EXP = 1
TX_POWER = 100
DISTANCE_BASE = 10
AP_POSITION = (50, 50, 0)
RECEIVER_POSITION = (50, 40, 0)
SNIFFER_POSITION = (50, 30, 0)
STATION_POSITIONS = [(50, 40, 0)]    # one sender position per worker, or None


def output_directories():
    return stream_outputs(dataset_dir(INPUT_DIR), WORKERS, BANDWIDTH_LIMITS,
        DELAY_MS, JITTER_MS, LOSS_PERCENT, ENCRYPTED, True, backend='wifi',
        use_wmediumd=USE_WMEDIUMD, path_loss_exp=PATH_LOSS_EXP,
        wifi_encrypted=WIFI_ENCRYPTED, encryption_type=ENCRYPTION_TYPE)


def main():
    import os
    if os.geteuid() != 0:
        raise SystemExit('Run this WiFi step with sudo and the full path to your CARLA-environment Python.')
    from helpers import wifi
    source = require_directory(dataset_dir(INPUT_DIR))
    for output in output_directories():
        print(f"WiFi capture: {output['raw']}")
        try:
            wifi.set_per_port_caps(port_start=START_PORT, port_nums=WORKERS,
                default_rate=output['rate'], delay_ms=DELAY_MS, jitter_ms=JITTER_MS,
                loss_percent=LOSS_PERCENT, per_port_rate=PER_PORT_RATE, per_port_netem=PER_PORT_NETEM,
                wifi_encrypted_flag=WIFI_ENCRYPTED, encryption_type=ENCRYPTION_TYPE,
                encryption_key=ENCRYPTION_KEY, propagation_model=PROPAGATION_MODEL,
                path_loss_exp=PATH_LOSS_EXP, tx_power=TX_POWER, distance_base=DISTANCE_BASE,
                use_wmediumd=USE_WMEDIUMD, capture_monitor_mode=CAPTURE_MONITOR_MODE,
                ap_position=AP_POSITION, receiver_position=RECEIVER_POSITION,
                sniffer_position=SNIFFER_POSITION, station_positions=STATION_POSITIONS)
            wifi.stream_parallel(source, workers=WORKERS, start_port=START_PORT,
                SRTP_encrypted_flag=ENCRYPTED, folder_prefix=output['prefix'],
                network_emulator=wifi._network_emulator, use_wifi=True, output_root=str(output['root']))
        finally:
            wifi.clear_per_port_caps('lo')


if __name__ == '__main__':
    main()
