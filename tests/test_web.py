"""Local web interface: API and pages, on a demo / fake S7-300 (no PLC, no browser)."""
import json
import threading
import unittest
import urllib.error
import urllib.request

from plc_online.protocols.s7 import S7Error
from plc_online.web.drivers import DemoS7, DriverError, Session, read_values, s7_diagnostic, s7_status, write_values
from plc_online.web.server import serve


class LostLink(DemoS7):
    """Answers once (the connection), then the cable is pulled."""
    calls = 0

    def leds(self):
        self.calls += 1
        if self.calls > 1:
            raise S7Error("pas de reponse")
        return super().leds()


class StatusTests(unittest.TestCase):
    def test_status_of_bench_cpu(self):
        plc = DemoS7()
        plc.running = False
        plc.leds = lambda: {"SF": (1, 0), "RUN": (0, 0), "STOP": (1, 0), "FRCE": (0, 1)}
        s = s7_status(plc)
        self.assertEqual((s["modele"], s["reference"], s["mode"]), ("CPU 315-2 DP", "6ES7 315-2AF03-0AB0", "STOP"))
        self.assertEqual(s["alertes"], ["FRCE allume : Forcage actif", "SF allume : Defaut groupe"])
        self.assertIn(("Somme programme", "6647-0600"), s["details"])
        frce = next(v for v in s["voyants"] if v["nom"] == "FRCE")
        self.assertTrue(frce["clignote"] and frce["defaut"])


class DiagnosticTests(unittest.TestCase):
    def test_bench_rack_with_wrong_config(self):
        d = s7_diagnostic(DemoS7())
        rows = {r["emplacement"]: r for r in d["modules"]}
        self.assertTrue(rows[2]["ok"])
        self.assertEqual((rows[4]["adresse"], rows[4]["configure"]["nom"], rows[4]["en_place"]["nom"], rows[4]["ok"]),
                         ("AB0..AB3", "SM 322 DO32x...", "SM 321 DI32x...", False))
        self.assertIn("6ES7 322-1BL00-0AA0", rows[4]["configure"]["references"])
        self.assertEqual(rows[5]["etat"], "MODULE DIFFERENT DE LA CONFIGURATION")
        self.assertEqual(d["alertes"], ["2 emplacement(s) en defaut dans le rack 0"])
        self.assertEqual(d["config_compilee"], "2026-09-30 15:36:16")
        self.assertEqual([e["id"] for e in d["evenements"]], ["4302", "5961", "5961", "4301"])
        self.assertEqual(d["parametres_cpu"][1], ("Memento de cadence", "non utilise"))

    def test_one_refused_read_does_not_hide_the_rest(self):
        plc = DemoS7()

        def refused(*a):
            raise S7Error("objet inexistant")
        plc.diagnostic_buffer = refused
        plc.upload_block = refused
        d = s7_diagnostic(plc)
        self.assertEqual(len(d["modules"]), 3)
        self.assertIn("Tampon de diagnostic non lu : objet inexistant", d["alertes"])
        self.assertIn("Configuration materielle non lue : objet inexistant", d["alertes"])


class WatchTests(unittest.TestCase):
    def test_read_values_and_errors(self):
        r = read_values(DemoS7(), ["MW10", "DB10.DBD4:real", "M0.0", "MW5000", "XX9"])
        self.assertEqual({k: r["valeurs"][k] for k in ("MW10", "DB10.DBD4:real", "M0.0")},
                         {"MW10": "1234", "DB10.DBD4:real": "21.5", "M0.0": "0"})
        self.assertIn("hors de la memoire", r["erreurs"]["MW5000"])
        self.assertIn("adresse non comprise", r["erreurs"]["XX9"])

    def test_write_reads_back(self):
        plc = DemoS7()
        r = write_values(plc, [{"adresse": "MW10", "valeur": "-5"}, {"adresse": "M0.1", "valeur": "1"}])
        self.assertEqual([(x["adresse"], x["ancien"], x["relu"], x["ok"]) for x in r["resultats"]],
                         [("MW10", "1234", "-5", True), ("M0.1", "0", "1", True)])
        self.assertEqual(read_values(plc, ["MW10", "M0.1"])["valeurs"], {"MW10": "-5", "M0.1": "1"})

    def test_bad_value_writes_nothing(self):
        plc = DemoS7()
        with self.assertRaisesRegex(DriverError, "MB0 : 300 ne tient pas"):
            write_values(plc, [{"adresse": "MW10", "valeur": "1"}, {"adresse": "MB0", "valeur": "300"}])
        self.assertEqual(read_values(plc, ["MW10"])["valeurs"]["MW10"], "1234")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.opened = []

        def opener(brand, settings):
            if settings.get("mpi") == 9:
                raise DriverError("pas de CPU a l'adresse MPI 9")
            plc = LostLink() if settings.get("mpi") == 7 else DemoS7()
            self.opened.append((brand, settings))
            return plc

        self.httpd = serve(0, Session(opener))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def json(self, method, path, body=None):
        code, raw = self.call(method, path, body)
        return code, json.loads(raw)

    def test_page_and_files(self):
        for path in ("/", "/app.js", "/style.css"):
            code, raw = self.call("GET", path)
            self.assertEqual(code, 200, path)
        self.assertIn(b"PLC Online", self.call("GET", "/")[1])
        self.assertEqual(self.call("GET", "/../README.md")[0], 404)

    def test_connect_status_disconnect(self):
        self.assertEqual(self.json("GET", "/api/etat"), (200, {"connecte": False}))
        code, s = self.json("POST", "/api/connexion", {"marque": "s7-300", "reglages": {"mpi": 2}})
        self.assertEqual((code, s["modele"], s["connecte"]), (200, "CPU 315-2 DP", True))
        self.assertEqual(self.opened, [("s7-300", {"mpi": 2})])
        self.assertEqual(self.json("GET", "/api/etat")[1]["mode"] in ("RUN", "STOP"), True)
        self.assertEqual(self.json("POST", "/api/deconnexion"), (200, {"ok": True}))
        self.assertEqual(self.json("GET", "/api/etat")[1], {"connecte": False})

    def test_errors_for_the_technician(self):
        code, e = self.json("POST", "/api/connexion", {"marque": "s7-300", "reglages": {"mpi": 9}})
        self.assertEqual((code, e["erreur"]), (502, "pas de CPU a l'adresse MPI 9"))
        code, e = self.json("POST", "/api/connexion", {"marque": "omron"})
        self.assertEqual((code, e["erreur"]), (400, "Marque non disponible : omron"))
        self.assertEqual(self.json("POST", "/api/connexion", {"marque": "s7-300", "reglages": {"mpi": 7}})[0], 200)
        code, e = self.json("GET", "/api/etat")
        self.assertEqual((code, e["erreur"]), (502, "Liaison perdue : pas de reponse"))
        self.assertEqual(self.json("GET", "/api/etat")[1], {"connecte": False})

    def test_diagnostic(self):
        self.assertEqual(self.json("GET", "/api/diagnostic"), (409, {"erreur": "Pas connecte"}))
        self.json("POST", "/api/connexion", {"marque": "s7-300", "reglages": {"mpi": 2}})
        code, d = self.json("GET", "/api/diagnostic")
        self.assertEqual((code, len(d["modules"]), len(d["evenements"])), (200, 3, 4))

    def test_watch_api(self):
        self.json("POST", "/api/connexion", {"marque": "s7-300", "reglages": {"mpi": 2}})
        code, r = self.json("POST", "/api/valeurs", {"adresses": ["MW10", "XX"]})
        self.assertEqual((code, r["valeurs"], list(r["erreurs"])), (200, {"MW10": "1234"}, ["XX"]))
        code, r = self.json("POST", "/api/ecrire", {"ecritures": [{"adresse": "MW10", "valeur": "7"}]})
        self.assertEqual((code, r["resultats"][0]["relu"], r["run"] in (True, False)), (200, "7", True))
        code, r = self.json("POST", "/api/ecrire", {"ecritures": []})
        self.assertEqual((code, r["erreur"]), (400, "Rien a ecrire"))

    def test_brands(self):
        code, b = self.json("GET", "/api/marques")
        self.assertTrue(b["s7-300"]["disponible"])
        self.assertFalse(b["omron"]["disponible"])


if __name__ == "__main__":
    unittest.main()
