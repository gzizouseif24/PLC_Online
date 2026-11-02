"""What the web interface needs from a PLC, the same for every brand.

A driver is opened with `connect(settings)` and gives `status()`: a plain dict the page can
show without knowing the brand. S7-300 first; Omron, TSX... plug in with the same dict.
"""
import random
import threading

from plc_online.protocols.s7 import S7300, S7Error
from plc_online.protocols.s7_catalogue import catalog_name

FAULT_LEDS = {"SF": "Defaut groupe", "BAF": "Defaut pile", "BUS1F": "Defaut bus MPI/DP",
              "BUS2F": "Defaut bus DP", "FRCE": "Forcage actif"}
OTHER_LEDS = {"RUN": "RUN", "STOP": "STOP"}


class DriverError(Exception):
    """Message for the technician (French), shown as is on the page."""


def s7_status(plc) -> dict:
    """Brand-neutral status of an S7-300 (same reads as `tools/siemens.py etat`)."""
    out = {"marque": "Siemens S7-300", "alertes": [], "voyants": [], "details": []}
    try:
        info = plc.identity()
        out["reference"] = info.get("reference", "")
        out["modele"] = info.get("nom_module") or catalog_name(info.get("reference")) or "CPU S7-300"
        if info.get("firmware"):
            out["details"].append(("Firmware", info["firmware"]))
        if info.get("materiel"):
            out["details"].append(("Version materiel", info["materiel"]))
        if info.get("numero_serie"):
            out["details"].append(("N° de serie", info["numero_serie"]))
        if info.get("nom_automate"):
            out["details"].append(("Station", info["nom_automate"]))
    except S7Error as e:
        out["modele"] = "CPU S7-300"
        out["alertes"].append(f"Identification non lue : {e}")

    leds = plc.leds()
    on = {name for name, (state, blink) in leds.items() if state or blink}
    out["mode"] = "RUN" if "RUN" in on and "STOP" not in on else "STOP" if "STOP" in on else "?"
    for name, (state, blink) in leds.items():
        fault = name in FAULT_LEDS
        out["voyants"].append({"nom": name, "texte": FAULT_LEDS.get(name) or OTHER_LEDS.get(name, name),
                               "allume": bool(state or blink), "clignote": bool(blink), "defaut": fault})
    for name in sorted(on & set(FAULT_LEDS)):
        out["alertes"].append(f"{name} allume : {FAULT_LEDS[name]}")

    try:
        p = plc.protection()
        out["details"].append(("Cle", p["cle"]))
        out["details"].append(("Protection", f"niveau {p['protection_effective']}"
                               + (" (sans mot de passe)" if p["protection_parametree"] == 0 else "")))
        hw, prog = p["somme_config_materielle"], p["somme_programme"]
        if any(hw + prog):
            out["details"].append(("Somme programme", f"{prog[0]:04X}-{prog[1]:04X}"))
            out["details"].append(("Somme config materielle", f"{hw[0]:04X}-{hw[1]:04X}"))
    except S7Error as e:
        out["alertes"].append(f"Cle et protection non lues : {e}")
    return out


class DemoS7:
    """Simulated S7-300 (bench values of 31/10/2026) to show and test the page without a PLC.
    The mode changes now and then, SF lights up in STOP."""

    def __init__(self):
        self.running = True

    def identity(self):
        return {"reference": "6ES7 315-2AF03-0AB0", "firmware": "V1.2.0"}

    def leds(self):
        if random.random() < 0.05:
            self.running = not self.running
        r = self.running
        return {"SF": (0 if r else 1, 0), "RUN": (1 if r else 0, 0), "STOP": (0 if r else 1, 0),
                "FRCE": (0, 0), "BAF": (0, 0)}

    def protection(self):
        return {"cle": "RUN-P", "protection_effective": 1, "protection_parametree": 0,
                "somme_config_materielle": (0x5656, 0x2A00), "somme_programme": (0x6647, 0x0600),
                "sommes_valides": False}

    def close(self):
        pass


BRANDS = {
    "s7-300": {"nom": "Siemens S7-300", "disponible": True,
               "reglages": [{"cle": "mpi", "nom": "Adresse MPI", "defaut": 2},
                            {"cle": "rack", "nom": "Rack", "defaut": 0},
                            {"cle": "slot", "nom": "Emplacement CPU", "defaut": 2}]},
    "demo": {"nom": "Demo (S7-300 simule)", "disponible": True, "reglages": []},
    "omron": {"nom": "Omron CJ1M", "disponible": False, "reglages": []},
    "tsx": {"nom": "Schneider TSX", "disponible": False, "reglages": []},
    "panasonic": {"nom": "Panasonic FP0R", "disponible": False, "reglages": []},
}


class Session:
    """The one PLC the laptop is connected to. Calls are serialized: the link is not shared."""

    def __init__(self, opener=None):
        self.lock = threading.Lock()
        self.plc, self.brand, self.settings = None, None, {}
        self.opener = opener or open_plc

    def connect(self, brand: str, settings: dict) -> dict:
        if brand not in BRANDS or not BRANDS[brand]["disponible"]:
            raise DriverError(f"Marque non disponible : {brand}")
        with self.lock:
            self._close()
            self.plc = self.opener(brand, settings)
            self.brand, self.settings = brand, settings
        return self.status()

    @property
    def connected(self) -> bool:
        return self.plc is not None

    def status(self) -> dict:
        with self.lock:
            if self.plc is None:
                raise DriverError("Pas connecte")
            try:
                out = s7_status(self.plc)
            except S7Error as e:
                self._close()
                raise DriverError(f"Liaison perdue : {e}") from None
        out["connecte"] = True
        out["connexion"] = {"marque": self.brand, **self.settings}
        return out

    def disconnect(self):
        with self.lock:
            self._close()

    def _close(self):
        if self.plc is not None:
            try:
                self.plc.close()
            except Exception:
                pass
        self.plc, self.brand = None, None


def open_plc(brand: str, settings: dict):
    if brand == "demo":
        return DemoS7()
    try:
        mpi, rack, slot = (int(settings.get(k, d)) for k, d in (("mpi", 2), ("rack", 0), ("slot", 2)))
    except (TypeError, ValueError):
        raise DriverError("Adresse MPI, rack et emplacement : des nombres") from None
    try:
        return S7300(mpi, rack, slot).open()
    except S7Error as e:
        raise DriverError(str(e)) from None
