"""Modicon (and any Modbus slave) in the web interface: read only, like tools/modicon.py.
Modbus RTU on a serial port, or Modbus TCP when an IP address is given.
  %MW = holding registers (03), %IW = input registers (04), %M = coils (01), %I = discrete inputs (02)."""
import re

from plc_online.protocols.modbus import Modicon, ModbusError, PlcRefused, RtuTransport, TcpTransport, open_serial, signed16
from plc_online.web import tsx_web


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "ip", "nom": "Adresse IP (Modbus TCP ; vide = liaison serie)", "defaut": "", "type": "texte"},
            {"cle": "port", "nom": "Port COM (Modbus RTU)", "defaut": "COM6", "type": "port"},
            {"cle": "baud", "nom": "Vitesse", "defaut": 19200},
            {"cle": "parite", "nom": "Parite (E, O ou N)", "defaut": "E", "type": "texte"},
            {"cle": "stop", "nom": "Bits de stop (1 ou 2)", "defaut": 1},
            {"cle": "esclave", "nom": "Esclave / unite Modbus", "defaut": 1}]

ZONES = {"MW": ("read_holding", True), "IW": ("read_input_registers", True), "M": ("read_coils", False),
         "I": ("read_discrete_inputs", False)}


def open_modicon(settings: dict):
    ip = str(settings.get("ip") or "").strip()
    try:
        unit, baud, stop = int(settings.get("esclave") or 1), int(settings.get("baud") or 19200), int(settings.get("stop") or 1)
    except (TypeError, ValueError):
        raise _drv().DriverError("Esclave, vitesse et bits de stop : des nombres") from None
    parity = str(settings.get("parite") or "E").strip().upper()[:1]
    if parity not in ("E", "O", "N") or stop not in (1, 2):
        raise _drv().DriverError("Parite : E, O ou N ; bits de stop : 1 ou 2")
    if ip:
        try:
            transport, where = TcpTransport(ip), f"{ip}:502"
        except OSError as e:
            raise _drv().DriverError(f"Pas de reponse de {ip}:502 : {e}") from None
    else:
        port = str(settings.get("port") or "COM6").strip()
        try:
            transport, where = RtuTransport(open_serial(port, baud, parity, stop)), f"{port} {baud} 8{parity}{stop}"
        except Exception as e:
            raise _drv().DriverError(tsx_web.port_error(port, e)) from None
    plc = Modicon(transport, unit)
    try:
        plc.read_holding(0, 1)
    except PlcRefused:
        pass                                  # answers, %MW0 refused: still a Modbus slave
    except (ModbusError, OSError) as e:
        plc.close()
        raise _drv().DriverError(f"Pas de reponse Modbus sur {where}, esclave {unit} : {e}") from None
    plc.nom = f"Modbus {where}, esclave {unit}"
    return plc


class DemoModicon:
    """A Modbus slave with a few values."""
    nom = "Modbus (demo), esclave 1"

    def __init__(self):
        self.mw = {0: 1250, 1: 42, 10: 0xFFFB}
        self.bits = {0: True, 3: True}

    def close(self):
        pass

    def read_holding(self, start, count):
        return [self.mw.get(start + i, 0) for i in range(count)]

    read_input_registers = read_holding

    def read_coils(self, start, count):
        return [self.bits.get(start + i, False) for i in range(count)]

    read_discrete_inputs = read_coils


def modicon_status(plc) -> dict:
    plc.read_holding(0, 1)
    return {"marque": "Modbus", "modele": plc.nom, "reference": "lecture seule", "mode": "?", "alertes": [],
            "voyants": [], "details": [("Acces", "lecture seule (%MW 03, %IW 04, %M 01, %I 02)")]}


ADDRESS = re.compile(r"^%?(MW|IW|M|I)(\d+)$", re.IGNORECASE)


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        address, _, fmt = str(t).partition(":")
        m = ADDRESS.match(address.strip())
        if not m:
            out["erreurs"][str(t)] = "adresse : %MW10, %IW0, %M5, %I2"
            continue
        zone, n = m.group(1).upper(), int(m.group(2))
        try:
            v = getattr(plc, ZONES[zone][0])(n, 1)[0]
        except PlcRefused as e:
            out["erreurs"][str(t)] = str(e)
            continue
        fmt = fmt.strip().lower() or "dec"
        if not ZONES[zone][1]:
            out["valeurs"][str(t)] = str(int(v))
        elif fmt == "hex":
            out["valeurs"][str(t)] = f"16#{v:04X}"
        elif fmt == "bin":
            b = f"{v:016b}"
            out["valeurs"][str(t)] = "2#" + "_".join(b[i:i + 4] for i in range(0, 16, 4))
        elif fmt == "ud":
            out["valeurs"][str(t)] = str(v)
        else:
            out["valeurs"][str(t)] = str(signed16(v))
    return out


def read_block(plc, zone, start, count) -> dict:
    zone = zone.upper().lstrip("%")
    read, words = ZONES[zone]
    values = getattr(plc, read)(start, count)
    return {"adresses": [f"%{zone}{start + i}" for i in range(count)],
            "valeurs": [signed16(v) if words else int(v) for v in values]}


class ModiconOps:
    def __init__(self, session):
        self.s = session

    def status(self, plc):
        return modicon_status(plc)

    def diagnostic(self):
        return {"alertes": [], "config_compilee": "", "modules": [], "systeme": [],
                "parametres_cpu": [("Acces", "Modbus generique : pas de diagnostic propre a l'automate")]}

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        raise _drv().DriverError("Modicon / Modbus : lecture seule dans cet outil")

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction non disponible en Modbus generique (lecture seule)")
