"""Modbus driver tests against a simulated Modicon, over RTU and TCP (no hardware)."""
import struct
import unittest

from plc_online.protocols.modbus import (
    Modicon, ModbusError, PlcRefused, RtuTransport, TcpTransport,
    crc16, dint_lw, real_lw)


class FakeSlave:
    """Answers Modbus PDUs like a Modicon with 1000 %MW and 1000 %M."""

    def __init__(self):
        self.mw = [i * 5 for i in range(1000)]
        self.m = [i % 3 == 0 for i in range(1000)]
        self.requests = []

    def answer(self, pdu: bytes) -> bytes:
        self.requests.append(pdu)
        func = pdu[0]
        if func in (1, 2, 3, 4):
            start, count = struct.unpack(">HH", pdu[1:5])
            if start + count > 1000:
                return bytes([func | 0x80, 2])
            if func in (3, 4):
                data = struct.pack(f">{count}H", *self.mw[start:start + count])
            else:
                data = bytearray((count + 7) // 8)
                for i in range(count):
                    if self.m[start + i]:
                        data[i // 8] |= 1 << (i % 8)
            return bytes([func, len(data)]) + bytes(data)
        if func == 6:
            addr, value = struct.unpack(">HH", pdu[1:5])
            self.mw[addr] = value
            return pdu
        return bytes([func | 0x80, 1])


class FakeSerial:
    def __init__(self, slave, unit=1):
        self.slave, self.unit = slave, unit
        self.corrupt_next = 0
        self.buf = b""

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, adu):
        body, crc = adu[:-2], struct.unpack("<H", adu[-2:])[0]
        assert crc16(body) == crc, "the driver sent a wrong CRC"
        reply = bytes([body[0]]) + self.slave.answer(body[1:])
        crc = crc16(reply)
        if self.corrupt_next:
            self.corrupt_next -= 1
            crc ^= 0xFFFF
        self.buf = reply + struct.pack("<H", crc)

    def read(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass


class FakeSocket:
    def __init__(self, slave):
        self.slave = slave
        self.buf = b""

    def sendall(self, adu):
        tid, proto, length, unit = struct.unpack(">HHHB", adu[:7])
        assert proto == 0 and length == len(adu) - 6
        pdu = self.slave.answer(adu[7:])
        self.buf = struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu

    def recv(self, n):
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def close(self):
        pass


class HelperTests(unittest.TestCase):
    def test_crc_matches_spec_example(self):
        # 01 03 00 00 00 0A -> CRC bytes C5 CD
        self.assertEqual(struct.pack("<H", crc16(bytes.fromhex("01030000000A"))), bytes.fromhex("C5CD"))

    def test_schneider_32bit_low_word_first(self):
        self.assertEqual(dint_lw([0x0001, 0x0002], 0), 0x00020001)
        self.assertEqual(dint_lw([0xFFFF, 0xFFFF], 0), -1)
        lo, hi = struct.unpack("<HH", struct.pack("<f", 12.5))
        self.assertEqual(real_lw([lo, hi], 0), 12.5)


class RtuTests(unittest.TestCase):
    def setUp(self):
        self.slave = FakeSlave()
        self.ser = FakeSerial(self.slave)
        self.plc = Modicon(RtuTransport(self.ser), unit=1)

    def test_read_holding_split_at_125(self):
        self.assertEqual(self.plc.read_holding(0, 300), [i * 5 for i in range(300)])
        self.assertEqual(len(self.slave.requests), 3)

    def test_read_coils(self):
        self.assertEqual(self.plc.read_coils(0, 10), [i % 3 == 0 for i in range(10)])

    def test_bad_crc_is_retried(self):
        self.ser.corrupt_next = 1
        self.assertEqual(self.plc.read_holding(4, 2), [20, 25])
        self.assertEqual(len(self.slave.requests), 2)

    def test_bad_crc_every_time_fails(self):
        self.ser.corrupt_next = 99
        with self.assertRaisesRegex(ModbusError, "CRC"):
            self.plc.read_holding(4, 2)

    def test_exception_is_not_retried(self):
        with self.assertRaises(PlcRefused) as ctx:
            self.plc.read_holding(990, 20)
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(len(self.slave.requests), 1)

    def test_write_then_read_back(self):
        self.plc.write_register(7, 1234)
        self.assertEqual(self.plc.read_holding(7, 1), [1234])

    def test_no_answer(self):
        self.ser.write = lambda adu: None
        with self.assertRaisesRegex(ModbusError, "timeout"):
            self.plc.read_holding(0, 1)


class TcpTests(unittest.TestCase):
    def setUp(self):
        self.slave = FakeSlave()
        self.plc = Modicon(TcpTransport("unused", sock=FakeSocket(self.slave)), unit=1)

    def test_read_holding(self):
        self.assertEqual(self.plc.read_holding(10, 3), [50, 55, 60])

    def test_read_coils_across_bytes(self):
        self.assertEqual(self.plc.read_coils(5, 12), [(5 + i) % 3 == 0 for i in range(12)])

    def test_exception(self):
        with self.assertRaises(PlcRefused):
            self.plc.read_coils(995, 10)


if __name__ == "__main__":
    unittest.main()
