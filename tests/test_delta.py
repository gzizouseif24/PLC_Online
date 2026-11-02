import struct
import unittest

from plc_online.protocols import delta
from plc_online.protocols.modbus import AsciiTransport, PlcRefused, lrc


class FakeDvp:
    """Serial port answering Modbus ASCII like a DVP (coils + holding registers by address)."""

    def __init__(self, station=1, d_size=10000):
        self.station, self.coils, self.regs, self.buf = station, {}, {}, b""
        self.d_size = d_size

    def reset_input_buffer(self):
        self.buf = b""

    def _valid(self, addr):
        d = addr - 0x1000 if 0x1000 <= addr < 0x2000 else (addr - 0x9000 + 4096 if addr >= 0x9000 else None)
        return d is None or d < self.d_size

    def write(self, frame):
        data = bytes.fromhex(frame[1:-2].decode())
        assert lrc(data[:-1]) == data[-1]
        unit, func = data[0], data[1]
        if unit != self.station:
            return
        addr, n = struct.unpack(">HH", data[2:6])
        if func == 3:
            if not all(self._valid(a) for a in range(addr, addr + n)):
                pdu = bytes([0x83, 2])
            else:
                vals = [self.regs.get(a, 0) for a in range(addr, addr + n)]
                pdu = bytes([3, 2 * n]) + struct.pack(f">{n}H", *vals)
        elif func == 1:
            raw = bytearray((n + 7) // 8)
            for i in range(n):
                if self.coils.get(addr + i):
                    raw[i // 8] |= 1 << (i % 8)
            pdu = bytes([1, len(raw)]) + raw
        elif func == 6:
            self.regs[addr] = n
            pdu = data[1:6]
        elif func == 16:
            vals = struct.unpack(f">{n}H", data[7:7 + 2 * n])
            for i, v in enumerate(vals):
                self.regs[addr + i] = v
            pdu = data[1:6]
        elif func == 5:
            self.coils[addr] = n == 0xFF00
            pdu = data[1:6]
        body = bytes([unit]) + pdu
        self.buf = b":" + (body + bytes([lrc(body)])).hex().upper().encode() + b"\r\n"

    def read_until(self, end):
        out, self.buf = self.buf, b""
        return out

    def close(self):
        pass


class DeltaTest(unittest.TestCase):
    def setUp(self):
        self.port = FakeDvp()
        self.plc = delta.Delta(AsciiTransport(self.port), 1)

    def test_addresses(self):
        self.assertEqual(delta.parse_address("x17"), ("X", 15))
        self.assertEqual(delta.bit_address("X", 15), 0x040F)
        self.assertEqual(delta.bit_address("M", 1000), 0x0BE8)
        self.assertEqual(delta.bit_address("M", 1536), 0xB000)
        self.assertEqual(delta.word_address("D", 4096), 0x9000)
        self.assertEqual(delta.name("Y", 10), "Y12")
        with self.assertRaises(delta.DeltaError):
            delta.parse_address("X8")

    def test_read_words_crossing_4096(self):
        self.port.regs[0x1000 + 4095] = 7
        self.port.regs[0x9000] = 8
        self.assertEqual(self.plc.read_words("D", 4095, 2), [7, 8])

    def test_write_and_status(self):
        self.plc.write_words("D", 1313, [1, 2, 3, 4, 5, 6, 26])
        self.assertEqual(self.plc.clock(), [1, 2, 3, 4, 5, 6, 26])
        self.port.coils[0x0800 + 1004] = True
        self.port.regs[0x1000 + 1004] = 0xC4EE
        st = self.plc.status()
        self.assertTrue(st["bits"][1004])
        self.assertEqual(delta.error_text(st["mots"][1004]), "pas d'instruction END dans le programme")

    def test_refused(self):
        self.port.d_size = 600
        with self.assertRaises(PlcRefused):
            self.plc.read_words("D", 590, 20)

    def test_read_all_d_stops_at_end(self):
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location(
            "delta_tool", pathlib.Path(__file__).resolve().parents[1] / "tools" / "delta.py")
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        self.port.d_size = 250
        self.assertEqual(len(tool.read_all_d(self.plc, show=False)), 250)


if __name__ == "__main__":
    unittest.main()
