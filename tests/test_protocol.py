import unittest

from quest_trajectory_recorder.receiver import parse_remote_text


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

if __name__ == "__main__":
    unittest.main()
