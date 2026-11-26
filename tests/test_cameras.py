"""Camera selection and coverage checks without launching CARLA."""
from pathlib import Path
import random
import runpy
import unittest

from helpers.cameras import DEFAULT_CAMERA_POSES, select_camera_poses, validate_camera_configs


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.settings = runpy.run_path(str(Path(__file__).parents[1] / '1_generate_videos.py'))
        state = random.getstate()
        self.addCleanup(random.setstate, state)

    def test_fixed_defaults_preserve_views_and_random_state(self):
        state = random.getstate()
        self.assertEqual(select_camera_poses(self.settings['CAMERA_CONFIGS']), DEFAULT_CAMERA_POSES)
        self.assertEqual(random.getstate(), state)

    def test_different_option_counts_and_independent_repeatable_draws(self):
        configs = [dict(positions=[(i, j, 10) for j in range(i + 1)],
                        rotations=[(-60, j, 0) for j in range(5 - i)]) for i in range(4)]
        random.seed(42)
        recordings = [select_camera_poses(configs, True) for _ in range(30)]
        random.seed(42)
        self.assertEqual(recordings, [select_camera_poses(configs, True) for _ in range(30)])
        for i, config in enumerate(configs):
            self.assertEqual({poses[i][0] for poses in recordings}, set(config['positions']))
            self.assertEqual({poses[i][1] for poses in recordings}, set(config['rotations']))
        # Camera 2 has two positions and four rotations: combinations aren't paired.
        self.assertGreater(len({poses[1] for poses in recordings}), 4)

    def test_empty_or_invalid_pose_options_fail_before_generation(self):
        for key, values in [('positions', []), ('rotations', [(0, 0)]),
                            ('positions', [(0, 0, float('nan'))])]:
            configs = [dict(c) for c in self.settings['CAMERA_CONFIGS']]
            configs[0][key] = values
            with self.subTest(key=key, values=values), self.assertRaises(ValueError):
                validate_camera_configs(configs, True)

    def test_coverage_prediction_uses_selected_poses_including_cache(self):
        from helpers.visibility import predict_visible_window
        vehicle = dict(vehicle_type='vehicle.tesla.model3', spawn_x=-50, spawn_y=20,
                       vx=1, vy=0, yaw=0, duration=1)
        far_away = tuple(((x + 10000, y, z), rotation)
                         for (x, y, z), rotation in DEFAULT_CAMERA_POSES)
        original = predict_visible_window(vehicle)
        self.assertIsNotNone(original)
        self.assertIsNone(predict_visible_window(vehicle, camera_poses=far_away))
        self.assertEqual(predict_visible_window(vehicle), original)


if __name__ == '__main__':
    unittest.main()
