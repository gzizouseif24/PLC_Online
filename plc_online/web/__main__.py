"""py -3.14-32 -m plc_online.web [--port 8000] [--sans-navigateur]"""
import argparse
import threading
import webbrowser

from plc_online.web.server import serve


def main():
    p = argparse.ArgumentParser(description="PLC Online, interface web locale")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--sans-navigateur", action="store_true", help="ne pas ouvrir le navigateur")
    a = p.parse_args()
    httpd = serve(a.port)
    url = f"http://localhost:{a.port}"
    print(f"PLC Online : {url}   (Ctrl+C pour arreter)")
    if not a.sans_navigateur:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Arret.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
