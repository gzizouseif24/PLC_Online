"""TSX in the web interface, on the demo TSX: status, watch, program and data copies."""
import io
import tempfile
import time
import unittest
import zipfile

from plc_online.web.drivers import DriverError, Session
from plc_online.web.tsx_web import TsxVar


class TsxWebTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo-tsx", {})

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
        self.assertEqual((st["modele"], st["mode"], st["alertes"]), ("TSX 3721", "RUN", []))
        d = self.s.diagnostic()
        self.assertIn("%S10", [x["adresse"] for x in d["systeme"]])
        self.assertNotIn("modules", d)

    def test_watch_and_write(self):
        r = self.s.values(["%MW12", "MW12:hex", "%MW9999", "E0.0"])
        self.assertEqual(r["valeurs"], {"%MW12": "3248", "MW12:hex": "16#0CB0"})
        self.assertEqual(set(r["erreurs"]), {"%MW9999", "E0.0"})
        w = self.s.write([{"adresse": "%MD40:real", "valeur": "1.5"}, {"adresse": "%MW30", "valeur": "-5"}])
        self.assertTrue(all(x["ok"] for x in w["resultats"]))
        with self.assertRaises(DriverError):
            self.s.write([{"adresse": "%KW0", "valeur": "1"}])

    def test_var_formats(self):
        self.assertEqual(TsxVar("%MW1:bin").decode(5), "2#0000_0000_0000_0101")
        self.assertEqual(TsxVar("%MW1").encode("-1"), 0xFFFF)
        self.assertEqual(TsxVar("%MD2:real").decode(TsxVar("%MD2:real").encode("2.5")), "2.5")
        with self.assertRaises(ValueError):
            TsxVar("%MW1").encode("70000")

    def test_program_save_compare_restore(self):
        self.s.save_program("avant")
        bid = self.wait()["id"]
        self.s.compare(bid)
        self.assertTrue(self.wait()["identique"])
        self.s.restore(bid, False, False)
        r = self.wait()
        self.assertEqual((r["erreur"], r["differences"]), ("", []))
        self.assertIn(r["sauvegarde_avant"], [b["id"] for b in self.s.list_backups()["sauvegardes"]])

    def test_stx_import_warns_other_cpu(self):
        apx = b"STATION\0" + bytes(500)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("EXE/APP/STATION.APX", apx)
            z.writestr("STATION.INI", "[PL7TOOL3]\r\nRefProcess=TSX 3722   V2.0\r\n")
        r = self.s.import_backup([("appli.stx", buf.getvalue())], "")
        plan = self.s.restore_plan(r["id"], False)
        self.assertTrue(plan["autre_automate"])
        self.assertEqual(plan["cible"], "TSX 3722")

    def test_data_save_plan_restore_and_dat(self):
        self.s.plc.stop()                       # demo counters stand still
        self.s.data_save("")
        did = self.wait()["id"]
        self.s.plc.mw[12] = 1
        plan = self.s.data_plan(did)
        self.assertEqual(plan["differents"], 1)
        self.s.data_restore(did)
        self.assertEqual(self.wait()["ecrits"], 1)
        self.assertEqual(self.s.plc.mw[12], 3248)
        dat = "[HEADER]\nVAR_GLOBAL\nAT %MW12 : WORD := (7);\nEND_VAR\n".encode("latin-1")
        r = self.s.data_import([("F1.dat", dat)], "")
        self.assertEqual(self.s.data_plan(r["id"])["differents"], 1)


if __name__ == "__main__":
    unittest.main()
