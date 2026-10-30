"""Uni-Telway slave test script against a simulated TSX master (no hardware)."""
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ut_test", Path(__file__).resolve().parents[1] / "research" / "tsx" / "ut_test.py")
ut = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ut)

# mirror answer captured on a real Uni-Telway bus (link address 1)
MIRROR_ANSWER = bytes.fromhex("10 02 01 08 20 00 fe 00 00 00 fb 00 34")


class FakeMaster:
    """Polls 1..8; echoes what the PC sends (2-wire RS-485); ACKs, then answers."""

    def __init__(self, answer=None, echo=True, nak=False):
        self.answer, self.echo, self.nak = answer, echo, nak
        self.out = bytearray()
        self.next_poll = 1
        self.received = []

    @property
    def in_waiting(self):
        return len(self.out)

    def reset_input_buffer(self):
        self.out.clear()

    def write(self, data):
        if self.echo:
            self.out += data
        if data[:2] == bytes([ut.DLE, ut.STX]):
            self.received.append(data)
            if self.nak:
                self.out.append(ut.NAK)
                return
            self.out.append(ut.ACK)
            if self.answer is not None:
                self.out += self.answer(data)

    def read(self, n):
        if not self.out:
            self.out += bytes([ut.DLE, ut.ENQ, self.next_poll])
            self.next_poll = self.next_poll % 8 + 1
        data, self.out = bytes(self.out[:n]), self.out[n:]
        return data


def mirror_answer(request):
    (ev,) = [e for e in ut.Parser().feed(request) if e[0] == "frame"]
    unite = ev[2][6:]
    return ut.build_frame(ev[1], [0x20, 0x00, 0xFE, 0x00, 0x00, 0x00, 0xFB, *unite[2:]])


class SlaveTests(unittest.TestCase):
    def test_build_frame_matches_capture(self):
        self.assertEqual(ut.build_frame(1, [0x20, 0, 0xFE, 0, 0, 0, 0xFB, 0x00]), MIRROR_ANSWER)

    def test_build_frame_doubles_dle(self):
        f = ut.build_frame(1, list(range(16)))  # length 0x10 and a data byte 0x10
        self.assertEqual(f[3:5], bytes([0x10, 0x10]))
        (ev,) = ut.Parser().feed(f)
        self.assertEqual(list(ev[2]), list(range(16)))
        self.assertTrue(ev[3])

    def test_mirror_round_trip_with_echo(self):
        master = FakeMaster(answer=mirror_answer, echo=True)
        slave = ut.Slave(master, addr=1)
        self.assertEqual(slave.unite(ut.MIRROR, list(b"PLC")), list(b"PLC"))
        sent = master.received[0]
        self.assertEqual(sent[:3], bytes([ut.DLE, ut.STX, 1]))

    def test_request_waits_for_own_address(self):
        master = FakeMaster(answer=mirror_answer, echo=False)
        master.next_poll = 2
        ut.Slave(master, addr=5).unite(ut.MIRROR, [1])
        self.assertEqual(master.received[0][2], 5)

    def test_no_answer_reports_step(self):
        master = FakeMaster(answer=None)
        slave = ut.Slave(master, addr=1)
        with self.assertRaisesRegex(ut.UniteError, "attente reponse"):
            slave.request([ut.MIRROR, 0, 1], timeout=0.2)

    def test_nak_three_times(self):
        slave = ut.Slave(FakeMaster(nak=True), addr=1)
        with self.assertRaisesRegex(ut.UniteError, "NAK"):
            slave.request([ut.MIRROR, 0, 1])

    def test_negative_answer(self):
        neg = lambda req: ut.build_frame(1, [0x20, 0, 0xFE, 0, 0, 0, 0xFD])
        with self.assertRaisesRegex(ut.UniteError, "negatif"):
            ut.Slave(FakeMaster(answer=neg), addr=1).unite(ut.STATUS)


if __name__ == "__main__":
    unittest.main()
