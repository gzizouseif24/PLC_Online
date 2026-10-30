"""Omron Host Link driver tests against a simulated CJ1M (no hardware needed)."""
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from plc_online.checksum import xor_checksum
from plc_online.protocols.hostlink import (
    AREAS, Cj1m, FinsRefused, HostLinkError, PlcRefused, decode_words, error_log_text, parse_address, signed16)

CMODE_READ = {"RR": "CIO", "RH": "H", "RJ": "A", "RD": "D"}
CMODE_WRITE = {"WR": "CIO", "WH": "H", "WJ": "A", "WD": "D"}
FINS_AREA = {0xB0: "CIO", 0xB1: "W", 0xB2: "H", 0xB3: "A", 0x82: "D"}
MAX_FRAME = 131


class Refuse(Exception):
    pass


def bcd(n):
    return (n // 10) << 4 | n % 10


class FakeCj1m:
    """Answers like a CJ1M: C-mode commands, FINS in 'FA' frames, replies over 131 characters
    split in several frames (each intermediate frame waits for a CR from the host)."""

    def __init__(self, unit=0, d_size=32768):
        self.unit = unit
        self.mem = {area: [0] * spec["size"] for area, spec in AREAS.items()}
        self.mem["D"] = [(0xF5BD if i == 0 else i * 7 & 0xFFFF) for i in range(d_size)]
        self.mode = 0x04
        self.fatal, self.non_fatal = 0, 0
        self.program = bytes(range(256)) * 4 + b"END"
        self.program_unit = 2          # bytes per program address
        self.errors = [(0x80C0, 0, (26, 10, 30, 14, 5, 7)), (0x4105, 0, (26, 10, 30, 14, 6, 0))]
        self.corrupt_next = 0
        self.fa_end_code = True    # bench CJ1M: '@00FA00 40...' (False: W342 diagram, no end code)
        self.battery = True
        self.setup = [0] * 512
        self.setup[128] = 0x8000              # battery errors not detected
        self.setup[144] = 0x8000              # peripheral port: PLC Setup, Host Link, 7 bits, 2 stop, even
        self.io_table = [0] * 1280
        self.io_table[0:4] = [0x0001, 0x1100, 0x0001, 0x1200]   # made-up content
        self.unit_list = [(0x00, b"CJ1M_CPU11")]
        self.end_code = "00"
        self.cut_to = None   # return fewer words than asked
        self.requests = []
        self._out = []

    # serial port side
    def reset_input_buffer(self):
        self._out = []

    def write(self, data):
        if data == b"\r":                     # host asks for the next frame
            return
        frame = data.decode("ascii")
        assert frame.endswith("*\r")
        body, fcs = frame[:-4], frame[-4:-2]
        assert xor_checksum(body) == fcs, "the driver sent a wrong FCS"
        assert len(frame) <= MAX_FRAME, "command frame too long"
        self.requests.append(body)
        header, text = body[3:5], body[5:]
        if header == "TS":
            self._out = [data]
            return
        reply = f"@{self.unit:02d}{header}"
        try:
            if self.end_code != "00":
                raise Refuse(self.end_code)
            answer = self.answer(header, text)
            # bench CJ1M sends '00' after 'FA'; the W342 §3-5-3 diagram shows none
            reply += answer if header == "FA" and not self.fa_end_code else "00" + answer
        except Refuse as e:
            reply += str(e)
        self._send(reply)

    def read_until(self, terminator):
        return self._out.pop(0) if self._out else b""

    def _send(self, text):
        frames = []
        first = True
        while True:
            room = (MAX_FRAME if first else 128) - 4
            if len(text) <= room:
                frames.append(text + xor_checksum(text) + "*\r")
                break
            part, text = text[:room + 1], text[room + 1:]
            frames.append(part + xor_checksum(part) + "\r")
            first = False
        if self.corrupt_next:
            self.corrupt_next -= 1
            f = frames[0]
            frames[0] = f[:-4] + f"{int(f[-4:-2], 16) ^ 0xFF:02X}" + f[-2:]
        self._out = [f.encode("ascii") for f in frames]

    # PLC side
    def answer(self, header, text):
        if header in CMODE_READ:
            area, start, count = CMODE_READ[header], int(text[0:4]), int(text[4:8])
            if self.cut_to:
                count = self.cut_to
            if start + count > len(self.mem[area]):
                raise Refuse("04")
            return "".join(f"{w:04X}" for w in self.mem[area][start:start + count])
        if header in CMODE_WRITE:
            if self.mode == 0x04:
                raise Refuse("01")
            area, start = CMODE_WRITE[header], int(text[0:4])
            for i, w in enumerate(decode_words(text[4:])):
                self.mem[area][start + i] = w
            return ""
        if header == "SC":
            self.mode = {"00": 0x00, "02": 0x02, "03": 0x04}[text]
            return ""
        if header == "MM":
            return "30"
        if header == "MS":
            ms_mode = {0x00: 0b00, 0x04: 0b10, 0x02: 0b11}[self.mode]
            return f"{ms_mode << 8 | 0x30:04X}"
        if header == "FA":
            assert text[0:9] == "000000000"
            cmd, params = text[9:13], text[13:]
            code, data = self.fins(cmd, params)
            return f"40000000{cmd}{code:04X}{data}"
        raise Refuse("IC")

    def fins(self, cmd, p):
        if cmd in ("0101", "0102"):
            area_code, addr, count = int(p[0:2], 16), int(p[2:6], 16), int(p[8:12], 16)
            if area_code == 0x89:
                area, addr = ("C", addr - 0x8000) if addr >= 0x8000 else ("T", addr)
            else:
                area = FINS_AREA[area_code]
            if addr + count > len(self.mem[area]):
                return 0x1104, ""
            if cmd == "0101":
                return 0x0040 if self.non_fatal else 0, "".join(f"{w:04X}" for w in self.mem[area][addr:addr + count])
            for i, w in enumerate(decode_words(p[12:])):
                self.mem[area][addr + i] = w
            return 0, ""
        if cmd == "0201":
            area, start, count = int(p[0:4], 16), int(p[4:8], 16), int(p[8:12], 16)
            table = {0x8010: self.setup, 0x8012: self.io_table}[area]
            words = table[start:start + count]
            last = 0x8000 if start + len(words) >= len(table) else 0
            code = 0x1104 if start + count > len(table) else 0
            return code, f"{area:04X}{start:04X}{len(words) | last:04X}" + "".join(f"{w:04X}" for w in words)
        if cmd == "0502":
            first, count = int(p[0:2], 16), int(p[2:4], 16)
            units = [u for u in self.unit_list if u[0] >= first][:count]
            raw = bytes([first, len(units) | 0x80])
            for addr, model in units:
                raw += bytes([addr]) + model.ljust(20, b" ")
            return 0, raw.hex().upper()
        if cmd == "0501":
            return 0, (b"CJ1M-CPU12".ljust(20, b" ") + b"02.00".ljust(20, b" ") + bytes(40)).hex().upper()
        if cmd == "0601":
            status = (0x01 if self.mode else 0x00) | (0x04 if self.battery else 0)
            raw = bytes([status, self.mode]) + self.fatal.to_bytes(2, "big") + self.non_fatal.to_bytes(2, "big")
            raw += bytes(2) + (0x4105).to_bytes(2, "big") + b"DEFAUT CONVOYEUR"
            return 0, raw.hex().upper()
        if cmd == "0701":
            return 0, bytes([bcd(26), bcd(10), bcd(30), bcd(14), bcd(5), bcd(9), 5]).hex().upper()
        if cmd == "2102":
            first, count = int(p[0:4], 16), int(p[4:8], 16)
            if first >= len(self.errors):
                return 0x1103, ""            # W342: command and end code only
            recs = self.errors[first:first + count]
            raw = (20).to_bytes(2, "big") + len(self.errors).to_bytes(2, "big") + len(recs).to_bytes(2, "big")
            for c1, c2, (y, mo, d, h, mi, s) in recs:
                raw += c1.to_bytes(2, "big") + c2.to_bytes(2, "big") + bytes(bcd(x) for x in (mi, s, d, h, y, mo))
            return (0x1104 if len(recs) < count else 0), raw.hex().upper()
        if cmd == "0306":
            assert p[0:4] == "FFFF"
            addr, count = int(p[4:12], 16), int(p[12:16], 16)
            assert addr % 4 == 0 and count % 4 == 0 and count <= 528, "W342: multiples of 4, 538-byte reply max"
            start = addr * self.program_unit
            if start >= len(self.program):
                return 0x1104, ""
            chunk = self.program[start:start + count]
            last = 0x8000 if start + count >= len(self.program) else 0
            return 0, f"FFFF{addr:08X}{len(chunk) | last:04X}" + chunk.hex().upper()
        return 0x0401, ""


class OmronTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCj1m()
        self.plc = Cj1m(self.fake, unit=0)

    # C-mode, as validated on the bench
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

    # other areas
    def test_cmode_areas(self):
        self.fake.mem["CIO"][1] = 0x0005
        self.fake.mem["H"][3] = 42
        self.fake.mem["A"][402] = 0x0010
        self.assertEqual(self.plc.read("CIO", 0, 2), [0, 5])
        self.assertEqual(self.plc.read("H", 3, 1), [42])
        self.assertEqual(self.plc.read("A", 402, 1), [0x10])
        self.assertEqual([r[3:5] for r in self.fake.requests], ["RR", "RH", "RJ"])
        self.assertEqual(self.fake.requests[0], "@00RR00000002")

    def test_fins_areas(self):
        self.fake.mem["W"][10] = 0x8001
        self.fake.mem["T"][5] = 300
        self.fake.mem["C"][2] = 17
        self.assertEqual(self.plc.read("W", 10, 1), [0x8001])
        self.assertEqual(self.plc.read("T", 5, 1), [300])
        self.assertEqual(self.plc.read("C", 2, 1), [17])
        self.assertEqual(self.fake.requests[0], "@00FA0000000000101B1000A000001")
        self.assertIn("0101898002000001", self.fake.requests[2])

    def test_high_dm_uses_fins(self):
        self.assertEqual(self.plc.read("D", 20000, 3), [(20000 + i) * 7 & 0xFFFF for i in range(3)])
        self.assertIn("FA", self.fake.requests[0][3:5])

    def test_fins_long_read_is_split(self):
        self.assertEqual(len(self.plc.read("W", 0, 60)), 60)
        self.assertEqual(len(self.fake.requests), 3)   # 26 + 26 + 8

    def test_fins_cpu_warning_bit_is_not_a_failure(self):
        self.fake.non_fatal = 0x0010     # battery: the reply code carries 0x0040
        self.assertEqual(self.plc.read("W", 0, 1), [0])

    def test_fins_refusal(self):
        with self.assertRaises(FinsRefused) as ctx:
            self.plc.fins("0101", "B1FFFF000001")
        self.assertEqual(ctx.exception.code, 0x1104)

    def test_out_of_range_is_refused_before_sending(self):
        with self.assertRaises(ValueError):
            self.plc.read("W", 510, 5)
        self.assertEqual(self.fake.requests, [])

    # writes and mode
    def test_write_refused_in_run(self):
        with self.assertRaises(PlcRefused) as ctx:
            self.plc.write("D", 100, [1234])
        self.assertEqual(ctx.exception.code, "01")

    def test_write_in_monitor(self):
        self.plc.set_mode("monitor")
        self.assertEqual(self.fake.requests[-1], "@00SC02")
        self.plc.write("D", 100, [-2])
        self.assertEqual(self.fake.requests[-1], "@00WD0100FFFE")
        self.assertEqual(self.plc.read("D", 100, 1), [0xFFFE])
        self.plc.write("W", 3, [7])
        self.assertEqual(self.plc.read("W", 3, 1), [7])

    def test_long_write_is_split(self):
        self.plc.set_mode("monitor")
        self.plc.write("D", 0, list(range(40)))
        self.assertEqual(self.plc.read("D", 0, 40), list(range(40)))

    # status, clock, error log
    def test_multi_frame_reply(self):
        cd = self.plc.controller_data()
        self.assertEqual(cd, {"modele": "CJ1M-CPU12", "version": "02.00"})

    def test_status(self):
        self.fake.non_fatal = 0x0010
        s = self.plc.status()
        self.assertEqual(s["mode"], "RUN")
        self.assertEqual(s["non_fatal"], 0x0010)
        self.assertEqual(s["code_erreur"], 0x4105)
        self.assertTrue(s["en_cours"])
        self.assertTrue(s["pile_presente"])
        self.assertEqual(s["message"], "DEFAUT CONVOYEUR")

    def test_fa_reply_as_drawn_in_the_manual_is_accepted_too(self):
        self.fake.fa_end_code = False
        self.assertEqual(self.plc.read("W", 0, 1), [0])
        self.assertEqual(self.plc.status()["mode"], "RUN")

    def test_ms_fallback(self):
        self.assertEqual(self.plc.status_cmode()["mode"], "RUN")
        self.plc.set_mode("monitor")
        self.assertEqual(self.plc.status_cmode()["mode"], "MONITOR")

    def test_empty_error_log(self):
        self.fake.errors = []
        self.assertEqual(self.plc.error_log(), [])

    def test_area_a_fallbacks(self):
        a = self.fake.mem["A"]
        a[400:403] = [0x80C0, 0x4000, 0x0010]
        a[351:355] = [0x0509, 0x3014, 0x2610, 0x0005]       # 14:05:09, 30/10/2026, Friday
        a[300] = 2
        a[100:110] = [0x80C0, 0, 0x0507, 0x3014, 0x2610, 0x4105, 0, 0x0600, 0x3014, 0x2610]
        s = self.plc.status_area_a()
        self.assertEqual((s["mode"], s["fatal"], s["non_fatal"], s["code_erreur"]), (0x04, 0x4000, 0x0010, 0x80C0))
        self.assertEqual(self.plc.clock_area_a(), {"date": "30/10/2026", "heure": "14:05:09", "jour": 5})
        log = self.plc.error_log_area_a()
        self.assertEqual(log[0], {"code": 0x80C0, "code2": 0, "date": "30/10/2026 14:05:07"})
        self.assertEqual(log[1]["code"], 0x4105)

    def test_read_only_auxiliary_area_is_refused(self):
        self.plc.set_mode("monitor")
        with self.assertRaisesRegex(ValueError, "lecture seule"):
            self.plc.write("A", 402, [0])
        self.plc.write("A", 500, [1])

    def test_clock(self):
        self.assertEqual(self.plc.clock(), {"date": "30/10/2026", "heure": "14:05:09", "jour": 5})

    def test_error_log(self):
        self.fake.errors *= 3                    # 6 records: two requests of 4
        log = self.plc.error_log()
        self.assertEqual(len(log), 6)
        self.assertEqual(log[0], {"code": 0x80C0, "code2": 0, "date": "30/10/2026 14:05:07"})
        self.assertEqual(error_log_text(0x80C0), "erreur bus E/S")
        self.assertEqual(error_log_text(0x4105), "FAL 5")

    def test_parameter_area_is_read_in_blocks(self):
        self.fake.setup[511] = 0x1234
        setup = self.plc.plc_setup()
        self.assertEqual(len(setup), 512)
        self.assertEqual(setup[511], 0x1234)
        self.assertEqual(len(self.plc.io_table()), 1280)

    # program
    def test_program_read(self):
        for unit in (1, 2, 4):
            self.fake.program_unit = unit
            self.assertEqual(self.plc.read_program(block=100), self.fake.program)
            self.assertEqual(self.plc.read_program(), self.fake.program)

    # addresses
    def test_parse_address(self):
        self.assertEqual(parse_address("D100"), ("D", 100, None))
        self.assertEqual(parse_address("0.03"), ("CIO", 0, 3))
        self.assertEqual(parse_address("CIO 1.15"), ("CIO", 1, 15))
        self.assertEqual(parse_address("w10.05"), ("W", 10, 5))
        self.assertEqual(parse_address("A402.04"), ("A", 402, 4))
        self.assertEqual(parse_address("C5"), ("C", 5, None))
        for bad in ("D100.1", "W1.16", "X5", "W600"):
            with self.assertRaises(ValueError):
                parse_address(bad)


class OpenPortTests(unittest.TestCase):
    """Windows keeps a USB serial port busy for a moment after it is closed."""

    def fake_serial(self, busy_times):
        import sys
        import types
        mod = types.ModuleType("serial")

        class SerialException(Exception):
            pass

        calls = []

        def Serial(*a, **k):
            calls.append(1)
            if len(calls) <= busy_times:
                raise SerialException("could not open port 'COM6': PermissionError(13, 'Accès refusé.', None, 5)")
            return "port"

        mod.SerialException, mod.Serial = SerialException, Serial
        old = sys.modules.get("serial")
        self.addCleanup(lambda: sys.modules.__setitem__("serial", old) if old else sys.modules.pop("serial", None))
        sys.modules["serial"] = mod
        return calls

    def test_busy_port_is_retried(self):
        from plc_online.protocols.hostlink import open_port
        calls = self.fake_serial(busy_times=2)
        self.assertEqual(open_port("COM6", wait=2), "port")
        self.assertEqual(len(calls), 3)

    def test_port_still_busy_gives_a_clear_message(self):
        from plc_online.protocols.hostlink import open_port
        self.fake_serial(busy_times=100)
        with self.assertRaisesRegex(HostLinkError, "port occupé"):
            open_port("COM6", wait=0.5)


def load_cli():
    path = Path(__file__).resolve().parents[1] / "tools" / "omron.py"
    spec = importlib.util.spec_from_file_location("omron_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OmronCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.fake = FakeCj1m(d_size=1000)
        self.plc = Cj1m(self.fake)

    def run_cmd(self, fn, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            fn(self.plc, *args)
        return out.getvalue()

    def test_etat(self):
        self.fake.fatal = 0x4000
        out = self.run_cmd(self.cli.cmd_etat)
        self.assertIn("CJ1M-CPU12", out)
        self.assertIn("Mode : RUN", out)
        self.assertIn("erreur bus E/S", out)
        self.assertIn("30/10/2026 14:05:09", out)
        self.assertIn("2 enregistrement", out)

    def test_etat_without_fins(self):
        self.fake.fins = lambda cmd, p: (0x0401, "")      # FINS not supported
        self.fake.mem["A"][401] = 0x4000
        self.fake.mem["A"][351:354] = [0x0509, 0x3014, 0x2610]
        out = self.run_cmd(self.cli.cmd_etat)
        self.assertIn("Mode : RUN", out)
        self.assertIn("erreur bus E/S", out)
        self.assertIn("30/10/2026 14:05:09   (lue dans A351", out)
        self.assertIn("code modele 30", out)

    def test_parametres(self):
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "p.txt")
            out = self.run_cmd(self.cli.cmd_parametres, name)
            saved = Path(name).read_text(encoding="utf-8")
        self.assertIn("Host Link, 9600 bauds, 7 bits, parite paire, 2 stop, unite 0", out)
        self.assertIn("Detection defaut pile (+128.15)", out)
        self.assertIn("NON (desactivee)", out)
        self.assertIn("CJ1M_CPU11", out)
        self.assertIn("4 mots non nuls", out)
        self.assertIn("+128   8000", saved)
        self.assertIn("+0     0001 1100 0001 1200", saved)

    def test_lire_shows_bits(self):
        self.fake.mem["CIO"][0] = 0b101
        out = self.run_cmd(self.cli.cmd_lire, "CIO", 0, 1)
        self.assertIn("0000 0000 0000 0101", out)

    def test_data_backup_stops_at_end_of_memory(self):
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "x.csv")
            self.run_cmd(self.cli.cmd_sauvegarder_donnees, name, ("D", "W"))
            lines = Path(name).read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum(1 for l in lines if l.startswith("D;")), 1000)
        self.assertEqual(sum(1 for l in lines if l.startswith("W;")), 512)
        self.assertIn("D;1;7;7;0007", lines)

    def test_data_restore(self):
        self.cli.confirm = lambda q: True
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "x.csv")
            self.fake.mem["H"][5] = 55
            self.run_cmd(self.cli.cmd_sauvegarder_donnees, name, ("D", "H", "W", "CIO"))
            self.assertIn("# automate;CJ1M-CPU12", Path(name).read_text(encoding="utf-8"))
            self.fake.mem["D"][10] = 1
            self.fake.mem["D"][11] = 2
            self.fake.mem["W"][3] = 3
            self.fake.mem["CIO"][100] = 0xFFFF       # outputs: must not be restored
            answers = iter([True, False])     # write: yes; switch to MONITOR: no
            self.cli.confirm = lambda q: next(answers)
            out = self.run_cmd(self.cli.cmd_restaurer_donnees, name)
            self.assertIn("refuse les ecritures", out)   # in RUN: nothing written
            self.assertEqual(self.fake.mem["D"][10], 1)
            self.cli.confirm = lambda q: True          # yes to everything: MONITOR, write, back to RUN
            out = self.run_cmd(self.cli.cmd_restaurer_donnees, name)
            self.assertIn("Restauration OK : 3 mots", out)
            self.assertIn("ignorees : CIO", out)
            self.assertIn("Lecture D : 1000 mots", out)
            self.assertEqual(self.fake.mode, 0x04)     # back in RUN
        self.assertEqual(self.fake.mem["D"][10:12], [70, 77])
        self.assertEqual(self.fake.mem["W"][3], 0)
        self.assertEqual(self.fake.mem["CIO"][100], 0xFFFF)
        self.assertEqual(self.fake.mem["H"][5], 55)

    def test_write_in_run_switches_to_monitor_and_back(self):
        self.cli.confirm = lambda q: True
        out = self.run_cmd(self.cli.cmd_ecrire, "D", 5, 1234)
        self.assertIn("Relu : D5 = 1234", out)
        self.assertEqual(self.fake.mode, 0x04)
        self.assertIn("@00SC02", self.fake.requests)
        self.assertIn("@00SC03", self.fake.requests)

    def test_write_in_monitor_does_not_change_mode(self):
        self.cli.confirm = lambda q: True
        self.plc.set_mode("monitor")
        self.fake.requests.clear()
        self.run_cmd(self.cli.cmd_ecrire, "D", 5, 1)
        self.assertFalse([r for r in self.fake.requests if r[3:5] == "SC"])
        self.assertEqual(self.fake.mode, 0x02)

    def test_compare_refuses_a_data_csv(self):
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "omron_donnees.csv")
            Path(name).write_text("# date;2026-10-30\nzone;adresse\n", encoding="utf-8")
            out = self.run_cmd(self.cli.cmd_comparer, name)
        self.assertIn("sauvegarde de DONNEES", out)
        self.assertFalse([r for r in self.fake.requests if "0306" in r])   # program not read

    def test_units_reply_not_understood_is_shown_raw(self):
        self.fake.unit_list = []
        out = self.run_cmd(self.cli.cmd_parametres, os.path.join(tempfile.mkdtemp(), "p.txt"))
        self.assertIn("réponse 0502 non décodée", out)
        self.assertIn("emplacement 0 : 0001", out)

    def test_backup_then_compare(self):
        with tempfile.TemporaryDirectory() as d:
            name = os.path.join(d, "p.cjbak")
            self.run_cmd(self.cli.cmd_sauvegarder, name)
            self.assertIn("IDENTIQUE", self.run_cmd(self.cli.cmd_comparer, name))
            self.fake.program = self.fake.program[:500] + b"X" + self.fake.program[501:]
            self.assertIn("DIFFERENT", self.run_cmd(self.cli.cmd_comparer, name))


if __name__ == "__main__":
    unittest.main()
