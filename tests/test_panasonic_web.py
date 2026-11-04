"""Panasonic FP0R in the web interface, on the demo FP0R-C16."""
import tempfile
import time
import unittest

from plc_online.protocols import mewtocol as mw
from plc_online.web import panasonic_web as pw
from plc_online.web.drivers import BRANDS, DriverError, Session


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.port = pw.DemoPort()
        self.plc = mw.Fp0r(self.port, 1)

    def test_contacts(self):
        self.assertTrue(self.plc.read_contact("X", 0, 0))
        self.assertFalse(self.plc.read_contact("X", 0, 1))
        self.plc.write_contact("Y", 0, 5, True)
        self.assertEqual(self.plc.read_contact_words("Y", 0, 1)[0], 0b100010)
        self.plc.write_contact_word("R", 10, 0x1234)
        self.assertEqual(self.plc.read_contact_words("R", 10, 1), [0x1234])
        self.assertEqual(self.port.buf, b"")

    def test_frames(self):
        self.assertEqual(self.plc._frame("RCSR901C")[:12], b"%01#RCSR901C")
        self.assertEqual(self.plc._frame("WCSY0003" + "1")[:13], b"%01#WCSY00031")

    def test_mode_and_status(self):
        self.plc.set_mode(False)
        self.assertEqual(self.plc.read_status()["mode"], "PROG")
        self.assertFalse(self.plc.read_contact("R", 902, 0))

    def test_refused_code_text(self):
        with self.assertRaises(mw.PlcRefused) as e:
            self.plc.read_dt(40000, 1)
        self.assertIn("adresse hors plage", str(e.exception))

    def test_addresses(self):
        self.assertEqual(pw.bit_name("R", (901, 12)), "R901C")
        self.assertEqual((pw.bit_name("X", 31), pw.bit_name("Y", 3)), ("X1F", "Y3"))
        v = pw.Fp0rVar("X1F")
        self.assertEqual((v.area, v.n, v.bit, v.word), ("X", 1, 15, False))
        self.assertEqual((pw.Fp0rVar("WR10").area, pw.Fp0rVar("dt90300:dint").fmt), ("WR", "dint"))
        for bad in ("DT", "Z5", "WX1:dint", "X1G"):
            with self.assertRaises(ValueError):
                pw.Fp0rVar(bad)

    def test_self_diag_text(self):
        self.assertEqual(pw.self_diag_text(0), "aucune")
        self.assertIn("F148", pw.self_diag_text(120))
        self.assertIn("operation", pw.self_diag_text(45))


class Fp0rWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo-panasonic", {})

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        j = self.s.job_state()
        self.assertEqual(j["erreur"], "")
        return j["resultat"]

    def test_brand_listed(self):
        self.assertTrue(BRANDS["panasonic"]["disponible"])
        self.assertIn("runstop", BRANDS["panasonic"]["fonctions"])

    def test_status_and_diagnostic(self):
        st = self.s.status()
        self.assertEqual((st["mode"], st["alertes"]), ("RUN", []))
        self.assertIn("Cycle", [k for k, _ in st["details"]])
        d = self.s.diagnostic()
        self.assertIn("R9000", [x["adresse"] for x in d["systeme"]])
        self.assertIn("DT90000", [x["adresse"] for x in d["systeme"]])
        x = d["modules"][0]
        self.assertEqual((x["adresse"], x["variables"][-1]["adresse"]), ("X0..X7", "X7"))
        self.assertTrue(x["en_place"]["nom"].startswith("1 0 1"))

    def test_fault_shown(self):
        self.s.plc.ser.contacts[("R", 900)] = 0x0081          # R9000 + R9007
        self.s.plc.ser.dt[90000] = 45
        self.s.plc.ser.dt[90017] = 123
        st = self.s.status()
        self.assertIn("E45", st["alertes"][0])
        self.assertIn("123", st["alertes"][1])
        self.assertTrue(st["voyants"][2]["allume"])

    def test_run_prog(self):
        self.s.stop_cpu()
        self.assertEqual(self.s.status()["mode"], "PROG")
        self.s.start_cpu()
        self.assertEqual(self.s.status()["mode"], "RUN")

    def test_watch_and_write(self):
        v = self.s.values(["DT0", "DT1:hex", "X0", "Y1", "R901C", "WX0:bin", "Q1"])
        self.assertEqual((v["valeurs"]["DT0"], v["valeurs"]["DT1:hex"], v["valeurs"]["X0"], v["valeurs"]["R901C"]),
                         ("1250", "16#002A", "1", "1"))
        self.assertEqual(v["valeurs"]["WX0:bin"], "2#0000_0000_0000_0101")
        self.assertIn("Q1", v["erreurs"])
        r = self.s.write([{"adresse": "DT20", "valeur": "-5"}, {"adresse": "Y3", "valeur": "1"},
                          {"adresse": "DT30:dint", "valeur": "100000"}, {"adresse": "WR5", "valeur": "16#00FF"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]), r)
        self.assertTrue(r["run"])
        self.assertEqual(self.s.values(["DT30:dint"])["valeurs"]["DT30:dint"], "100000")
        with self.assertRaises(DriverError):
            self.s.write([{"adresse": "X0", "valeur": "1"}])

    def test_data_save_plan_restore(self):
        self.s.data_save("avant")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "12315 DT")
        self.s.plc.write_dt(10, 7)
        self.s.plc.write_dt(5000, 9)
        plan = self.s.data_plan(saved["id"])
        self.assertEqual((plan["differents"], plan["exemples"][0]["adresse"]), (2, "DT10"))
        self.s.data_restore(saved["id"])
        r = self.wait()
        self.assertEqual((r["ecrits"], r["nb_differents_apres"]), (2, 0))
        self.assertEqual(self.s.plc.read_dt(10, 1), [300])
        self.assertEqual(len(self.s.data_list()["sauvegardes"]), 1)

    def test_data_import(self):
        text = "zone;adresse;valeur\nDT;0;5\nDT;3;-1\n"
        r = self.s.data_import([("recette.csv", text.encode())], "")
        self.assertEqual(r["comptes"], "2 DT")
        values, _ = pw.parse_data_csv((self.s.fp0r.data / r["id"] / "donnees.csv").read_text())
        self.assertEqual(values[3], 0xFFFF)

    def test_no_program(self):
        with self.assertRaises(DriverError):
            self.s.list_backups()


if __name__ == "__main__":
    unittest.main()
