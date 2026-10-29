"""Uni-Telway listener: stream parsing, on synthetic bytes and a frame captured on hardware."""
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ut_listen", Path(__file__).resolve().parents[1] / "scripts" / "tsx" / "ut_listen.py")
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)

# mirror answer captured on a real Uni-Telway bus (link address 1)
MIRROR = bytes.fromhex("10 02 01 08 20 00 fe 00 00 00 fb 00 34")
# length 0x10 = DLE, so it is doubled; BCC taken after doubling
LONG_DLE = bytes.fromhex("10 02 01 10 10 20 00 fe 00 00 00 37 00 b4 00 00 00 01 00 07 00 44")


class ParserTests(unittest.TestCase):
    def test_polls_and_eot(self):
        evs = ut.Parser().feed(bytes([0x10, 0x05, 0x01, 0x04, 0x10, 0x05, 0x02]))
        self.assertEqual(evs, [("poll", 1), ("eot",), ("poll", 2)])

    def test_captured_frame(self):
        (ev,) = ut.Parser().feed(MIRROR)
        self.assertEqual(ev[:4], ("frame", 1, bytes.fromhex("20 00 fe 00 00 00 fb 00"), True))

    def test_doubled_length(self):
        (ev,) = ut.Parser().feed(LONG_DLE)
        self.assertTrue(ev[3])
        self.assertEqual(len(ev[2]), 16)

    def test_bad_bcc(self):
        (ev,) = ut.Parser().feed(MIRROR[:-1] + b"\x35")
        self.assertFalse(ev[3])

    def test_split_across_reads(self):
        p = ut.Parser()
        evs = []
        for i in range(len(MIRROR)):
            evs += p.feed(MIRROR[i:i + 1])
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0][0], "frame")

    def test_stats_attribute_answers_to_polls(self):
        s = ut.Stats()
        for ev in ut.Parser().feed(bytes([0x10, 5, 1, 0x04, 0x10, 5, 2, 0x10, 5, 1]) + MIRROR):
            s.add(ev)
        self.assertEqual(s.polls, {1: 2, 2: 1})
        self.assertEqual(s.answers[1], {"EOT": 1, "trame": 1})
        self.assertEqual(s.answers[2], {"rien": 1})


if __name__ == "__main__":
    unittest.main()
