"""Focused schedule checks; no CARLA server or third-party dependencies needed."""
import math
import csv
from pathlib import Path
import random
import tempfile
import unittest

from helpers.multi_vehicle import stagger_schedule, stagger_visible_schedule, validate_settings, NoFeasibleSchedule
from helpers.visibility import measure_visible_overlap


class StaggerScheduleTests(unittest.TestCase):
    def assert_separated(self, durations, delta, fps=30):
        schedule = stagger_schedule(durations, delta, fps)
        for i, ((start, end), duration) in enumerate(zip(schedule, durations)):
            self.assertEqual(end - start, math.ceil(duration * fps))
            if i:
                previous_start, previous_end = schedule[i - 1]
                self.assertGreaterEqual(start - previous_start, math.ceil(delta * fps))
                self.assertGreaterEqual(end - previous_end, math.ceil(delta * fps))
        return schedule

    def test_shorter_later_car_does_not_exit_with_first_car(self):
        # Naive starts 0, 1, 2 would give exits 10, 8, 11, violating exit order.
        self.assertEqual(self.assert_separated([10, 7, 9], 1),
                         [(0, 300), (120, 330), (150, 420)])

    def test_equal_durations_have_exact_delta(self):
        self.assertEqual(self.assert_separated([10, 10, 10], 1),
                         [(0, 300), (30, 330), (60, 360)])

    def test_fractional_delta_rounds_up(self):
        self.assertEqual(self.assert_separated([1, 1], .05), [(0, 30), (2, 32)])

    def test_large_delta_and_one_car(self):
        self.assertEqual(self.assert_separated([1, 1], 5), [(0, 30), (150, 180)])
        self.assertEqual(self.assert_separated([1], 1), [(0, 30)])

    def test_invalid_car_count_and_delta(self):
        for cars in [0, -1, True, 1.5]:
            with self.subTest(cars=cars), self.assertRaises(ValueError):
                validate_settings(cars, 1)
        for delta in [0, -1, float('nan'), float('inf'), True, '1']:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                validate_settings(3, delta)

    def test_vehicle_count_has_no_artificial_cap(self):
        for count in [4, 6, 20, 100]:
            validate_settings(count, .01, 2)
            result = stagger_visible_schedule([100] * count, [(0, 3000)] * count, .01, 2)
            self.assertEqual(len(result), count)
            self.assertGreaterEqual(min(e for _, e in result)-max(s for s, _ in result), 60)

    def test_varied_durations_always_preserve_both_gaps(self):
        rng = random.Random(1)
        for _ in range(100):
            self.assert_separated([rng.uniform(.1, 20) for _ in range(3)], rng.uniform(.01, 3))


class VisibleOverlapTests(unittest.TestCase):
    def test_camera_windows_not_actor_lifetimes_control_overlap(self):
        windows = [(30, 150), (90, 210), (60, 180)]
        schedule = stagger_visible_schedule([10, 10, 10], windows, 1, 2)
        start = max(spawn + window[0] for (spawn, _), window in zip(schedule, windows))
        end = min(spawn + window[1] for (spawn, _), window in zip(schedule, windows))
        self.assertGreaterEqual(end - start, 60)
        for events in ([s for s, _ in schedule], [e for _, e in schedule]):
            ordered = sorted(events)
            self.assertTrue(all(b - a >= 30 for a, b in zip(ordered, ordered[1:])))

    def test_infeasible_overlap_does_not_extend_travel_durations(self):
        with self.assertRaises(NoFeasibleSchedule):
            stagger_visible_schedule([10, 10, 10], [(0, 60)] * 3, 1, 2)
        with self.assertRaises(NoFeasibleSchedule):
            stagger_visible_schedule([10], [None], 1, 2)

    def test_subframe_minimum_rounds_up_and_single_car_works(self):
        schedule = stagger_visible_schedule([1], [(2, 12)], .1, .05)
        self.assertEqual(schedule, [(0, 30)])
        with self.assertRaises(NoFeasibleSchedule):
            stagger_visible_schedule([1], [(2, 3)], .1, .05)

    def test_invalid_minimum(self):
        for minimum in [-1, float('inf'), float('nan'), True, '2']:
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                validate_settings(3, 1, minimum)

    def test_zero_minimum_preserves_existing_schedule(self):
        durations = [10, 7, 9]
        self.assertEqual(stagger_visible_schedule(durations, [], 1, 0),
                         stagger_schedule(durations, 1))

    def test_measurement_requires_continuous_visibility_of_every_car(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trajectory.csv'
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['frame', 'vehicle_index', 'fov_states'])
                for frame in [0, 1, 2, 3, 5, 6]:
                    for car in range(3):
                        states = [0, 0, 0, 0]
                        # A spawned but invisible car breaks the run at frame 2.
                        if (frame, car) != (2, 2):
                            states[car] = 1 if car == 0 else 2
                        writer.writerow([frame, car, states])
            result = measure_visible_overlap(path, 3, fps=10)
            self.assertEqual(result['overlap_frames'], 2)
            self.assertEqual(result['overlap_seconds'], .2)
            self.assertEqual(result['overlap_start_frame'], 0)
            self.assertEqual(result['overlap_end_frame'], 2)


class IndependentSettingsTests(unittest.TestCase):
    def test_recording_driving_count_and_pedestrians_are_independent(self):
        import runpy
        from helpers.generation import validate_generation
        settings = runpy.run_path(str(Path(__file__).parents[1] / '1_generate_videos.py'))
        for mode in ['separate', 'continuous']:
            for driving in ['fixed_velocity', 'agent']:
                for cars in [1, 4, 20]:
                    for pedestrians in [0, 20]:
                        with self.subTest(mode=mode, driving=driving, cars=cars, pedestrians=pedestrians):
                            validate_generation(dict(settings, MODE=mode, DRIVING_MODE=driving,
                                CARS_PER_SAMPLE=cars, PEDESTRIAN_COUNT=pedestrians))

    def test_overlap_can_be_measured_per_continuous_group(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rows.csv'
            with path.open('w', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['frame','vehicle_index','fov_states'])
                for frame in range(5):
                    for car in [3,4,5]:
                        writer.writerow([frame,car,[1,0,0,0]])
            self.assertEqual(measure_visible_overlap(path,3,30,range(3,6))['overlap_frames'],5)
            self.assertEqual(measure_visible_overlap(path,3,30,range(3))['overlap_frames'],0)


if __name__ == '__main__':
    unittest.main()
