"""Delta DVP in the web interface, on the demo DVP28SV."""
import tempfile
import time
import unittest

from plc_online.protocols import delta as dl
from plc_online.web.drivers import DriverError, Session


class DeltaWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo-delta", {})

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        j = self.s.job_state()
        self.assertEqual(j["erreur"], "")
        return j["resultat"]

    def test_status_and_diagnostic(self):
        st = self.s.status()
        self.assertEqual((st["mode"], st["alertes"]), ("RUN", []))
        self.assertIn("Horloge", [k for k, _ in st["details"]])
        d = self.s.diagnostic()
        self.assertIn("M1004", [x["adresse"] for x in d["systeme"]])
        x = d["modules"][0]
        self.assertEqual((x["adresse"], x["variables"][-1]["adresse"]), ("X0..X17", "X17"))
        self.assertTrue(x["en_place"]["nom"].startswith("1 0 1"))

    def test_fault_shown(self):
        self.s.plc.write_bit("M", 1004, True)
        self.s.plc.write_word("D", 1004, 0xC4EE)
        st = self.s.status()
        self.assertIn("END", st["alertes"][0])
        self.assertTrue(st["voyants"][1]["allume"])

    def test_watch_and_write(self):
        v = self.s.values(["D10", "D11:hex", "X0", "Y1", "X8", "Q1"])
        self.assertEqual((v["valeurs"]["D10"], v["valeurs"]["D11:hex"], v["valeurs"]["X0"]), ("1250", "16#002A", "1"))
        self.assertIn("X8", v["erreurs"])
        r = self.s.write([{"adresse": "D20", "valeur": "-5"}, {"adresse": "M5", "valeur": "1"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]))
        with self.assertRaises(DriverError):
            self.s.write([{"adresse": "X0", "valeur": "1"}])

    def test_clock(self):
        self.assertRegex(self.s.set_clock()["horloge"], r"\d\d/\d\d/20\d\d")

    def test_data_save_plan_restore(self):
        self.s.data_save("avant")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "10000 D")
        self.s.plc.write_word("D", 10, 7)
        self.s.plc.write_word("D", 5000, 9)
        plan = self.s.data_plan(saved["id"])
        self.assertEqual((plan["differents"], plan["exemples"][0]["adresse"]), (2, "D10"))
        self.s.data_restore(saved["id"])
        r = self.wait()
        self.assertEqual((r["ecrits"], r["nb_differents_apres"]), (2, 0))
        self.assertEqual(self.s.plc.read_words("D", 4999, 2), [0, 0])
        self.assertEqual(len(self.s.data_list()["sauvegardes"]), 1)

    def test_data_import(self):
        text = "zone;adresse;valeur\nD;0;5\nD;3;-1\n"
        r = self.s.data_import([("recette.csv", text.encode())], "")
        self.assertEqual(r["comptes"], "2 D")
        values, _ = dl.parse_data_csv((self.s.delta.data / r["id"] / "donnees.csv").read_text())
        self.assertEqual(values[3], 0xFFFF)

    def test_no_program(self):
        with self.assertRaises(DriverError):
            self.s.list_backups()


if __name__ == "__main__":
    unittest.main()
