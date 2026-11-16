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


P0 = "DB1 (56 o : USER1), OB1 (106 o : MAIN SBR_0 INT_0), SDB0 (332 o) ; charge par Micro/WIN le 2026-11-16 14:20:55"


class BlockTests(unittest.TestCase):
    def test_bench_blocks(self):
        """Blocks uploaded from the bench CPU 224 (16/11) after Micro/WIN loaded an empty project."""
        folder = Path(__file__).resolve().parents[1] / "research" / "s7200" / "bench_P0"
        ob = ppi.block_info((folder / "OB1.bin").read_bytes())
        self.assertEqual((ob["type"], ob["numero"], ob["taille"], ob["code"], ob["protege"], ob["modifie"]),
                         ("OB", 1, 106, 16, False, "2026-11-16 14:20:55"))
        self.assertEqual(ob["noms"], ["MAIN", "SBR_0", "INT_0"])
        self.assertEqual(len(ob["source"]), 65)
        db = ppi.block_info((folder / "DB1.bin").read_bytes())
        self.assertEqual((db["type"], db["noms"]), ("DB", ["USER1"]))
        sdb = ppi.block_info((folder / "SDB0.bin").read_bytes())
        self.assertEqual((sdb["type"], sdb["numero"], sdb["taille"], sdb["source"]), ("SDB", 0, 332, b""))

    def test_load_frames_as_libnodave(self):
        """testPPIload: 1A with the file name and sizes, parts with 00 FB, ends with _INSE."""
        port = sw.DemoPort()
        port.running = False
        sent = []
        orig = port.write
        port.write = lambda f: (sent.append(f), orig(f))[1]
        plc = ppi.S7200(ppi.Ppi(port, 2, 0)).connect()
        blob = port.blocks[("SDB", 0)]
        port.blocks.pop(("SDB", 0))
        plc.load_block(blob)
        self.assertEqual(port.blocks[("SDB", 0)], blob)
        pdus = [f[7:-2] for f in sent if f[0] == ppi.SD2]
        self.assertIn(b"\x1a\x00\x01\x00\x00\x00\x00\x00\x09_0B00000P\x0d1000332000296", pdus[1])
        answers = [f for f in sent if f[0] == ppi.SD2 and f[6] == 0x5C]
        self.assertEqual(len(answers), 3)                       # 218 + 114 bytes, then the 1C answer
        self.assertIn(b"_INSE", pdus[-1])
        with self.assertRaises(ppi.PlcRefused):                 # RUN: refused (as Micro/WIN asks for STOP)
            port.running = True
            plc.load_block(blob)


class DecodeTests(unittest.TestCase):
    """OB1 of the bench program (16/11) against Micro/WIN's own STL export of SBR10 'Allgemein'."""
    folder = Path(__file__).resolve().parents[1] / "research" / "s7200"

    def instructions(self, text, block):
        body = text.split(block, 1)[1].split("END_SUBROUTINE_BLOCK", 1)[0]
        return [" ".join(line.split("//")[0].split()) for line in body.splitlines()
                if line.strip() and not line.startswith(("Network", "TITLE", "BEGIN", "//"))]

    def test_allgemein_as_microwin(self):
        from plc_online.protocols import s7200_code
        r = s7200_code.to_stl((self.folder / "machine_banc" / "OB1.bin").read_bytes())
        awl = (self.folder / "machine_banc" / "Allgemein_SBR10.awl").read_text(encoding="latin-1")
        self.assertEqual(self.instructions(r["texte"], "Allgemein:SBR10"), self.instructions(awl, "Allgemein:SBR10"))
        self.assertIn("SUBROUTINE_BLOCK ScherenHyd:SBR11", r["texte"])
        self.assertIn("CALL   SBR11", r["texte"])
        self.assertEqual(r["texte"].count("Network "), 4 + 5 + 20)
        self.assertEqual((r["inconnu"], r["doute"]), (0, 2))          # MOVB IB1, IB2 / MOVB QB2, QB1 (deduced)
        self.assertIn("MOVB   IB1, IB2", r["texte"])

    def networks(self, text):
        import re
        body = text.split("BEGIN", 1)[1].split("END_ORGANIZATION_BLOCK", 1)[0]
        return [[" ".join(line.split("//")[0].split()) for line in n.splitlines()
                 if line.strip() and not line.strip().startswith("//")]
                for n in re.split(r"\nNetwork \d+[^\n]*\n", "\n" + body.strip("\n") + "\n")[1:]]

    def test_programme2_as_microwin(self):
        """118 networks: V bits (short and long address), S / R on V, MOVW / MOVD, LDW>= / LDW<=, INCW / INCD,
        TON with a VW preset, EU, O / ON, ALD / OLD."""
        from plc_online.protocols import s7200_code
        r = s7200_code.to_stl((self.folder / "programme2" / "OB1.bin").read_bytes())
        awl = (self.folder / "programme2" / "PPAL_OB1.awl").read_text(encoding="latin-1")
        got, want = self.networks(r["texte"]), self.networks(awl)
        self.assertEqual(len(got), 118)
        for k, (g, w) in enumerate(zip(got, want), 1):
            self.assertEqual(g, w, f"Network {k}")
        self.assertEqual((r["doute"], r["inconnu"]), (0, 0))

    def test_empty_project(self):
        from plc_online.protocols import s7200_code
        r = s7200_code.to_stl((self.folder / "bench_P0" / "OB1.bin").read_bytes())
        self.assertEqual([line for line in r["texte"].splitlines() if "BLOCK" in line and "END" not in line],
                         ["ORGANIZATION_BLOCK MAIN:OB1", "SUBROUTINE_BLOCK SBR_0:SBR0", "INTERRUPT_BLOCK INT_0:INT0"])
        self.assertEqual((r["sur"], r["doute"], r["inconnu"]), (0, 0, 0))


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
        self.assertEqual(self.wait()["comptes"], P0)
        refused = self.s.plc.upload_refused
        self.assertTrue(any("OB2" in x for x in refused) and any("SDB2" in x for x in refused), refused)

    def test_protected_plc(self):
        """Password: the block list is refused with D241. Status says so, the program read gives a clear message."""
        port = self.s.plc.ppi.ser
        orig = port._answer

        def answer(p):
            if p["param"][:1] == b"\x1d":                          # initUpload: refused, password required
                return port._reply(p["ref"], bytes([0x1D]), error=0xD241, kind=2)
            return orig(p)
        port._answer = answer
        self.s.plc.protege = None
        st = self.s.status()
        self.assertTrue(any("mot de passe" in a for a in st["alertes"]))
        self.assertTrue(any(d[0] == "Protection" for d in st["details"]))
        self.s.save_program("")
        j = self.s.job_state()
        while not j["fini"]:
            time.sleep(0.02); j = self.s.job_state()
        self.assertIn("mot de passe", j["erreur"])
        self.assertIn("etat, diagnostic", j["erreur"])

    def test_program_and_data(self):
        self.s.save_program("")
        saved = self.wait()
        self.assertEqual(saved["comptes"], P0)
        port = self.s.plc.ppi.ser
        p0 = dict(port.blocks)
        port.blocks[("OB", 1)] = p0[("OB", 1)][:40] + b"\x01" + p0[("OB", 1)][41:]
        self.s.compare(saved["id"])
        self.assertIn("OB1 modifie", self.wait()["differences"][0]["detail"])
        plan = self.s.restore_plan(saved["id"], False)
        self.assertEqual((plan["run"], plan["autre_automate"], len(plan["charger"])), (True, False, 3))
        self.assertEqual(len(plan["avert"]), 1)
        self.assertIn("DB1", plan["avert"][0])
        self.s.restore(saved["id"], False, False)
        r = self.wait()
        self.assertEqual((r["erreur"], r["charges"], r["differences"], r["etait_en_run"]),
                         ("", ["OB1", "DB1", "SDB0"], [], True))
        self.assertEqual(port.blocks, p0)
        self.assertFalse(port.running)
        details = self.s.backup_blocks(saved["id"])
        self.assertEqual(details["blocs"][2]["taille"], "106 octets : MAIN SBR_0 INT_0")
        self.assertIn("SUBROUTINE_BLOCK SBR_0:SBR0", details["stl"]["texte"])
        self.assertIn("avant restauration", str(self.s.list_backups()))
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
