"""Step 3: extract headerless packet-size/frame-type CSVs with ffprobe."""
from helpers.pipeline import dataset_dir, require_directory

INPUT_DIR = None                   # None follows step 1
WORKERS = 128
CAMERA_ONLY = False                # always excludes *_gridview.mp4


def main():
    from helpers.frames import extract_frame_typesize_parallel
    source = require_directory(dataset_dir(INPUT_DIR))
    print(f'Frame sizes: {source}_{WORKERS}workers_framesizes')
    extract_frame_typesize_parallel(source, workers=WORKERS, use_camera_regex=CAMERA_ONLY)


if __name__ == '__main__':
    main()
