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
        self.assertEqual([(m["emplacement"], m["configure"]["nom"], m["ok"]) for m in d["modules"]],
                         [(1, "TSX DEZ 32D2", True), (2, "TSX DEZ 32D2", True),
                          (3, "TSX DSZ 32R5", True), (4, "TSX DSZ 32R5", True)])

    def test_unpowered_module(self):
        self.s.plc.io_faults[1] = self.s.plc.io_faults[2] = 1      # as captured from PL7 on the bench
        self.assertIn("voyant I/O", self.s.status()["alertes"][0])
        d = self.s.diagnostic()
        self.assertEqual([m["emplacement"] for m in d["modules"] if not m["ok"]], [1, 2])
        self.assertEqual(d["modules"][0]["adresse"], "%I1.0 ...")

    def test_module_absent_or_different(self):
        self.s.plc.io_faults[3] = self.s.plc.io_faults[4] = 1
        self.s.plc.installed = {3: None, 4: 0x1009}                    # 3 removed, 4 holds a DEZ 32D2
        rows = {m["emplacement"]: m for m in self.s.diagnostic()["modules"]}
        self.assertIn("ABSENT", rows[3]["etat"])
        self.assertIn("DIFFERENT", rows[4]["etat"])
        self.assertEqual(rows[4]["en_place"]["nom"], "TSX DEZ 32D2")
        self.assertEqual(rows[1]["en_place"]["nom"], "TSX DEZ 32D2")

    def test_config_kept_when_app_info_changes(self):
        uploads = []
        real = self.s.plc.upload
        self.s.plc.upload = lambda progress=None: (uploads.append(1), real(progress))[1]
        for _ in range(3):                                         # app info differs at each read
            self.assertEqual(len(self.s.diagnostic()["modules"]), 4)
        self.assertEqual(len(uploads), 1)                          # program read once, then kept

    def test_reserved_by_pl7_uses_a_matching_copy(self):
        self.s.plc.app = self.s.plc.app[:-100] + b"\xdd\xdd\x77\x77" + bytes(40) + b"\x6b\x93" + bytes(54)
        self.s.save_program("copie")
        self.wait()
        self.s.tsx.configs.clear()
        self.s.plc.reserved_elsewhere = True                       # PL7 connected on the other port
        d = self.s.diagnostic()
        self.assertEqual(len(d["modules"]), 4)
        self.assertTrue(any("reserve par un autre poste" in a for a in d["alertes"]))

    def test_reserved_and_no_copy(self):
        self.s.plc.reserved_elsewhere = True
        d = self.s.diagnostic()
        self.assertTrue(any("Configuration non lue" in a for a in d["alertes"]))

    def test_external_fault_named(self):
        self.s.plc.io_faults[1] = 1
        rows = {m["emplacement"]: m for m in self.s.diagnostic()["modules"]}
        self.assertIn("alimentation", rows[1]["etat"])

    def test_module_status_answers_from_the_capture(self):
        from plc_online.protocols import unitelway as ut
        t = ut.Tsx.__new__(ut.Tsx)
        answers = iter([[0x01, 0x03, 0x04, 0x00, 0x10, 0x09, 0x04, 0x00, 0x10, 0x09, 0x02, 0x02, 0x30, 0xFF, 0x00],
                        [0x01, 0x02, 0x04, 0x00, 0x10, 0x1B]])
        t._ask = lambda req, expect: next(answers)
        self.assertEqual(t.module_status(1)["en_place"], 0x1009)
        r = t.module_status(5)
        self.assertEqual((r["configure"], r["en_place"]), (0x101B, None))

    def test_config_from_image(self):
        from plc_online.protocols.unitelway import parse_config
        image = b"STATION\0" + bytes(50) + b"\x04\x00TSX 3721\0" + b"\xff\x01TSX DEZ 32D2\0" + bytes(28) \
            + b"\xff\x02TSX DEZ 32D2\0" + b"\xff\x03TSX AEZ 414\0"
        self.assertEqual(parse_config(image), [(1, "TSX DEZ 32D2"), (2, "TSX DEZ 32D2"), (3, "TSX AEZ 414")])

    def test_watch_and_write(self):
        r = self.s.values(["%MW12", "MW12:hex", "%MW9999", "E0.0"])
        self.assertEqual(r["valeurs"], {"%MW12": "3248", "MW12:hex": "16#0CB0"})
        self.assertEqual(set(r["erreurs"]), {"%MW9999", "E0.0"})
        w = self.s.write([{"adresse": "%MD40:real", "valeur": "1.5"}, {"adresse": "%MW30", "valeur": "-5"}])
        self.assertTrue(all(x["ok"] for x in w["resultats"]))
        with self.assertRaises(DriverError):
            self.s.write([{"adresse": "%KW0", "valeur": "1"}])

    def test_set_clock(self):
        r = self.s.set_clock()
        self.assertEqual(r["methode"], "37")
        self.assertEqual(r["horloge"][:10], time.strftime("%d/%m/%Y"))
        self.assertEqual(self.s.plc.s50, 0)                          # update mode switched off again

    def test_clock_words(self):
        import datetime
        from plc_online.protocols.unitelway import clock_words
        self.assertEqual(clock_words(datetime.datetime(2026, 11, 2, 12, 45, 30)), [0x3000, 0x1245, 0x1102, 0x2026])

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

    def test_restore_into_an_empty_plc(self):
        self.s.save_program("bon programme")
        bid = self.wait()["id"]
        good = self.s.plc.app
        self.s.plc.stop()
        plc = self.s.plc
        plc.app, plc.read_cpu = b"", lambda: [0, 1 if plc.app else 4, 8] + [0] * 74   # failed load: PLC empty
        plan = self.s.restore_plan(bid, False)
        self.assertEqual((plan["vide"], plan["run"]), (True, False))
        def no_stop():
            raise AssertionError("STOP sent to an empty PLC")
        plc.stop = no_stop                     # an empty PLC refuses STOP (FD); PL7 does not send it
        self.s.restore(bid, False, False)
        r = self.wait()
        self.assertTrue(r["automate_vide"])
        self.assertEqual((r["erreur"], r["differences"]), ("", []))
        self.assertEqual(self.s.plc.app, good)

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
