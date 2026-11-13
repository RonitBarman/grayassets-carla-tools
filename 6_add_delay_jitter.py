"""Step 6: transform packet CSV timestamps; does not modify PCAP or the network."""
import random
from helpers.pipeline import require_directory, resolve, step

INPUT_DIR = None                   # None follows step 5 (including all bandwidths)
DELAY_MS = 20
JITTER_MS = 5
SEED = 1


def main():
    from helpers.timing import add_delay_jitter_to_packets
    sources = [resolve(INPUT_DIR)] if INPUT_DIR is not None else step('5_extract_packet_fields.py')['output_directories']()
    for source in sources:
        print(f'Transformed timestamps: {source}_added_delay{DELAY_MS}_jitter{JITTER_MS}')
        random.seed(SEED)
        add_delay_jitter_to_packets(require_directory(source), DELAY_MS, JITTER_MS)


if __name__ == '__main__':
    main()
