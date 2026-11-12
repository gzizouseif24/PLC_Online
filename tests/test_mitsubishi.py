"""Mitsubishi FX programming port: frames, address map, demo FX1N-40MR in the web interface."""
import tempfile
import time
import unittest

from plc_online.protocols import melsec_fx as fx
from plc_online.web import melsec_web as mw
from plc_online.web.drivers import BRANDS, DriverError, Session


class ProtocolTests(unittest.TestCase):
    def test_documented_frames(self):
        # read 4 bytes from D123, write 1234 / ABCD to D123 / D124 (examples of the protocol description)
        self.assertEqual(fx.frame("0", "10F604"), b"\x02010F604\x0374")
        self.assertEqual(fx.frame("1", "10F6043412CDAB"), b"\x02110F6043412CDAB\x0349")

    def test_address_map(self):
        # FX-232AW BT5096-C tables 1 to 7
        for text, image, bit, force in [("S999", 0x007C, 7, 0x03E7), ("X100", 0x0088, 0, 0x0440),
                                        ("X177", 0x008F, 7, 0x047F), ("Y23", 0x00A2, 3, 0x0513),
                                        ("T255", 0x00DF, 7, 0x06FF), ("M1023", 0x017F, 7, 0x0BFF),
                                        ("M8000", 0x01E0, 0, 0x0F00), ("C255", 0x01DF, 7, 0x0EFF)]:
            area, n = fx.parse_address(text)
            self.assertEqual(fx.bit_image(area, n), (image, bit), text)
            self.assertEqual(fx.force_address(area, n), force, text)
        for text, address in [("D123", 0x10F6), ("D511", 0x13FE), ("D8000", 0x0E00), ("T255", 0x09FE),
                              ("C199", 0x0B8E), ("C201", 0x0C04)]:
            self.assertEqual(fx.data_address(*fx.parse_address(text))[0], address, text)
        self.assertEqual(fx.data_address("C", 200)[1], 4)
        for bad in ("X8", "X180", "D512", "M1024", "D8256", "Z1", "D"):
            with self.assertRaises(fx.FxError):
                fx.parse_address(bad)
        self.assertEqual(fx.name("X", 0o27), "X27")

    def test_cpu_and_errors(self):
        self.assertEqual(fx.cpu_text(26100), "FX1N V1.00")
        self.assertIn("bus", fx.error_text(8061, 6103))
        self.assertEqual(fx.error_text(8067, 0), "aucune")
        self.assertIn("index V / Z", fx.error_text(8067, 6706))

    def test_nak_and_bad_sum(self):
        port = mw.DemoPort()
        plc = fx.FxPlc(port, retries=0)
        orig = port.write
        port.write = lambda req: setattr(port, "buf", fx.NAK)
        with self.assertRaises(fx.FxRefused):
            plc.read_words("D", 0, 1)
        port.write = lambda req: (orig(req), setattr(port, "buf", port.buf[:-1] + b"0"))
        with self.assertRaises(fx.FxError):
            plc.read_words("D", 0, 1)


class LayoutTests(unittest.TestCase):
    def test_base_units(self):
        self.assertEqual(mw.base_model("FX1N-40MR-ES/UL"), ("FX1N-40MR", (24, 16)))
        self.assertEqual(mw.base_model("fx1n-24mt")[1], (14, 10))
        self.assertIsNone(mw.base_model("FX3U-32MR")[1])

    def test_expansion_models(self):
        for text, model in [("FX2N-16EX-ES/UL", "16EX"), ("FX0N-8ER", "8ER"), ("16EYR", "16EYR"), ("", ""),
                            ("aucune", ""), ("FX2N-4AD", "FX2N-4AD"), ("truc", None)]:
            self.assertEqual(mw.expansion_model(text), model, text)

    def test_octal_numbering(self):
        # 24MR: 14 inputs (16 occupied), 10 outputs (16 occupied); 8ER: 4 / 4 used, 8 / 8 occupied
        units = mw.layout("FX1N-24MR", ["8ER", "16EX", ""])
        self.assertEqual([(u[2], u[3]) for u in units],
                         [((0, 14), (0, 10)), ((16, 4), (16, 4)), ((24, 16), (24, 0))])
        self.assertEqual(fx.name("X", 24), "X30")


class FxWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo-mitsubishi", {})

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        j = self.s.job_state()
        self.assertEqual(j["erreur"], "")
        return j["resultat"]

    def test_brand(self):
        self.assertTrue(BRANDS["mitsubishi"]["disponible"])
        self.assertIn("runstop", BRANDS["mitsubishi"]["fonctions"])

    def test_status_and_diagnostic(self):
        st = self.s.status()
        self.assertEqual((st["mode"], st["reference"], st["alertes"]), ("RUN", "FX1N V1.00", []))
        d = self.s.diagnostic()
        base, ext = d["modules"]
        self.assertEqual((base["adresse"], ext["adresse"]), ("X0..X27 / Y0..Y17", "X30..X47"))
        self.assertTrue(base["en_place"]["nom"].startswith("X0..X27 : 1 0 0 1"))
        self.assertTrue(ext["en_place"]["nom"].startswith("X30..X47 : 0 1"))
        self.assertEqual(ext["variables"][1]["adresse"], "X31")
        self.assertIn("M8061", [x["adresse"] for x in d["systeme"]])

    def test_fault_shown(self):
        port = self.s.plc.ser
        port._set("M", 8067, True)
        port._put("D", 8067, 6705)
        port._put("D", 8069, 120)
        st = self.s.status()
        self.assertIn("6705", st["alertes"][0])
        self.assertIn("pas 120", st["alertes"][0])

    def test_watch_write_runstop(self):
        v = self.s.values(["D0", "D100", "X31", "M8000", "D8001:hex", "X8"])
        self.assertEqual([v["valeurs"][k] for k in ("D0", "D100", "X31", "M8000", "D8001:hex")],
                         ["1250", "-5", "1", "1", "16#65F4"])
        self.assertIn("X8", v["erreurs"])
        r = self.s.write([{"adresse": "D5", "valeur": "-7"}, {"adresse": "Y3", "valeur": "1"},
                          {"adresse": "C200", "valeur": "100000"}, {"adresse": "M10", "valeur": "1"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]), r)
        with self.assertRaises(DriverError):
            self.s.write([{"adresse": "X0", "valeur": "1"}])
        self.s.stop_cpu()
        self.assertEqual(self.s.status()["mode"], "STOP")
        self.s.start_cpu()
        self.assertEqual(self.s.status()["mode"], "RUN")

    def test_clock(self):
        self.s.set_clock()
        self.assertFalse(self.s.plc.read_bits("M", 8015, 1)[0])

    def test_data_save_plan_restore(self):
        self.s.data_save("avant")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "512 D")
        self.s.plc.write_words("D", 10, [7])
        plan = self.s.data_plan(saved["id"])
        self.assertEqual((plan["differents"], plan["exemples"][0]["adresse"]), (1, "D10"))
        self.s.data_restore(saved["id"])
        r = self.wait()
        self.assertEqual((r["ecrits"], r["nb_differents_apres"]), (1, 0))
        self.assertEqual(self.s.plc.read_words("D", 10, 1), [0])

    def test_program_memory(self):
        data = self.s.plc.read_program_memory(2000)
        self.assertEqual((len(data), data[:4]), (92 + 4000 + 4, bytes([0x10, 0, 0xAB, 0xCD])))

    def test_program_copy_compare_import(self):
        self.s.save_program("avant")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "8000 pas, 16096 octets")
        self.assertEqual(self.s.list_backups()["sauvegardes"][0]["note"], "avant")
        self.s.compare(saved["id"])
        self.assertTrue(self.wait()["identique"])
        self.s.plc.ser.mem[0x8000 + 92 + 2 * 120] = 0x55           # step 120 changed
        self.s.plc.ser.mem[0x8000 + 92 + 2 * 121 + 1] = 0x55       # step 121
        self.s.compare(saved["id"])
        r = self.wait()
        self.assertEqual((r["identique"], r["differences"][0]["detail"]), (False, "pas 120..121"))
        name, data = self.s.export_backup(saved["id"])
        self.assertEqual(self.s.import_backup([(name, data)], "")["comptes"], "8000 pas, 16096 octets")
        with self.assertRaises(DriverError):
            self.s.restore_plan(saved["id"], False)
        self.s.delete_backup(saved["id"])
        self.assertEqual(len(self.s.list_backups()["sauvegardes"]), 1)

    def test_program_protected(self):
        orig = self.s.plc.ser.write
        self.s.plc.ser.write = lambda req: (orig(req), setattr(self.s.plc.ser, "buf", fx.NAK)
                                            if req[2:3] == b"8" and req[1:2] == b"0" else None)
        self.s.save_program("")
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        self.assertIn("mot-cle", self.s.job_state()["erreur"])

    def test_step_ranges(self):
        a = bytes(200)
        b = bytearray(a)
        b[3], b[92], b[93], b[96], b[150] = 1, 1, 1, 1, 1
        self.assertEqual(mw.step_ranges(a, bytes(b)), ["parametres", "pas 0", "pas 2", "pas 29"])


if __name__ == "__main__":
    unittest.main()
