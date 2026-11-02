"""Local web server (Python standard library only: runs in the 32-bit Python the S7 driver needs).

    py -3.14-32 -m plc_online.web            then http://localhost:8000 opens in the browser

JSON API:
    GET  /api/marques       brands and their connection settings
    POST /api/connexion     {"marque": "s7-300", "reglages": {"mpi": 2}}  -> status
    GET  /api/etat          status of the connected PLC ({"connecte": false} if none)
    GET  /api/diagnostic    diagnostic buffer, rack (configured vs plugged in), CPU parameters
    POST /api/valeurs       {"adresses": ["MW10", "EB0:bin"]} -> {"valeurs": {...}, "erreurs": {...}}
    POST /api/ecrire        {"ecritures": [{"adresse": "MW10", "valeur": "5"}]} -> old / read back
    GET  /api/tache         progress of the long operation running (save, compare, restore)
    GET  /api/programme/sauvegardes            program copies kept on the laptop
    GET  /api/programme/blocs?id=...           blocks of one copy
    POST /api/programme/sauvegarder {"note"}   copy the CPU program (background, see /api/tache)
    POST /api/programme/comparer {"id"}        CPU vs a copy (background)
    POST /api/programme/plan {"id", "avec_db"}  what a restore would load / leave
    POST /api/programme/restaurer {"id", "avec_db", "supprimer_en_trop"}   (background, CPU to STOP)
    GET  /api/programme/telecharger?id=...     one copy as a .zip
    POST /api/programme/importer {"fichiers": [{"nom", "contenu" (base64)}], "note"}   .bin files or a .zip
    POST /api/cpu/demarrer                     CPU to RUN (warm restart)
    POST /api/cpu/arreter                      CPU to STOP
    POST /api/cpu/horloge                      CPU clock set to the PC time
    POST /api/deconnexion
Errors: {"erreur": "message for the technician"} with HTTP 400 (bad request), 409 (not
connected), 502 (the PLC did not answer), 503 (a long operation is running).
"""
import base64
import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from plc_online.web.drivers import BRANDS, DriverError, Session

STATIC = Path(__file__).resolve().parent / "static"


def make_handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PLCOnline"

        def log_message(self, fmt, *args):          # keep the console quiet
            pass

        def send_json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 10 * 1024 * 1024:
                raise DriverError("Requete trop grosse")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise DriverError("Requete illisible") from None

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/marques":
                return self.send_json(200, BRANDS)
            if path == "/api/etat":
                if not session.connected:
                    return self.send_json(200, {"connecte": False})
                return self.api(lambda: session.status())
            if path == "/api/diagnostic":
                return self.api(lambda: session.diagnostic())
            if path == "/api/tache":
                return self.send_json(200, session.job_state())
            if path == "/api/programme/sauvegardes":
                return self.api(lambda: session.list_backups())
            if path == "/api/programme/telecharger":
                query = urllib.parse.parse_qs(self.path.partition("?")[2])
                try:
                    name, data = session.export_backup((query.get("id") or [""])[0])
                except DriverError as e:
                    return self.send_json(400, {"erreur": str(e)})
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/programme/blocs":
                query = urllib.parse.parse_qs(self.path.partition("?")[2])
                return self.api(lambda: session.backup_blocks((query.get("id") or [""])[0]))
            if path == "/":
                path = "/index.html"
            file = (STATIC / path.lstrip("/")).resolve()
            if STATIC not in file.parents or not file.is_file():
                return self.send_json(404, {"erreur": "Page introuvable"})
            body = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                             + ("; charset=utf-8" if file.suffix in (".html", ".js", ".css") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/connexion":
                def connect():
                    req = self.read_json()
                    return session.connect(str(req.get("marque", "")), dict(req.get("reglages") or {}))
                return self.api(connect)
            if path == "/api/valeurs":
                return self.api(lambda: session.values(list(self.read_json().get("adresses") or [])))
            if path == "/api/ecrire":
                return self.api(lambda: session.write(list(self.read_json().get("ecritures") or [])))
            if path == "/api/programme/sauvegarder":
                return self.api(lambda: session.save_program(str(self.read_json().get("note") or "")))
            if path == "/api/programme/comparer":
                return self.api(lambda: session.compare(str(self.read_json().get("id") or "")))
            if path == "/api/programme/plan":
                def plan():
                    req = self.read_json()
                    return session.restore_plan(str(req.get("id") or ""), bool(req.get("avec_db")))
                return self.api(plan)
            if path == "/api/programme/restaurer":
                def restore():
                    req = self.read_json()
                    return session.restore(str(req.get("id") or ""), bool(req.get("avec_db")),
                                           bool(req.get("supprimer_en_trop")))
                return self.api(restore)
            if path == "/api/cpu/demarrer":
                return self.api(lambda: session.start_cpu())
            if path == "/api/cpu/arreter":
                return self.api(lambda: session.stop_cpu())
            if path == "/api/cpu/horloge":
                return self.api(lambda: session.set_clock())
            if path == "/api/programme/importer":
                def do_import():
                    req = self.read_json()
                    files = []
                    for f in req.get("fichiers") or []:
                        try:
                            files.append((str(f.get("nom", "")), base64.b64decode(f.get("contenu", ""), validate=True)))
                        except (ValueError, TypeError):
                            raise DriverError(f"{f.get('nom', '?')} : contenu illisible") from None
                    return session.import_backup(files, str(req.get("note") or ""))
                return self.api(do_import)
            if path == "/api/deconnexion":
                return self.api(lambda: (session.disconnect(), {"ok": True})[1])
            return self.send_json(404, {"erreur": "Action inconnue"})

        def api(self, action):
            try:
                return self.send_json(200, action())
            except DriverError as e:
                text = str(e)
                code = (409 if text == "Pas connecte" else 503 if text.startswith("Operation en cours")
                        else 502 if "Liaison" in text or "CPU" in text else 400)
                return self.send_json(code, {"erreur": text})
            except Exception as e:                    # never leave the page without an answer
                return self.send_json(500, {"erreur": f"Erreur interne : {e}"})

    return Handler


def serve(port=8000, session=None, host="127.0.0.1", backups=None):
    """Local only (127.0.0.1): the PLC is not reachable from the network through the laptop."""
    return ThreadingHTTPServer((host, port), make_handler(session or Session(backups=backups)))
