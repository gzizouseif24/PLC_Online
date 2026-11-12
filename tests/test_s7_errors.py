"""Diagnostic buffer details and empty error OBs (S7-300)."""
import tempfile
import time
import unittest

from plc_online.protocols import s7_program as program
from plc_online.protocols.s7 import parse_block
from plc_online.protocols.s7_events import event_details
from plc_online.web.drivers import DriverError, Session


class EventDetailsTest(unittest.TestCase):
    def test_io_access_error(self):
        d = dict(event_details(0x2942, bytes.fromhex("017A8C2000CA0069001E")))
        self.assertEqual(d["Type"], "erreur externe, evenement entrant")
        self.assertEqual((d["OB demande"], d["Classe de priorite"]), ("OB122", "1"))
        self.assertEqual(d["Acces"], "zone P (peripherie), acces mot")
        self.assertEqual((d["Adresse accedee"], d["Bloc"]), ("202", "FC105"))

    def test_other_events_keep_the_type(self):
        self.assertEqual(event_details(0x4302, bytes(10)), [("Type", "evenement entrant")])


class ErrorObTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Session(backups=self.tmp.name)
        self.s.connect("demo", {})

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self):
        while not self.s.job_state()["fini"]:
            time.sleep(0.02)
        return self.s.job_state()["resultat"]

    def test_empty_ob_block(self):
        b = parse_block(program.empty_ob(122))
        self.assertEqual((b["type"], b["numero"], b["corps"][:2]), (0x08, 122, b"\x65\x00"))

    def test_load_only_missing(self):
        self.s.load_error_obs([122, 1, 85])               # OB1 is not an error OB: ignored
        r = self.wait()
        self.assertEqual((r["charges"], r["erreur"]), (["OB85", "OB122"], ""))
        self.s.load_error_obs([122])
        self.assertEqual(self.wait()["deja"], ["OB122"])
        self.assertIn(122, self.s.plc.list_blocks("OB"))

    def test_key_on_run(self):
        self.s.plc.protection = lambda: {"cle": "RUN"}
        self.s.load_error_obs([122])
        self.assertIn("RUN-P", self.wait()["erreur"])

    def test_diagnostic_details_in_page_data(self):
        ev = self.s.diagnostic()["evenements"][0]
        self.assertIn(["Adresse accedee", "202"], ev["details"])
        with self.assertRaises(DriverError):
            self.s.load_error_obs([])


if __name__ == "__main__":
    unittest.main()
