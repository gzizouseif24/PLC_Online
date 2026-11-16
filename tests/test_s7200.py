"""Siemens S7-200 over PPI: link layer, S7 PDUs, SM decoding, web session on the simulated CPU 224."""
import tempfile
import time
import unittest
from pathlib import Path

from plc_online.protocols import ppi
from plc_online.web import s7200_web as sw
from plc_online.web.drivers import BRANDS, DriverError, Session


class LinkTests(unittest.TestCase):
    def test_frames_as_libnodave(self):
        # SD2: 68 L L 68 DA SA 6C PDU FCS 16 ; SD1 "request data": 10 DA SA 5C FCS 16
        f = ppi.sd2_frame(2, 0, 0x6C, b"\x32\x01")
        self.assertEqual(f, bytes([0x68, 5, 5, 0x68, 2, 0, 0x6C, 0x32, 1, (2 + 0x6C + 0x32 + 1) & 0xFF, 0x16]))
        self.assertEqual(ppi.sd1_frame(2, 0, 0x5C), bytes([0x10, 2, 0, 0x5C, 0x5E, 0x16]))

    def test_not_ready_is_asked_again(self):
        port = sw.DemoPort()
        orig = port.write
        state = {"busy": 2}

        def write(frame):
            if frame[0] == ppi.SD1 and state["busy"]:
                state["busy"] -= 1
                port.buf = bytes([ppi.SC])                 # not ready yet
                return
            orig(frame)
        port.write = write
        plc = ppi.S7200(ppi.Ppi(port)).connect()
        self.assertEqual(plc.read("SM", 6, 1), bytes([0b0010_0000]))

    def test_frames_of_other_stations_are_skipped(self):
        # S7-200 in PPI master mode: FDL status requests (SD1) and tokens (SD4) between our frames
        port = sw.DemoPort()
        orig = port.write
        poll = ppi.sd1_frame(0, 2, 0x49) + bytes([0xDC, 3, 2]) + ppi.sd2_frame(5, 2, 0x6C, b"\x01\x02")

        def write(frame):
            orig(frame)
            port.buf = poll + port.buf
        port.write = write
        plc = ppi.S7200(ppi.Ppi(port, retries=0)).connect()
        self.assertEqual(plc.read("V", 0, 2), bytes([0x04, 0xD2]))
        self.assertTrue(any(f[0] == ppi.SD1 for f in plc.ppi.skipped))

    def test_journal_written_when_the_link_fails(self):
        port = sw.DemoPort()
        plc = ppi.S7200(ppi.Ppi(port, retries=1)).connect()
        plc.ppi.journal_path = Path(tempfile.mkdtemp()) / "journal_ppi.txt"
        port.write = lambda frame: None                    # cable unplugged
        with self.assertRaises(ppi.PpiError) as e:
            plc.read("V", 0, 2)
        self.assertIn("journal_ppi.txt", str(e.exception))
        text = plc.ppi.journal_path.read_text()
        self.assertIn("ECHEC", text)
        self.assertEqual(plc.ppi.failures, 1)

    def test_lost_reply_and_busy_plc(self):
        # bench log 16/11: a reply lost after E5, then the PLC answers 10 00 02 02 04 16 (busy) to every new request
        port = sw.DemoPort()
        plc = ppi.S7200(ppi.Ppi(port, retries=2)).connect()
        port.lose_next = 1
        self.assertEqual(plc.read("V", 0, 2), bytes([0x04, 0xD2]))
        port.pending = ppi.s7_header(99, b"\x04\x01", b"")      # an old reply nobody collected
        self.assertEqual(plc.read("V", 2, 2), bytes([0, 42]))
        self.assertTrue(any("occupe" in d or "perdu" in k for _, k, d in plc.ppi.journal))

    def test_bad_checksum_and_errors(self):
        port = sw.DemoPort()
        plc = ppi.S7200(ppi.Ppi(port, retries=0)).connect()
        with self.assertRaises(ppi.PlcRefused) as e:
            plc.read("V", 9000, 2)
        self.assertIn("hors zone", str(e.exception))
        orig = port.write

        def corrupt(frame):
            orig(frame)
            if frame[0] == ppi.SD1 and port.buf:
                port.buf = port.buf[:-2] + bytes([port.buf[-2] ^ 1]) + port.buf[-1:]
        port.write = corrupt
        with self.assertRaises(ppi.PpiError):
            plc.read("V", 0, 2)


class DecodingTests(unittest.TestCase):
    def test_manual_figure_4_11(self):
        # CPU 224XP + 4 In/4 Out, 8 In, 4 AI/1 AQ, 8 Out, 4 AI/1 AQ (S7-200 System Manual, figure 4-11)
        sm = bytearray(30)
        ids = [0b0000_0101, 0b0000_0100, 0b0001_1001, 0b0000_0001, 0b0001_1001, 0x80, 0x80]
        for i, ident in enumerate(ids):
            sm[8 + 2 * i] = ident
        units = sw.layout("CPU 224XP", sw.modules(sm))
        digital = [(u["di"], u["dq"]) for u in units if u["di"][1] or u["dq"][1]]
        # I2.0 / Q2.0 for module 0, I3.0 for module 1, Q3.0 for module 3
        self.assertEqual(digital, [((0, 14), (0, 10)), ((16, 8), (16, 8)), ((24, 8), (24, 0)), ((32, 0), (24, 8))])
        analog = [(u["ai"], u["aq"]) for u in units if u["ai"][1]]
        # AIW0 / AQW0 in the CPU, AIW4 / AQW4 for module 2, AIW12 / AQW8 for module 4
        self.assertEqual(analog, [((0, 2), (0, 1)), ((4, 4), (4, 2)), ((12, 4), (8, 2))])

    def test_module_errors_and_cpu(self):
        sm = bytearray(30)
        sm[6] = 0b1001_0000
        sm[8], sm[9] = 0b0000_1010, 0b1000_0100
        sm[10:22] = bytes([0x80, 0] * 6)
        m = sw.modules(sm)
        self.assertEqual(m[0]["erreurs"], ["erreur de configuration", "pas d'alimentation utilisateur"])
        self.assertEqual(sw.CPU_IDS[sm[6] >> 4], "CPU 226")
        self.assertIn("Module 0", sw.faults(sm)[0])

    def test_addresses(self):
        for text in ("I0.0", "V100.3", "SM0.5", "VB10", "VW100", "VD200:real", "SMW22", "AIW0", "AQW2", "MB0"):
            sw.S7200Var(text)
        for bad in ("VW", "V100", "AIB0", "VB10.1", "X0.0", "VW0:real", "I0.8"):
            with self.assertRaises(ValueError, msg=bad):
                sw.S7200Var(bad)

    def test_program_container(self):
        blocks = {("OB", 1): b"\x01\x02", ("SDB", 0): b"", ("DB", 1): b"\xff" * 3}
        blob = sw.pack_blocks(blocks)
        self.assertEqual(sw.unpack_blocks(blob), blocks)
        other = sw.pack_blocks({**blocks, ("OB", 1): b"\x01\x03"})
        self.assertEqual(sw.diff(other, blob), ["OB1 modifie (premier octet different : 1)"])


class WebTests(unittest.TestCase):
    def setUp(self):
        self.s = Session(backups=tempfile.mkdtemp())
        self.s.connect("demo-s7-200", {})

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        j = self.s.job_state()
        self.assertEqual(j["erreur"], "")
        return j["resultat"]

    def test_brand_status_diagnostic(self):
        self.assertTrue(BRANDS["s7-200"]["disponible"])
        st = self.s.status()
        self.assertEqual((st["modele"], st["mode"], st["alertes"]), ("Siemens S7-200 CPU 224 (demo)", "RUN", []))
        d = self.s.diagnostic()
        self.assertEqual([m["adresse"] for m in d["modules"]], ["I0 / Q0", "I2 / Q2", "AIW0 / AQW0"])
        self.assertTrue(d["modules"][2]["en_place"]["nom"].startswith("AIW0 = 6400"))

    def test_fault_bits(self):
        self.s.plc.ppi.ser.mem["SM"][5] = 1
        self.s.plc.ppi.ser.mem["SM"][9] = 0b0000_0010
        alerts = self.s.status()["alertes"]
        self.assertTrue(any("SM5.0" in a for a in alerts) and any("fusible" in a for a in alerts))

    def test_watch_write_runstop(self):
        v = self.s.values(["I0.2", "VW0", "VD0:hex", "AIW0", "I2.1"])["valeurs"]
        self.assertEqual(list(v.values()), ["1", "1234", "16#04D2002A", "6400", "1"])
        r = self.s.write([{"adresse": "VW10", "valeur": "-12"}, {"adresse": "Q1.0", "valeur": "1"},
                          {"adresse": "VD20:real", "valeur": "1.5"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]))
        for bad in ("I0.0", "SMB5"):
            with self.assertRaises(DriverError):
                self.s.write([{"adresse": bad, "valeur": "1"}])
        self.s.stop_cpu()
        self.assertEqual(self.s.status()["mode"], "STOP")
        self.s.start_cpu()
        self.assertEqual(self.s.status()["mode"], "RUN")

    def test_outputs_in_stop(self):
        r = self.s.write([{"adresse": "Q0.1", "valeur": "1"}])
        self.assertIn("STOP", r["note"])                      # RUN: the program rewrites Q0
        self.s.stop_cpu()
        r = self.s.write([{"adresse": "Q0.1", "valeur": "1"}, {"adresse": "Q0.3", "valeur": "1"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]), r)
        self.assertEqual(self.s.plc.read("Q", 0, 1), bytes([0b1010]))
        self.assertNotIn("note", r)

    def test_refused_block_is_not_fatal(self):
        port = self.s.plc.ppi.ser
        port.blocks[("OB", 2)] = b""
        orig = port._answer

        def answer(p):
            if p["param"][:1] == b"\x1d" and p["param"][12:17] == b"00002":
                return port._reply(p["ref"], bytes([0x1D]), error=0xD209, kind=2)
            return orig(p)
        port._answer = answer
        self.s.save_program("")
        self.assertEqual(self.wait()["comptes"], "DB1 (40 o), OB1 (120 o), SDB0 (64 o)")
        self.assertTrue(any("OB2" in x for x in self.s.plc.upload_refused))

    def test_program_and_data(self):
        self.s.save_program("")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "DB1 (40 o), OB1 (120 o), SDB0 (64 o)")
        self.s.plc.ppi.ser.blocks[("OB", 1)] = b"\x00" * 120
        self.s.compare(saved["id"])
        self.assertIn("OB1 modifie", self.wait()["differences"][0]["detail"])
        with self.assertRaises(DriverError):
            self.s.restore_plan(saved["id"], False)
        self.s.data_save("")
        data = self.wait()
        self.assertEqual(data["comptes"], "8192 VB")
        self.s.plc.write("V", 100, b"\x07")
        self.assertEqual(self.s.data_plan(data["id"])["differents"], 1)
        self.s.data_restore(data["id"])
        self.assertEqual(self.wait()["nb_differents_apres"], 0)

    def test_tools(self):
        self.assertEqual(self.s.block("VW", 0, 2)["valeurs"], [1234, 42])
        u = self.s.inputs()["unites"]
        self.assertEqual([len(x["entrees"]) for x in u], [14, 16])
        self.assertEqual(u[1]["entrees"][1], {"adresse": "I2.1", "valeur": 1})


if __name__ == "__main__":
    unittest.main()
