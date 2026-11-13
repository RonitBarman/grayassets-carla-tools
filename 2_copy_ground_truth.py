"""Step 2: copy CSVs to <dataset>_GroundTruth, preserving subdirectories."""
from helpers.pipeline import dataset_dir, require_directory

# None follows step 1 automatically. Set a path only to process another dataset.
INPUT_DIR = None


def main():
    from helpers.ground_truth import copy_csv_files_preserve_structure
    source = require_directory(dataset_dir(INPUT_DIR))
    print(f'Ground truth: {source}_GroundTruth')
    copy_csv_files_preserve_structure(source)


if __name__ == '__main__':
    main()
