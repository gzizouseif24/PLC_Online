"""Allen-Bradley Logix: status / axis decoding and the web session on the demo GuardLogix."""
import unittest

from plc_online.protocols import logix as lx
from plc_online.web import logix_web as lw
from plc_online.web.drivers import BRANDS, DriverError, Session


class DecodingTests(unittest.TestCase):
    def test_controller_status(self):
        s = lx.decode_status(bytes([0x60, 0x30]))
        self.assertEqual((s["mode"], s["cle"], s["majeur"], s["mineur"]), ("RUN", "REM", False, False))
        s = lx.decode_status(bytes([0x70, 0x24]))
        self.assertEqual((s["mode"], s["cle"], s["majeur"]), ("PROG", "PROG", True))

    def test_module_status(self):
        m = lx.module_status(bytes([0x20, 0x00]))
        self.assertTrue(m["connexion_en_defaut"])
        self.assertFalse(lx.module_status(bytes([0x60, 0x00]))["connexion_en_defaut"])

    def test_axis_faults(self):
        a = lx.axis_summary("Ax", {"CIPAxisState": 8, "CIPAxisFaults": (1 << 7) | (1 << 33),
                                   "CIPAxisFaultsRA": 1 << 5, "DCBusVoltage": 560.4})
        self.assertFalse(a["ok"])
        self.assertEqual(a["defauts"][0], "FLT S07 surcharge moteur (limite usine)")
        self.assertTrue(a["defauts"][1].startswith("FLT S33 (voir"))
        self.assertTrue(a["defauts"][2].startswith("FLT M05"))
        self.assertEqual((a["etat"], a["bus"]), ("DEFAUT MAJEUR", "560 V"))

    def test_start_inhibit(self):
        a = lx.axis_summary("Ax", {"CIPAxisState": 9, "CIPStartInhibitsRA": 2})
        self.assertTrue(a["inhibe"])
        self.assertTrue(a["inhibitions"][0].startswith("INHIBIT M01"))

    def test_symbolic_path(self):
        self.assertEqual(lx.symbolic_path("Ax_1.CIPAxisState")[0] * 2, len(lx.symbolic_path("Ax_1.CIPAxisState")) - 1)
        self.assertEqual(lx.symbolic_path("A[300]").hex(), "04" "91014100" "29002c01")
        self.assertEqual(lx.decode_atomic(bytes.fromhex("ca000000c842")), (100.0, "REAL"))

    def test_parse_value(self):
        self.assertEqual(lw.parse_value("1", "BOOL"), True)
        self.assertEqual(lw.parse_value("12,5", "REAL"), 12.5)
        self.assertEqual(lw.parse_value("16#FF", "DINT"), 255)
        with self.assertRaises(ValueError):
            lw.parse_value("3", "MY_UDT")


class LogixWebTests(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        self.s.connect("demo-logix", {})

    def test_brand(self):
        self.assertIn("axes", BRANDS["logix"]["fonctions"])

    def test_status_and_diagnostic(self):
        st = self.s.status()
        self.assertEqual((st["mode"], st["cle"]), ("RUN", "REM"))
        d = self.s.diagnostic()
        slots = [m["emplacement"] for m in d["modules"]]
        self.assertEqual(slots, [0, 1, 2, 3])
        self.assertFalse(d["modules"][3]["ok"])                     # EN2TR: I/O connection faulted
        self.assertTrue(any("Ax_Knife" in a for a in d["alertes"]))

    def test_axes_and_history(self):
        r = self.s.axes()
        by = {a["axe"]: a for a in r["axes"]}
        self.assertTrue(by["Ax_Infeed"]["ok"])
        self.assertIn("FLT S07", by["Ax_Knife"]["defauts"][0])
        self.assertTrue(by["Ax_Unwind"]["inhibe"])
        self.assertEqual(len(r["historique"]), 2)                   # faults already present at the first read
        self.s.plc.axes["Ax_Knife"]["CIPAxisFaults"] = 0
        self.s.plc.axes["Ax_Knife"]["CIPAxisState"] = 2
        r = self.s.axes()
        self.assertFalse(r["historique"][0]["actif"])
        self.assertIn("FLT S07", r["historique"][0]["texte"])

    def test_watch_and_write(self):
        v = self.s.values(["Recipe_Speed", "Ax_Knife.CIPAxisState", "Nope", "Line_Run"])
        self.assertEqual((v["valeurs"]["Recipe_Speed"], v["valeurs"]["Ax_Knife.CIPAxisState"], v["valeurs"]["Line_Run"]),
                         ("1250", "8", "1"))
        self.assertIn("Nope", v["erreurs"])
        r = self.s.write([{"adresse": "Recipe_Length", "valeur": "365"}, {"adresse": "Recipe_Speed", "valeur": "99.5"}])
        self.assertTrue(all(x["ok"] for x in r["resultats"]))
        with self.assertRaises(DriverError) as e:
            self.s.write([{"adresse": "SafetyOK", "valeur": "0"}])
        self.assertIn("securite", str(e.exception))

    def test_no_axes_on_other_brands(self):
        s = Session()
        s.connect("demo-delta", {})
        with self.assertRaises(DriverError):
            s.axes()


if __name__ == "__main__":
    unittest.main()
