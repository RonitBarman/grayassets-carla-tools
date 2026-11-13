import os

import shutil

def copy_csv_files_preserve_structure(source_dir, target_suffix='_GroundTruth', output_root=None):
    """
    Copy all CSV files from source directory to a new directory with preserved structure
    
    Args:
        source_dir: Source directory path
        target_suffix: Suffix to append to source directory name for target
    """
    target_dir = output_root or source_dir + target_suffix
    if not os.path.exists(source_dir):
        print(f"Error: Source directory '{source_dir}' does not exist.")
        return
    print(f'Copying CSV files from: {source_dir}')
    print(f'To: {target_dir}')
    csv_count = 0
    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.endswith('.csv'):
                source_file = os.path.join(root, file)
                relative_path = os.path.relpath(root, source_dir)
                if relative_path == '.':
                    target_subdir = target_dir
                else:
                    target_subdir = os.path.join(target_dir, relative_path)
                os.makedirs(target_subdir, exist_ok=True)
                target_file = os.path.join(target_subdir, file)
                shutil.copy2(source_file, target_file)
                print(f'  Copied: {source_file} -> {target_file}')
                csv_count += 1
    print(f'\nCompleted! Copied {csv_count} CSV files.')
