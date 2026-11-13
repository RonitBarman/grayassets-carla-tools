"""Step 5: extract packet timestamps, sizes and RTP fields with tshark."""
from helpers.pipeline import capture_outputs, packet_outputs, require_directory, step

STREAM_SCRIPT = '4_stream_videos.py'  # or 4_stream_videos_mininet.py / 4_stream_videos_wifi.py
INPUT_DIR = None                     # None derives captures from STREAM_SCRIPT
WORKERS = 128                        # extraction workers, not stream workers
USE_REORDERCAP = True


def output_directories():
    return packet_outputs(STREAM_SCRIPT, WORKERS, INPUT_DIR)


def main():
    from helpers.packets import extract_pkt_lengths_parallel
    settings = step(STREAM_SCRIPT)
    decode_args = []
    for index in range(settings['WORKERS']):
        port = settings['START_PORT'] + 2 * index
        decode_args.extend(['-d', f'udp.port=={port},rtp'])
    for source, output in zip(capture_outputs(STREAM_SCRIPT, INPUT_DIR), output_directories()):
        print(f'Packet fields: {output}')
        extract_pkt_lengths_parallel(require_directory(source), workers=WORKERS,
            use_reordercap=USE_REORDERCAP,
            extra_fields=['rtp.seq', 'rtp.timestamp', 'rtp.marker'], extra_args=decode_args)


if __name__ == '__main__':
    main()
