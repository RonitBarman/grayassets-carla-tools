"""Vehicle scheduling for continuous recordings."""
import os
import csv
import random
import math
import itertools
from helpers.spawn import get_spawn_config

def schedule_random_vehicles_with_color(sim, num_vehicles2simulate, vehicle_type_list, spawn_loc_list, speed_range, buffer_duration_list, seed, start_time, folder_name, vehicle_color_name_list=None, vehicle_color_map=None, lane_list=[0], lane_random_offset_flag=False, lane_random_offset_range=0.0):
    random.seed(seed)
    current_time = start_time
    schedule_log_path = os.path.join(sim.output_root, folder_name, 'all_vehicle_schedules.csv')
    os.makedirs(os.path.dirname(schedule_log_path), exist_ok=True)
    with open(schedule_log_path, 'w', newline='') as log_file:
        writer = csv.writer(log_file)
        writer.writerow(['index', 'vehicle_type', 'vehicle_color_name', 'vehicle_color', 'spawn_direction', 'spawn_time', 'speed', 'min_duration', 'buffer', 'spawn_x', 'spawn_y', 'velocity_x', 'velocity_y', 'yaw', 'lane', 'lane_random_offset'])
    use_colors = bool(vehicle_color_name_list) and bool(vehicle_color_map)
    for i in range(num_vehicles2simulate):
        vehicle_type = random.choice(vehicle_type_list)
        spawn_loc = random.choice(spawn_loc_list)
        speed = random.uniform(speed_range[0], speed_range[1])
        buffer_duration = random.choice(buffer_duration_list)
        vehicle_color_name = None
        vehicle_color = None
        if use_colors:
            vehicle_color_name = random.choice(vehicle_color_name_list)
            vehicle_color = vehicle_color_map.get(vehicle_color_name)
            if vehicle_color is None:
                raise KeyError(f"Color '{vehicle_color_name}' not found in vehicle_color_map")
        lane = random.choice(lane_list)
        if lane_random_offset_flag:
            lane_random_offset = random.gauss(mu=0, sigma=lane_random_offset_range)
            lane_random_offset = round(lane_random_offset, 2)
            if lane_random_offset < -lane_random_offset_range:
                lane_random_offset = -lane_random_offset_range
            elif lane_random_offset > lane_random_offset_range:
                lane_random_offset = lane_random_offset_range
        else:
            lane_random_offset = 0
        loc, vel, yaw, min_duration = get_spawn_config(spawn_loc, speed, lane, lane_random_offset)
        print(f"[{i + 1}/{num_vehicles2simulate}] Scheduling {vehicle_type} from {spawn_loc} at t={current_time:.2f}, speed={speed:.3f}, min_duration={min_duration}, buffer={buffer_duration}, color={vehicle_color_name or 'default'}, lane={lane}, lane_random_offset={lane_random_offset}")
        with open(schedule_log_path, 'a', newline='') as log_file:
            writer = csv.writer(log_file)
            writer.writerow([i, vehicle_type, vehicle_color_name, vehicle_color, spawn_loc, current_time, speed, min_duration, buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw, lane, lane_random_offset])
        sim.schedule_vehicle(vehicle_type=vehicle_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=current_time, duration=min_duration + buffer_duration, vehicle_color_name=vehicle_color_name, vehicle_color=vehicle_color)
        current_time += min_duration + buffer_duration
    return current_time

def schedule_random_vehicles_with_followingvehicles(sim, num_vehicles2simulate, vehicle_type_list, spawn_loc_list, speed_range, buffer_duration_list, seed, start_time, folder_name):
    random.seed(seed)
    current_time = start_time
    schedule_log_path = os.path.join(sim.output_root, folder_name, 'all_vehicle_schedules_with_followers.csv')
    os.makedirs(os.path.dirname(schedule_log_path), exist_ok=True)
    with open(schedule_log_path, 'w', newline='') as log_file:
        writer = csv.writer(log_file)
        writer.writerow(['index', 'vehicle_type', 'spawn_direction', 'spawn_time', 'speed', 'min_duration', 'buffer', 'spawn_x', 'spawn_y', 'velocity_x', 'velocity_y', 'yaw', 'is_follower'])
    index = 0
    for _ in range(num_vehicles2simulate):
        vehicle_type = random.choice(vehicle_type_list)
        spawn_loc = random.choice(spawn_loc_list)
        speed = random.uniform(speed_range[0], speed_range[1])
        buffer_duration = random.choice(buffer_duration_list)
        loc, vel, yaw, min_duration = get_spawn_config(spawn_loc, speed)
        duration = min_duration + buffer_duration
        print(f'[{index}] Scheduling {vehicle_type} from {spawn_loc} at t={current_time:.2f}, speed={speed:.2f}')
        with open(schedule_log_path, 'a', newline='') as log_file:
            writer = csv.writer(log_file)
            writer.writerow([index, vehicle_type, spawn_loc, current_time, speed, min_duration, buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw, False])
        sim.schedule_vehicle(vehicle_type=vehicle_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=current_time, duration=duration)
        index += 1
        time_extension = duration + 1.0
        if random.random() < 0.5:
            delay = math.floor(random.uniform(1.5, 4.5) * 2) / 2.0
            follower_time = current_time + delay
            follower_type = random.choice(vehicle_type_list)
            print(f'    ↳ Follower: {follower_type} with delay={delay:.2f}s at t={follower_time:.2f}')
            with open(schedule_log_path, 'a', newline='') as log_file:
                writer = csv.writer(log_file)
                writer.writerow([index, follower_type, spawn_loc, follower_time, speed, min_duration, buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw, True])
            sim.schedule_vehicle(vehicle_type=follower_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=follower_time, duration=duration)
            index += 1
            time_extension = delay + duration + 1.0
        current_time += time_extension
    return current_time


def schedule_random_vehicles_with_following_and_opposite_vehicles(sim, num_vehicles2simulate, vehicle_type_list, spawn_loc_list, speed_range, buffer_duration_list, seed, start_time, folder_name, allow_followers=True):
    random.seed(seed)
    current_time = start_time
    schedule_log_path = os.path.join(sim.output_root, folder_name, 'all_vehicle_schedules_with_followers.csv')
    os.makedirs(os.path.dirname(schedule_log_path), exist_ok=True)
    with open(schedule_log_path, 'w', newline='') as log_file:
        writer = csv.writer(log_file)
        writer.writerow(['index', 'vehicle_type', 'spawn_direction', 'spawn_time', 'speed', 'min_duration', 'buffer', 'spawn_x', 'spawn_y', 'velocity_x', 'velocity_y', 'yaw', 'is_follower'])
    index = 0
    opposite_directions = {'up': 'down', 'down': 'up', 'left': 'right', 'right': 'left'}
    for _ in range(num_vehicles2simulate):
        vehicle_type = random.choice(vehicle_type_list)
        spawn_loc = random.choice(spawn_loc_list)
        speed = random.uniform(speed_range[0], speed_range[1])
        buffer_duration = random.choice(buffer_duration_list)
        loc, vel, yaw, min_duration = get_spawn_config(spawn_loc, speed)
        duration = min_duration + buffer_duration
        print(f'[{index}] Scheduling {vehicle_type} from {spawn_loc} at t={current_time:.2f}, speed={speed:.2f}')
        with open(schedule_log_path, 'a', newline='') as log_file:
            writer = csv.writer(log_file)
            writer.writerow([index, vehicle_type, spawn_loc, current_time, speed, min_duration, buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw, False])
        sim.schedule_vehicle(vehicle_type=vehicle_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=current_time, duration=duration)
        index += 1
        end_times = [current_time + duration]
        if allow_followers and random.random() < 0.5:
            delay = math.floor(random.uniform(1.5, 4.5) * 2) / 2.0
            follower_time = current_time + delay
            follower_type = random.choice(vehicle_type_list)
            print(f'    ↳ Follower: {follower_type} with delay={delay:.2f}s at t={follower_time:.2f}')
            with open(schedule_log_path, 'a', newline='') as log_file:
                writer = csv.writer(log_file)
                writer.writerow([index, follower_type, spawn_loc, follower_time, speed, min_duration, buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw, True])
            sim.schedule_vehicle(vehicle_type=follower_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=follower_time, duration=duration)
            index += 1
            end_times.append(follower_time + duration)
        if random.random() < 0.5 and spawn_loc in opposite_directions:
            opposite_spawn = opposite_directions[spawn_loc]
            opposite_vehicle_type = random.choice(vehicle_type_list)
            opposite_speed = random.uniform(speed_range[0], speed_range[1])
            opp_loc, opp_vel, opp_yaw, opp_min_duration = get_spawn_config(opposite_spawn, opposite_speed)
            opp_buffer_duration = random.choice(buffer_duration_list)
            opp_duration = opp_min_duration + opp_buffer_duration
            print(f'    ↳ Opposite: {opposite_vehicle_type} from {opposite_spawn} at t={current_time:.2f}, speed={opposite_speed:.2f}')
            with open(schedule_log_path, 'a', newline='') as log_file:
                writer = csv.writer(log_file)
                writer.writerow([index, opposite_vehicle_type, opposite_spawn, current_time, opposite_speed, opp_min_duration, opp_buffer_duration, opp_loc.x, opp_loc.y, opp_vel.x, opp_vel.y, opp_yaw, False])
            sim.schedule_vehicle(vehicle_type=opposite_vehicle_type, velocity=(opp_vel.x, opp_vel.y), spawn_pos=(opp_loc.x, opp_loc.y), spawn_yaw=opp_yaw, spawn_time=current_time, duration=opp_duration)
            index += 1
            end_times.append(current_time + opp_duration)
        time_extension = max(end_times) - current_time + 1.0
        current_time += time_extension
    return current_time

def schedule_all_combinations(sim, vehicle_type_list, spawn_loc_list, speed_list, buffer_duration_list, start_time=1.0, folder_name=None):
    """
    Schedules one vehicle for every combination of vehicle_type, spawn location, speed, and buffer.
    Each vehicle is scheduled sequentially without overlap.

    Args:
        sim: CARLASimulator instance
        vehicle_type_list: list of strings (CARLA blueprint filter strings)
        spawn_loc_list: list of strings (['up', 'down', 'left', 'right'])
        speed_list: list of float speeds
        buffer_duration_list: list of float buffers
        start_time: when to begin scheduling
    Returns:
        total simulation time needed
    """
    current_time = start_time
    index = 1
    schedule_log_path = os.path.join(sim.output_root, folder_name, 'all_vehicle_combinations.csv')
    os.makedirs(os.path.dirname(schedule_log_path), exist_ok=True)
    with open(schedule_log_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['index', 'vehicle_type', 'spawn_direction', 'spawn_time', 'speed', 'min_duration', 'buffer', 'spawn_x', 'spawn_y', 'velocity_x', 'velocity_y', 'yaw'])
        for vehicle_type, spawn_loc, speed, buffer_duration in itertools.product(vehicle_type_list, spawn_loc_list, speed_list, buffer_duration_list):
            loc, vel, yaw, min_duration = get_spawn_config(spawn_loc, speed)
            duration = min_duration + buffer_duration
            print(f'[{index}] Scheduling {vehicle_type} from {spawn_loc} at t={current_time:.2f}, speed={speed}, buffer={buffer_duration}')
            writer.writerow([index, vehicle_type, spawn_loc, round(current_time, 2), speed, round(min_duration, 3), buffer_duration, loc.x, loc.y, vel.x, vel.y, yaw])
            sim.schedule_vehicle(vehicle_type=vehicle_type, velocity=(vel.x, vel.y), spawn_pos=(loc.x, loc.y), spawn_yaw=yaw, spawn_time=current_time, duration=duration)
            current_time += duration
            index += 1
    return current_time + 2.0
