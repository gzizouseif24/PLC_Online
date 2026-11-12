import struct
import unittest

from plc_online.protocols import wago as wg
from plc_online.web import wago_web


class FakeCoupler:
    """Modbus TCP transport answering like a 750-352 with a 750-461 and a DI8 module."""

    def __init__(self):
        self.holding = {0x2002: 0x1234, 0x2010: 6, 0x2011: 750, 0x2012: 352, 0x2013: 1, 0x2014: 6,
                        0x1020: 0, 0x1021: 0, 0x1022: 0, 0x1023: 32, 0x1024: 0, 0x1025: 8,
                        0x2030: 352, 0x2031: 461, 0x2032: 0x8801}
        self.inputs = {0: 0xFF16, 1: 0x2134}       # -23.4 deg C, broken wire
        self.dinputs = {0: 1, 3: 1}

    def exchange(self, unit, pdu):
        func, addr, n = struct.unpack(">BHH", pdu[:5])
        if func in (3, 4):
            table = self.holding if func == 3 else self.inputs
            vals = [table.get(a, 0) for a in range(addr, addr + n)]
            return bytes([func, 2 * n]) + struct.pack(f">{n}H", *vals)
        if func == 2:
            raw = bytearray((n + 7) // 8)
            for i in range(n):
                if self.dinputs.get(addr + i):
                    raw[i // 8] |= 1 << (i % 8)
            return bytes([2, len(raw)]) + raw
        return bytes([func | 0x80, 1])

    def close(self):
        pass


class WagoTest(unittest.TestCase):
    def setUp(self):
        self.plc = wg.Wago("10.0.0.5", transport=FakeCoupler())

    def test_identity_and_modules(self):
        self.assertTrue(self.plc.is_wago())
        self.assertEqual(self.plc.identity()["modele"], "750-352")
        mods = self.plc.modules()
        self.assertEqual(mods[0], 352)
        self.assertEqual((mods[1]["code"], mods[1]["iw"]), ("461", 0))
        self.assertEqual((mods[2]["entrees_bits"], mods[2]["ix"]), (8, 0))

    def test_pt100(self):
        self.assertEqual(wg.pt100(0xFF16), ("-23.4 °C", True))
        self.assertFalse(wg.pt100(0x2134)[1])
        self.assertFalse(wg.pt100(0x8001)[1])

    def test_diagnostic_and_watch(self):
        d = wago_web.wago_diagnostic(self.plc)
        row = d["modules"][1]
        self.assertEqual(row["adresse"], "IW0..IW1")
        self.assertIn("-23.4", row["en_place"]["nom"])
        self.assertFalse(row["ok"])                          # channel 2 broken wire
        self.assertEqual(row["variables"][1]["adresse"], "IW1:pt100")
        self.assertEqual(d["modules"][2]["en_place"]["nom"], "entrees : 1 0 0 1 0 0 0 0")
        v = wago_web.read_values(self.plc, ["IW0:pt100", "I3", "IW0", "X1"])
        self.assertEqual(v["valeurs"]["IW0:pt100"], "-23.4 °C")
        self.assertEqual(v["valeurs"]["I3"], "1")
        self.assertEqual(v["valeurs"]["IW0"], "-234")
        self.assertIn("X1", v["erreurs"])

    def test_session_demo(self):
        from plc_online.web.drivers import Session
        s = Session()
        st = s.connect("demo-wago", {})
        self.assertEqual(st["modele"], "WAGO 750-352")
        self.assertTrue(any("rupture" in a for a in st["alertes"]))
        self.assertEqual(s.values(["IW0:pt100"])["valeurs"]["IW0:pt100"], "23.4 °C")
        r = s.write([{"adresse": "Q1", "valeur": "1"}])
        self.assertTrue(r["resultats"][0]["ok"])
        d = s.diagnostic()
        self.assertTrue(len(d["modules"]) == 4)
        raw = {r["adresse"]: r for r in d["systeme"]}
        self.assertTrue(raw["0x2000 x9 (03)"]["valeur"].startswith("1234 0000"))
        self.assertIn("exception", raw["0x2000 x9 (04)"]["valeur"])


if __name__ == "__main__":
    unittest.main()


class PickyCoupler(FakeCoupler):
    """Refuses the firmware words 0x2013/14, 0x1020 and blocks of more than 4 words on 0x2030."""

    def exchange(self, unit, pdu):
        func, addr, n = struct.unpack(">BHH", pdu[:5])
        if func in (3, 4) and (addr <= 0x2013 < addr + n or addr <= 0x1020 < addr + n
                               or (addr == 0x2030 and n > 4)):
            return bytes([func | 0x80, 2])
        return super().exchange(unit, pdu)


class PickyTest(unittest.TestCase):
    def test_partial_answers(self):
        plc = wg.Wago("10.0.0.5", transport=PickyCoupler())
        self.assertEqual(plc.identity()["modele"], "750-352")
        self.assertEqual(plc.led_error()[0], None)
        self.assertEqual([m["code"] for m in plc.modules()[1:]], ["461", "8801"])
        st = wago_web.wago_status(plc)
        self.assertIn("Modules", [k for k, _ in st["details"]])
        d = wago_web.wago_diagnostic(plc)
        self.assertIn("0x1020", d["alertes"][0])
