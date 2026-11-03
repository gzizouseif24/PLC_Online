"""WAGO 750 couplers in the web interface: status, module list, watch table (Modbus TCP)."""
import ipaddress
import re


from plc_online.protocols import wago as wg
from plc_online.protocols.modbus import ModbusError, PlcRefused, signed16


def _drv():
    from plc_online.web import drivers
    return drivers


SETTINGS = [{"cle": "ip", "nom": "Adresse IP du coupleur", "defaut": "192.168.1.1", "type": "texte"}]


def open_wago(settings: dict):
    ip = str(settings.get("ip") or "").strip()
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        raise _drv().DriverError("Adresse IP du coupleur : ex. 192.168.1.10 (ou bouton Chercher)") from None
    try:
        plc = wg.Wago(ip)
    except OSError as e:
        raise _drv().DriverError(f"Pas de reponse de {ip} sur le port 502 ({e}). Le PC est-il dans le meme reseau "
                                 "(meme debut d'adresse, ex. 192.168.1.x) ? Bouton Chercher.") from None
    if not plc.is_wago():
        plc.close()
        raise _drv().DriverError(f"{ip} repond en Modbus TCP mais ce n'est pas un coupleur WAGO 750")
    return plc


def search(extra=()):
    try:
        return wg.search(extra)
    except OSError as e:
        raise _drv().DriverError(f"Recherche impossible : {e}") from None


# ---- demo ----

class DemoWago:
    """750-352 + 750-461 (channel 1 at 23.4 deg C, channel 2 broken wire) + DI8 + DO8."""

    host = "demo"

    def __init__(self):
        self.out_words = [0] * 8
        self.out_bits = [False] * 16
        self.in_bits = [True, False, False, True] + [False] * 12

    def is_wago(self):
        return True

    def identity(self):
        return {"modele": "750-352", "serie": 750, "article": 352, "firmware": "01.06 (index 6)"}

    def led_error(self):
        return 0, 0

    def image_sizes(self):
        return {"analog_out_bits": 0, "analog_in_bits": 32, "digital_out_bits": 8, "digital_in_bits": 8}

    def modules(self):
        return [352] + wg.layout([wg.describe(461), wg.describe(0x8801), wg.describe(0x8802)])

    def input_words(self, start, count):
        image = [234, 0x2134]
        if start + count > len(image):
            raise PlcRefused(2)
        return image[start:start + count]

    def output_words(self, start, count):
        return self.out_words[start:start + count]

    def input_bits(self, start, count):
        return self.in_bits[start:start + count]

    def output_bits(self, start, count):
        return self.out_bits[start:start + count]

    def write_output_word(self, n, value):
        raise PlcRefused(2)

    def write_output_bit(self, n, on):
        self.out_bits[n] = bool(on)

    def close(self):
        pass


# ---- status / diagnostic ----

def module_values(plc, m) -> list:
    """[(text, ok)] per channel of a module with known words (750-461 decoded in deg C)."""
    out = []
    try:
        if m.get("iw") is not None and m["mots_e"]:
            words = plc.input_words(m["iw"], m["mots_e"])
            for i, w in enumerate(words):
                if m["code"] == "461":
                    text, ok = wg.pt100(w)
                else:
                    text, ok = f"{signed16(w)}  (16#{w:04X})", True
                out.append((f"voie {i + 1} : {text}", ok))
        elif m.get("ix") is not None:
            bits = plc.input_bits(m["ix"], m["entrees_bits"])
            out.append(("entrees : " + " ".join(str(int(b)) for b in bits), True))
        elif m.get("qx") is not None:
            bits = plc.output_bits(m["qx"], m["sorties_bits"])
            out.append(("sorties : " + " ".join(str(int(b)) for b in bits), True))
        elif m.get("qw") is not None and m["mots_s"]:
            words = plc.output_words(m["qw"], m["mots_s"])
            out += [(f"sortie {i + 1} : {signed16(w)}", True) for i, w in enumerate(words)]
    except ModbusError as e:
        out.append((f"non lu ({e})", False))
    return out


def address_text(m) -> str:
    if m.get("iw") is not None and m["mots_e"]:
        return f"IW{m['iw']}" + (f"..IW{m['iw'] + m['mots_e'] - 1}" if m["mots_e"] > 1 else "")
    if m.get("qw") is not None and m["mots_s"]:
        return f"QW{m['qw']}" + (f"..QW{m['qw'] + m['mots_s'] - 1}" if m["mots_s"] > 1 else "")
    if m.get("ix") is not None:
        return f"I{m['ix']}..I{m['ix'] + m['entrees_bits'] - 1}"
    if m.get("qx") is not None:
        return f"Q{m['qx']}..Q{m['qx'] + m['sorties_bits'] - 1}"
    return ""


def module_vars(m) -> list:
    if m.get("iw") is not None and m["mots_e"]:
        fmt = ":pt100" if m["code"] == "461" else ""
        return [{"adresse": f"IW{m['iw'] + i}{fmt}", "libelle": f"{m['nom']} voie {i + 1}"} for i in range(m["mots_e"])]
    if m.get("qw") is not None and m["mots_s"]:
        return [{"adresse": f"QW{m['qw'] + i}", "libelle": f"{m['nom']} sortie {i + 1}"} for i in range(m["mots_s"])]
    if m.get("ix") is not None:
        return [{"adresse": f"I{m['ix'] + i}", "libelle": f"{m['type']} voie {i + 1}"} for i in range(m["entrees_bits"])]
    if m.get("qx") is not None:
        return [{"adresse": f"Q{m['qx'] + i}", "libelle": f"{m['type']} voie {i + 1}"} for i in range(m["sorties_bits"])]
    return []


def wago_diagnostic(plc) -> dict:
    out = {"alertes": [], "parametres_cpu": [], "config_compilee": "", "modules": []}
    code, arg = plc.led_error()
    if code:
        out["alertes"].append(f"Voyant I/O : code {code}, argument {arg} : {wg.LED_ERRORS.get(code, 'code inconnu')}"
                              + (f" (module {arg})" if code in (4, 5) and arg else ""))
    mods = plc.modules()
    coupler, mods = mods[0], mods[1:]
    out["modules"].append({"emplacement": 0, "type": "coupleur", "adresse": "",
                           "configure": {"code": "", "nom": f"750-{coupler}", "references": "coupleur Modbus TCP"},
                           "en_place": {"code": "", "nom": plc.host, "references": ""},
                           "etat": f"code {code}" if code else "ok", "ok": not code, "variables": []})
    unknown = False
    for i, m in enumerate(mods, 1):
        values = module_values(plc, m)
        bad = [t for t, ok in values if not ok]
        if not m["digital"] and m["mots_e"] is None and m["code"] not in wg.ANALOG_OUT and m["code"] not in wg.PASSIVE:
            unknown = True
        out["modules"].append({
            "emplacement": i, "type": m["type"], "adresse": address_text(m),
            "configure": {"code": m["code"], "nom": m["nom"], "references": m["type"]},
            "en_place": {"code": "", "nom": " / ".join(t for t, _ in values) or "-", "references": ""},
            "etat": " ; ".join(bad) if bad else "ok", "ok": not bad, "variables": module_vars(m)})
        for t in bad:
            out["alertes"].append(f"Module {i} ({m['nom']}) {t}")
    if unknown:
        out["alertes"].append("Un module de taille inconnue : adresses des modules suivants non calculees")
    sizes = plc.image_sizes()
    out["parametres_cpu"] = [
        ("Entrees analogiques / complexes", f"{sizes['analog_in_bits'] // 16} mots"),
        ("Sorties analogiques / complexes", f"{sizes['analog_out_bits'] // 16} mots"),
        ("Entrees TOR", f"{sizes['digital_in_bits']} bits"),
        ("Sorties TOR", f"{sizes['digital_out_bits']} bits"),
    ]
    return out


def wago_status(plc) -> dict:
    ident = plc.identity()
    code, arg = plc.led_error()
    out = {"marque": "WAGO", "modele": f"WAGO {ident['modele']}", "reference": f"firmware {ident['firmware']}",
           "mode": "DEFAUT" if code else "OK", "alertes": [], "voyants": [], "details": []}
    if code:
        out["alertes"].append(f"Voyant I/O en defaut : code {code} / {arg} : {wg.LED_ERRORS.get(code, 'code inconnu')}")
    out["voyants"].append({"nom": "I/O", "texte": "bus interne et modules", "allume": True, "clignote": bool(code),
                           "defaut": bool(code)})
    try:
        mods = plc.modules()[1:]
        sizes = plc.image_sizes()
        out["details"] += [("Adresse IP", plc.host), ("Modules", str(len(mods))),
                           ("Image entrees", f"{sizes['analog_in_bits'] // 16} mots + {sizes['digital_in_bits']} bits"),
                           ("Image sorties", f"{sizes['analog_out_bits'] // 16} mots + {sizes['digital_out_bits']} bits")]
        for i, m in enumerate(mods, 1):
            if m["code"] == "461" and m.get("iw") is not None:
                for t, ok in module_values(plc, m):
                    if not ok:
                        out["alertes"].append(f"Module {i} (750-461) {t}")
    except ModbusError as e:
        out["alertes"].append(f"Modules non lus : {e}")
    return out


# ---- watch table ----

ADDRESS = re.compile(r"^(IW|QW|I|Q)(\d+)$", re.IGNORECASE)
WORD_FORMATS = ["dec", "ud", "hex", "bin", "pt100"]


class WagoVar:
    """'IW0', 'IW1:pt100', 'QW0', 'I3', 'Q0'."""

    def __init__(self, text: str):
        self.text = text.strip()
        address, _, fmt = self.text.partition(":")
        m = ADDRESS.match(address.strip())
        if not m:
            raise ValueError("adresse WAGO : IW0 (mot d'entree), QW0 (mot de sortie), I0 (bit d'entree), Q0 (bit de sortie)")
        self.zone, self.addr = m.group(1).upper(), int(m.group(2))
        self.bit = self.zone in ("I", "Q")
        self.fmt = "" if self.bit else (fmt.strip().lower() or "dec")
        if not self.bit and self.fmt not in WORD_FORMATS:
            raise ValueError(f"format {self.fmt} : {', '.join(WORD_FORMATS)}")

    def decode(self, v) -> str:
        if self.bit:
            return str(int(v))
        if self.fmt == "pt100":
            text, ok = wg.pt100(v)
            return text if ok else ("850.0 °C (rupture fil / hors plage)" if v == 0x2134 else "court-circuit / < -200 °C")
        if self.fmt == "ud":
            return str(v)
        if self.fmt == "hex":
            return f"16#{v:04X}"
        if self.fmt == "bin":
            t = f"{v:016b}"
            return "2#" + "_".join(t[i:i + 4] for i in range(0, 16, 4))
        return str(signed16(v))

    def encode(self, text: str) -> int:
        t = text.strip().lower().replace("_", "")
        if self.bit:
            if t not in ("0", "1"):
                raise ValueError("0 ou 1")
            return int(t)
        try:
            if t.startswith("16#") or t.startswith("0x"):
                v = int(t[3:] if t.startswith("16#") else t[2:], 16)
            elif t.startswith("2#"):
                v = int(t[2:], 2)
            elif self.fmt == "pt100":
                v = round(float(t.replace(",", ".")) * 10)
            else:
                v = int(t)
        except ValueError:
            raise ValueError("valeur illisible") from None
        if not -32768 <= v <= 65535:
            raise ValueError("hors -32768..65535")
        return v & 0xFFFF


def _read(plc, var):
    if var.zone == "IW":
        return plc.input_words(var.addr, 1)[0]
    if var.zone == "QW":
        return plc.output_words(var.addr, 1)[0]
    if var.zone == "I":
        return plc.input_bits(var.addr, 1)[0]
    return plc.output_bits(var.addr, 1)[0]


def read_values(plc, texts) -> dict:
    out = {"valeurs": {}, "erreurs": {}}
    for t in texts:
        try:
            v = WagoVar(str(t))
        except ValueError as e:
            out["erreurs"][str(t)] = str(e)
            continue
        try:
            out["valeurs"][str(t)] = v.decode(_read(plc, v))
        except PlcRefused as e:
            out["erreurs"][str(t)] = f"hors de l'image du coupleur ({e})"
    return out


def write_values(plc, items) -> dict:
    results = []
    for it in items:
        text = str(it.get("adresse", ""))
        try:
            v = WagoVar(text)
            if v.zone in ("IW", "I"):
                raise ValueError("une entree ne s'ecrit pas : seulement QW / Q")
            value = v.encode(str(it.get("valeur", "")))
        except ValueError as e:
            raise _drv().DriverError(f"{text} : {e}") from None
        old = _read(plc, v)
        if v.bit:
            plc.write_output_bit(v.addr, value)
        else:
            plc.write_output_word(v.addr, value)
        back = _read(plc, v)
        results.append({"adresse": text, "avant": v.decode(old), "relu": v.decode(back),
                        "ok": int(back) == value})
    return {"resultats": results, "run": False}


class WagoOps:
    """Session calls for a WAGO coupler (status, diagnostic, watch table)."""

    def __init__(self, session):
        self.s = session

    def status(self, plc):
        return wago_status(plc)

    def diagnostic(self):
        return self.s._call(wago_diagnostic)

    def values(self, texts):
        return self.s._call(lambda plc: read_values(plc, texts))

    def write(self, items):
        return self.s._call(lambda plc: write_values(plc, items))

    def __getattr__(self, name):
        raise _drv().DriverError("Fonction non disponible pour un coupleur WAGO")


