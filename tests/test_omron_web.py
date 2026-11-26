"""Omron CJ1M in the web interface, on the simulated CJ1M."""
import tempfile
import time
import unittest

from plc_online.web import omron_web as ow
from plc_online.web.drivers import BRANDS, DriverError, Session


class OmronWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo-omron", {})

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        j = self.s.job_state()
        self.assertEqual(j["erreur"], "")
        return j["resultat"]

    def test_brand(self):
        self.assertTrue(BRANDS["omron"]["disponible"])
        self.assertIn("monitor", BRANDS["omron"]["fonctions"])

    def test_status_and_diagnostic(self):
        st = self.s.status()
        self.assertEqual((st["modele"], st["mode"], st["alertes"]), ("CJ1M-CPU12", "RUN", []))
        d = self.s.diagnostic()
        self.assertEqual([m["configure"]["nom"] for m in d["modules"]], ["CJ1W-ID211", "CJ1W-OC211"])
        self.assertEqual((d["modules"][0]["adresse"], d["modules"][1]["adresse"]), ("CIO 0", "CIO 1"))
        self.assertIn("erreur pile", d["systeme"][0]["texte"])

    def test_faults(self):
        self.s.plc.ser.non_fatal = 0x8000 | 0x0010
        st = self.s.status()
        self.assertTrue(any("FAL" in a for a in st["alertes"]))
        self.assertTrue(any("pile" in a for a in st["alertes"]))
        self.assertTrue(st["voyants"][1]["clignote"])

    def test_watch_write_needs_monitor(self):
        v = self.s.values(["D0", "D2", "0.02", "A400:hex"])["valeurs"]
        self.assertEqual((v["D0"], v["D2"], v["0.02"], v["A400:hex"]), ("1250", "-5", "1", "16#0000"))
        with self.assertRaises(DriverError) as e:
            self.s.write([{"adresse": "D5", "valeur": "3"}])
        self.assertIn("MONITOR", str(e.exception))
        self.s.monitor_cpu()
        r = self.s.write([{"adresse": "D5", "valeur": "-3"}, {"adresse": "1.04", "valeur": "1"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]))
        self.assertEqual(self.s.plc.read("CIO", 1, 1)[0], 0b10011)

    def test_data_restore_goes_through_monitor(self):
        self.s.data_save("")
        saved = self.wait()
        self.assertEqual(saved["comptes"], "32768 D, 512 H, 512 W")
        self.s.plc.ser.mem["D"][100] = 9
        self.assertEqual(self.s.data_plan(saved["id"])["differents"], 1)
        self.s.data_restore(saved["id"])
        r = self.wait()
        self.assertEqual((r["ecrits"], r["nb_differents_apres"]), (1, 0))
        self.assertEqual(self.s.status()["mode"], "RUN")

    def test_program_copy_and_tools(self):
        self.s.save_program("")
        saved = self.wait()
        self.s.compare(saved["id"])
        self.assertTrue(self.wait()["identique"])
        with self.assertRaises(DriverError):
            self.s.restore_plan(saved["id"], False)
        self.assertEqual(self.s.block("D", 0, 3)["valeurs"], [1250, 42, -5])
        u = self.s.inputs()["unites"]
        self.assertEqual((len(u), u[0]["entrees"][2]), (1, {"adresse": "0.02", "valeur": 1}))

    def test_modes(self):
        self.s.stop_cpu()
        self.assertEqual(self.s.status()["mode"], "STOP")
        self.s.monitor_cpu()
        self.assertEqual(self.s.status()["mode"], "MONITOR")

    def test_var(self):
        self.assertEqual(ow.OmronVar("W10.05").bit, 5)
        with self.assertRaises(ValueError):
            ow.OmronVar("D100:real")


if __name__ == "__main__":
    unittest.main()


class ModiconWebTests(unittest.TestCase):
    def test_read_only_slave(self):
        s = Session(backups=tempfile.mkdtemp())
        s.connect("demo-modicon", {})
        v = s.values(["%MW0", "MW10", "%M3", "%IW1:hex", "X1"])
        self.assertEqual((v["valeurs"]["%MW0"], v["valeurs"]["MW10"], v["valeurs"]["%IW1:hex"]), ("1250", "-5", "16#002A"))
        self.assertIn("X1", v["erreurs"])
        self.assertEqual(s.block("M", 0, 4)["valeurs"], [1, 0, 0, 1])
        with self.assertRaises(DriverError):
            s.write([{"adresse": "%MW0", "valeur": "1"}])
