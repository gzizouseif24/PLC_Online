"""Panasonic driver tests against a simulated FP0R (no hardware needed).

    python -m unittest discover tests
"""
import unittest

from plc_online.checksum import xor_checksum
from plc_online.drivers.panasonic import (
    Fp0r, MewtocolError, PlcRefused, decode_words, dint, encode_word)


class FakeFp0r:
    """Answers MEWTOCOL frames like an FP0R, including its ~28-word reply limit."""

    REPLY_LIMIT = 28

    def __init__(self, station=1):
        self.station = station
        self.dt = {i: i * 3 for i in range(1000)}
        self.program = [0x1234 + i for i in range(25)]  # rest of memory reads FFFF
        self.corrupt_next = 0     # replies to send with a wrong BCC
        self.error_code = None    # answer %01!xx instead
        self.requests = []
        self._reply = b""

    def reset_input_buffer(self):
        self._reply = b""

    def write(self, data):
        frame = data.decode("ascii")
        assert frame.endswith("\r")
        body, bcc = frame[:-3], frame[-3:-1]
        assert xor_checksum(body) == bcc, "the driver sent a wrong BCC"
        head = f"%{self.station:02d}"
        cmd = body[len(head) + 1:]
        self.requests.append(cmd)
        if self.error_code:
            reply = f"{head}!{self.error_code}"
        elif cmd.startswith("RDD"):
            a, b = int(cmd[3:8]), int(cmd[8:13])
            n = min(b - a + 1, self.REPLY_LIMIT)  # cut silently, like the real PLC
            reply = f"{head}$RD" + "".join(encode_word(self.dt[a + i]) for i in range(n))
        elif cmd.startswith("WDD"):
            a = int(cmd[3:8])
            self.dt[a] = decode_words(cmd[13:17])[0]
            reply = f"{head}$WD"
        elif cmd == "RT":
            reply = f"{head}$RT" + "4B21" + "0120" + "0003" + "01"
        elif cmd.startswith("RP"):
            a, b = int(cmd[2:7]), int(cmd[7:12])
            steps = [self.program[i] if i < len(self.program) else 0xFFFF for i in range(a, b + 1)]
            reply = f"{head}$RP" + "".join(encode_word(w) for w in steps)
        else:
            raise AssertionError(f"unexpected command {cmd!r}")
        bcc = xor_checksum(reply)
        if self.corrupt_next:
            self.corrupt_next -= 1
            bcc = f"{int(bcc, 16) ^ 0xFF:02X}"
        self._reply = (reply + bcc + "\r").encode("ascii")

    def read_until(self, terminator):
        out, self._reply = self._reply, b""
        return out


class DecodingTests(unittest.TestCase):
    def test_words_are_little_endian(self):
        self.assertEqual(decode_words("D2040000"), [1234, 0])
        self.assertEqual(encode_word(1234), "D204")
        self.assertEqual(encode_word(-1), "FFFF")

    def test_dint_low_word_first(self):
        self.assertEqual(dint([0x0001, 0x0002], 0), 0x00020001)
        self.assertEqual(dint([0xFFFF, 0xFFFF], 0), -1)


class Fp0rTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFp0r()
        self.plc = Fp0r(self.fake, station=1)

    def test_long_read_is_split_and_complete(self):
        words = self.plc.read_dt(0, 200)
        self.assertEqual(words, [i * 3 for i in range(200)])
        self.assertTrue(all(int(c[8:13]) - int(c[3:8]) + 1 <= 24 for c in self.fake.requests))

    def test_cut_reply_is_rejected(self):
        # Bypass the block size to provoke the silent cut at 28 words.
        with self.assertRaisesRegex(MewtocolError, "coupée"):
            self.plc._transact("RDD0000000039", "RD", data_len=4 * 40)

    def test_bad_bcc_is_retried(self):
        self.fake.corrupt_next = 1
        self.assertEqual(self.plc.read_dt(10, 2), [30, 33])
        self.assertEqual(len(self.fake.requests), 2)

    def test_bad_bcc_every_time_fails(self):
        self.fake.corrupt_next = 99
        with self.assertRaisesRegex(MewtocolError, "BCC"):
            self.plc.read_dt(10, 2)
        self.assertEqual(len(self.fake.requests), 3)

    def test_plc_error_is_not_retried(self):
        self.fake.error_code = "61"
        with self.assertRaises(PlcRefused) as ctx:
            self.plc.read_dt(0, 1)
        self.assertEqual(ctx.exception.code, "61")
        self.assertEqual(len(self.fake.requests), 1)

    def test_write_then_read_back(self):
        self.plc.write_dt(914, 1234)
        self.assertEqual(self.plc.read_dt(914, 1), [1234])

    def test_write_is_not_retried(self):
        self.fake.corrupt_next = 1
        with self.assertRaises(MewtocolError):
            self.plc.write_dt(914, 1234)
        self.assertEqual(len(self.fake.requests), 1)

    def test_status(self):
        s = self.plc.read_status()
        self.assertEqual((s["model"], s["capacity_k"], s["mode"]), ("4B21", 3, "RUN"))

    def test_program_stops_at_ffff_fill(self):
        words = self.plc.read_program(5000, pause=0)
        self.assertEqual(words, self.fake.program)
        self.assertLess(len(self.fake.requests), 10)


if __name__ == "__main__":
    unittest.main()
