"""Local web server (Python standard library only: runs in the 32-bit Python the S7 driver needs).

    py -3.14-32 -m plc_online.web            then http://localhost:8000 opens in the browser

JSON API:
    GET  /api/marques       brands and their connection settings
    POST /api/connexion     {"marque": "s7-300", "reglages": {"mpi": 2}}  -> status
    GET  /api/etat          status of the connected PLC ({"connecte": false} if none)
    POST /api/deconnexion
Errors: {"erreur": "message for the technician"} with HTTP 400 (bad request), 409 (not
connected), 502 (the PLC did not answer).
"""
import json
import mimetypes
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
            if path == "/api/deconnexion":
                return self.api(lambda: (session.disconnect(), {"ok": True})[1])
            return self.send_json(404, {"erreur": "Action inconnue"})

        def api(self, action):
            try:
                return self.send_json(200, action())
            except DriverError as e:
                text = str(e)
                code = 409 if text == "Pas connecte" else 502 if "Liaison" in text or "CPU" in text else 400
                return self.send_json(code, {"erreur": text})
            except Exception as e:                    # never leave the page without an answer
                return self.send_json(500, {"erreur": f"Erreur interne : {e}"})

    return Handler


def serve(port=8000, session=None, host="127.0.0.1"):
    """Local only (127.0.0.1): the PLC is not reachable from the network through the laptop."""
    return ThreadingHTTPServer((host, port), make_handler(session or Session()))
