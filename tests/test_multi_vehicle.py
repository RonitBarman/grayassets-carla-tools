"""Focused schedule checks; no CARLA server or third-party dependencies needed."""
import math
import random
import unittest

from helpers.multi_vehicle import stagger_schedule, validate_settings


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
        for cars in [0, 4, -1, True, 1.5]:
            with self.subTest(cars=cars), self.assertRaises(ValueError):
                validate_settings(cars, 1)
        for delta in [0, -1, float('nan'), float('inf'), True, '1']:
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                validate_settings(3, delta)

    def test_varied_durations_always_preserve_both_gaps(self):
        rng = random.Random(1)
        for _ in range(100):
            self.assert_separated([rng.uniform(.1, 20) for _ in range(3)], rng.uniform(.01, 3))


if __name__ == '__main__':
    unittest.main()
