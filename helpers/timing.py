"""Legacy synthetic delay/jitter transformation of packet CSVs."""
import os

import csv

import random

import shutil

from pathlib import Path

def add_delay_jitter_to_packets(input_folder, delay_ms, jitter_ms, output_root=None):
    """
    Add delay and jitter to packet timings in CSV files within a folder structure.
    
    Args:
        input_folder (str): Path to the input folder containing CSV files
        delay_ms (float): Fixed delay to add to all packets (in milliseconds)
        jitter_ms (float): Random jitter range (in milliseconds)
    
    Note:
        Set random.seed() before calling this function for reproducible results.
    """
    input_path = Path(input_folder)
    if not input_path.exists():
        raise ValueError(f"Input folder '{input_folder}' does not exist")
    output_folder = output_root or f'{input_folder}_added_delay{delay_ms}_jitter{jitter_ms}'
    output_path = Path(output_folder)
    output_path.mkdir(parents=True, exist_ok=True)
    delay_sec = delay_ms / 1000.0
    jitter_range_sec = jitter_ms / 1000.0
    for csv_file in input_path.rglob('*.csv'):
        relative_path = csv_file.relative_to(input_path)
        output_file = output_path / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        process_csv_file(csv_file, output_file, delay_sec, jitter_range_sec)
        print(f'Processed: {csv_file} -> {output_file}')

def process_csv_file(input_file, output_file, delay_sec, jitter_range_sec):
    """
    Process a single CSV file to add delay and jitter to packet timings.
    
    Args:
        input_file (Path): Input CSV file path
        output_file (Path): Output CSV file path
        delay_sec (float): Fixed delay in seconds
        jitter_range_sec (float): Jitter range in seconds
    """
    packets = []
    with open(input_file, 'r', newline='') as csvfile:
        reader = csv.reader(csvfile)
        header = next(reader)
        for row in reader:
            packets.append(row)
        try:
            if 'frame.time_epoch' in header:
                epoch_idx = header.index('frame.time_epoch')
                header.remove('frame.time_epoch')
                for packet in packets:
                    if len(packet) > epoch_idx:
                        del packet[epoch_idx]
            time_col_index = header.index('frame.time_relative')
        except ValueError:
            raise ValueError(f"CSV file {input_file} does not contain 'frame.time_relative' column")
    if not packets:
        with open(output_file, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(header)
        return
    processed_packets = []
    prev_timestamp = None
    for i, packet in enumerate(packets):
        original_timestamp = float(packet[time_col_index])
        delay = delay_sec
        jitter = random.uniform(-jitter_range_sec / 2, jitter_range_sec / 2)
        new_timestamp = original_timestamp + delay + jitter
        if prev_timestamp is not None:
            original_interval = original_timestamp - float(packets[i - 1][time_col_index])
            if new_timestamp <= prev_timestamp:
                new_timestamp = prev_timestamp + original_interval
        new_packet = packet.copy()
        new_packet[time_col_index] = f'{new_timestamp:.9f}'
        processed_packets.append(new_packet)
        prev_timestamp = new_timestamp
    with open(output_file, 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(header)
        writer.writerows(processed_packets)
