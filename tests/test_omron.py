"""Omron Host Link driver tests against a simulated CJ1M (no hardware needed)."""
import unittest

from plc_online.checksum import xor_checksum
from plc_online.drivers.omron import Cj1m, HostLinkError, PlcRefused, decode_words, signed16


class FakeCj1m:
    def __init__(self, unit=0):
        self.unit = unit
        self.dm = {i: (0xF5BD if i == 0 else i * 7) for i in range(1000)}
        self.corrupt_next = 0
        self.end_code = "00"
        self.cut_to = None   # return fewer words than asked
        self.requests = []
        self._reply = b""

    def reset_input_buffer(self):
        self._reply = b""

    def write(self, data):
        frame = data.decode("ascii")
        assert frame.endswith("*\r")
        body, fcs = frame[:-4], frame[-4:-2]
        assert xor_checksum(body) == fcs, "the driver sent a wrong FCS"
        header = body[3:5]
        self.requests.append(body)
        if header == "TS":
            self._reply = data
            return
        assert header == "RD"
        start, count = int(body[5:9]), int(body[9:13])
        if self.cut_to:
            count = self.cut_to
        reply = f"@{self.unit:02d}RD{self.end_code}"
        if self.end_code == "00":
            reply += "".join(f"{self.dm[start + i]:04X}" for i in range(count))
        fcs = xor_checksum(reply)
        if self.corrupt_next:
            self.corrupt_next -= 1
            fcs = f"{int(fcs, 16) ^ 0xFF:02X}"
        self._reply = (reply + fcs + "*\r").encode("ascii")

    def read_until(self, terminator):
        out, self._reply = self._reply, b""
        return out


class OmronTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCj1m()
        self.plc = Cj1m(self.fake, unit=0)

    def test_words_are_msd_first(self):
        self.assertEqual(decode_words("04D2F5BD"), [1234, 0xF5BD])
        self.assertEqual(signed16(0xF5BD), -2627)  # value seen in CX-Programmer

    def test_frame_matches_report(self):
        self.plc.read_dm(0, 4)
        self.assertEqual(self.fake.requests[0], "@00RD00000004")

    def test_echo(self):
        self.assertTrue(self.plc.test())

    def test_long_read_is_split(self):
        self.assertEqual(self.plc.read_dm(0, 75)[1:], [i * 7 for i in range(1, 75)])
        self.assertEqual(len(self.fake.requests), 3)

    def test_bad_fcs_is_retried(self):
        self.fake.corrupt_next = 1
        self.assertEqual(self.plc.read_dm(1, 1), [7])
        self.assertEqual(len(self.fake.requests), 2)

    def test_short_reply_is_rejected(self):
        self.fake.cut_to = 2
        with self.assertRaisesRegex(HostLinkError, "coupée"):
            self.plc.read_dm(0, 4)

    def test_end_code_is_not_retried(self):
        self.fake.end_code = "15"
        with self.assertRaises(PlcRefused) as ctx:
            self.plc.read_dm(0, 4)
        self.assertEqual(ctx.exception.code, "15")
        self.assertEqual(len(self.fake.requests), 1)


if __name__ == "__main__":
    unittest.main()
