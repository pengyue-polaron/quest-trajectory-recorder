import math
import unittest

from quest_trajectory_recorder.receiver import parse_remote_text
from quest_trajectory_recorder.openteach_bridge import remote_to_openteach_frame


class ProtocolTest(unittest.TestCase):
    def test_parse_remote_text(self):
        msg = "absolute|1,2,3|0,0,0,1|False|1,2,2.9|1.1,2,3|1,1.9,3"
        parsed = parse_remote_text(msg)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["kind"], "absolute")
        self.assertEqual(parsed["position"], [1.0, 2.0, 3.0])
        self.assertEqual(parsed["rotation"], [0.0, 0.0, 0.0, 1.0])
        self.assertFalse(parsed["flag"])
        self.assertEqual(parsed["num_points"], 3)

    def test_point_axes_to_openteach_frame(self):
        # Observed APK point order: -Z, +X, -Y endpoints.
        msg = "absolute|1,2,3|0,0,0,1|False|1,2,2.9|1.1,2,3|1,1.9,3"
        frame = remote_to_openteach_frame(parse_remote_text(msg), "points")
        self.assertEqual(frame[0], [1.0, 2.0, 3.0])
        self.assertAlmostEqual(frame[1][0], 1.0)
        self.assertAlmostEqual(frame[2][1], 1.0)
        self.assertAlmostEqual(frame[3][2], 1.0)

    def test_quaternion_axes_identity(self):
        msg = "absolute|1,2,3|0,0,0,1|False|0,0,0|0,0,0|0,0,0"
        frame = remote_to_openteach_frame(parse_remote_text(msg), "quaternion")
        self.assertEqual(frame[0], [1.0, 2.0, 3.0])
        self.assertTrue(math.isclose(frame[1][0], 1.0))
        self.assertTrue(math.isclose(frame[2][1], 1.0))
        self.assertTrue(math.isclose(frame[3][2], 1.0))


if __name__ == "__main__":
    unittest.main()
