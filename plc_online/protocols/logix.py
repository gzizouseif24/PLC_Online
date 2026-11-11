"""Allen-Bradley ControlLogix / GuardLogix over EtherNet/IP, with pycomm3 (pip install pycomm3).

What it reads: controller identity and mode (key switch, major / minor fault), the modules of the
1756 chassis slot by slot, tags by name, and the CIP Motion axes (Kinetix) through their
AXIS_CIP_DRIVE tags: state, faults, start inhibits, bus voltage, loads.

Axis bit meanings: CIP axis faults FLT Sxx are bit xx of CIPAxisFaults, FLT Mxx bit xx of
CIPAxisFaultsRA (Kinetix 5500 manual 2198-UM001, fault code tables). Only the names checked
against that table are given; any other bit is shown by its code, as on the drive display.
"""
import struct


class LogixError(Exception):
    """Controller not reached, or a reply that cannot be used."""


class TagRefused(LogixError):
    """The controller answered, but refused the tag (missing, protected, safety)."""


# ---- controller status (Identity object, status word) ----

FAULT_BITS = {0: "defaut mineur recuperable", 1: "defaut mineur non recuperable",
              2: "DEFAUT MAJEUR recuperable", 3: "DEFAUT MAJEUR non recuperable"}
KEY = {1: "RUN", 2: "PROG", 3: "REM"}
MODE = {0x6: "RUN", 0x7: "PROG"}       # bits 4..7 of the status word (Rockwell KB 28917)


def decode_status(status: bytes) -> dict:
    """Identity status word of a Logix controller: mode, key switch, faults."""
    low, high = status[0], status[1]
    faults = [text for bit, text in FAULT_BITS.items() if high & (1 << bit)]
    return {"mode": MODE.get(low >> 4, f"code {low >> 4}"), "cle": KEY.get(high >> 4 & 3, "?"),
            "defauts": faults, "majeur": bool(high & 0x0C), "mineur": bool(high & 0x03)}


def module_status(status: bytes) -> dict:
    """Identity status word of any CIP module: fault bits and the general state."""
    low, high = status[0], status[1]
    ext = low >> 4
    state = {0: "auto-test / inconnu", 1: "mise a jour firmware", 2: "defaut de connexion E/S",
             3: "pas de connexion E/S", 4: "configuration non valide", 5: "defaut majeur",
             6: "connecte, au moins une connexion E/S en RUN", 7: "connecte, connexions E/S en idle"}.get(ext, "")
    faults = [text for bit, text in FAULT_BITS.items() if high & (1 << bit)]
    return {"etat": state, "defauts": faults, "majeur": bool(high & 0x0C), "mineur": bool(high & 0x03),
            "connexion_en_defaut": ext == 2}


# ---- CIP Motion axes (AXIS_CIP_DRIVE) ----

AXIS_STATE = {0: "Initialisation", 1: "Pre-charge (bus CC en charge)", 2: "Arrete", 3: "Demarrage",
              4: "En marche", 5: "Test", 6: "Arret en cours", 7: "Arret d'urgence", 8: "DEFAUT MAJEUR",
              9: "Demarrage inhibe", 10: "Arret (shutdown)"}
AXIS_STATE_BAD = {7, 8, 9, 10}

# AxisFault: which family the active fault belongs to
AXIS_FAULT = {0: "defaut physique de l'axe (variateur / moteur / retour)", 1: "defaut module (connexion variateur)",
              2: "defaut de configuration", 3: "defaut de groupe d'axes", 4: "defaut de mouvement",
              5: "defaut de garde (STO cable)", 6: "defaut d'initialisation", 7: "defaut APR (position absolue)",
              8: "defaut de securite (STO integre)"}

# CIPAxisFaults bit n = FLT Sn on the Kinetix 5500 display
FLT_S = {
    3: "survitesse moteur (limite usine)", 4: "survitesse moteur (limite utilisateur)",
    7: "surcharge moteur (limite usine)", 8: "surcharge moteur (limite utilisateur)",
    10: "surintensite onduleur", 13: "surcharge onduleur (limite usine)", 14: "surcharge onduleur (limite utilisateur)",
    16: "courant de fuite a la terre", 23: "perte de phase reseau", 25: "echec de pre-charge",
    41: "bruit sur le retour (limite usine)", 42: "bruit sur le retour (limite utilisateur)",
    43: "perte du signal de retour (limite usine)", 44: "perte du signal de retour (limite utilisateur)",
    45: "communication codeur (limite usine)", 54: "erreur de position excessive",
    55: "erreur de vitesse excessive", 56: "limite de surcouple", 57: "limite de sous-couple",
    61: "entree de validation desactivee", 62: "exception demandee par l'automate",
}

AXIS_MEMBERS = ["CIPAxisState", "AxisFault", "CIPAxisFaults", "CIPAxisFaultsRA", "CIPInitializationFaults",
                "CIPInitializationFaultsRA", "CIPStartInhibits", "CIPStartInhibitsRA", "CIPAxisAlarms",
                "CIPAxisAlarmsRA", "ModuleFaults", "GuardFaults", "AxisSafetyFaults", "AxisStatus",
                "DCBusVoltage", "MotorCapacity", "InverterCapacity", "ActualPosition", "ActualVelocity"]


def bits(value) -> list:
    """Numbers of the bits set in an integer (any size), lowest first."""
    if not isinstance(value, int) or isinstance(value, bool):
        return []
    v = value & ((1 << 64) - 1)
    return [i for i in range(64) if v >> i & 1]


def fault_list(m: dict) -> list:
    """Active faults of an axis, as on the drive display: ['FLT S07 surcharge moteur...', ...]."""
    out = []
    for b in bits(m.get("CIPAxisFaults")):
        out.append(f"FLT S{b:02d} " + FLT_S.get(b, "(voir le tableau des codes du manuel 2198-UM001)"))
    for b in bits(m.get("CIPAxisFaultsRA")):
        out.append(f"FLT M{b:02d} (defaut propre au variateur, voir le manuel 2198-UM001)")
    for b in bits(m.get("CIPInitializationFaults")) + bits(m.get("CIPInitializationFaultsRA")):
        out.append(f"INIT FLT bit {b} (defaut d'initialisation : configuration / moteur / firmware)")
    for b in bits(m.get("ModuleFaults")):
        out.append(f"defaut module bit {b} (connexion avec le variateur)")
    for b in bits(m.get("GuardFaults")):
        out.append(f"defaut STO cable (GuardFaults bit {b})")
    for b in bits(m.get("AxisSafetyFaults")):
        out.append(f"defaut de securite integre (AxisSafetyFaults bit {b})")
    if not out:
        out = [AXIS_FAULT[b] for b in bits(m.get("AxisFault")) if b in AXIS_FAULT]
    return out


def inhibit_list(m: dict) -> list:
    out = [f"INHIBIT S{b:02d}" + (" entree de validation (enable) absente" if b == 0 else "")
           for b in bits(m.get("CIPStartInhibits"))]
    out += [f"INHIBIT M{b:02d} (propre au variateur : Safe Torque Off le plus souvent)"
            for b in bits(m.get("CIPStartInhibitsRA"))]
    return out


def alarm_list(m: dict) -> list:
    return ([f"ALARME S{b:02d} " + FLT_S.get(b, "") for b in bits(m.get("CIPAxisAlarms"))]
            + [f"ALARME M{b:02d}" for b in bits(m.get("CIPAxisAlarmsRA"))])


def axis_summary(name: str, m: dict) -> dict:
    """One axis for the page: state, faults, inhibits, alarms, measures, raw values."""
    state = m.get("CIPAxisState")
    faults, inhibits, alarms = fault_list(m), inhibit_list(m), alarm_list(m)
    bad = bool(faults) or state in AXIS_STATE_BAD
    num = lambda k, fmt: (fmt.format(m[k]) if isinstance(m.get(k), (int, float)) else "")
    return {"axe": name,
            "etat": AXIS_STATE.get(state, f"code {state}") if state is not None else "non lu",
            "ok": not bad, "inhibe": state == 9 or bool(inhibits),
            "defauts": faults, "inhibitions": inhibits, "alarmes": alarms,
            "bus": num("DCBusVoltage", "{:.0f} V"), "charge_moteur": num("MotorCapacity", "{:.0f} %"),
            "charge_variateur": num("InverterCapacity", "{:.0f} %"),
            "position": num("ActualPosition", "{:.3f}"), "vitesse": num("ActualVelocity", "{:.2f}"),
            "brut": {k: (f"16#{v & 0xFFFFFFFFFFFFFFFF:X}" if isinstance(v, int) and not isinstance(v, bool) else v)
                     for k, v in m.items() if v is not None},
            "non_lus": [k for k in AXIS_MEMBERS if m.get(k) is None]}


# ---- raw symbolic read (fallback when pycomm3 has no template for a member) ----

ATOMIC = {0xC1: ("BOOL", "<B"), 0xC2: ("SINT", "<b"), 0xC3: ("INT", "<h"), 0xC4: ("DINT", "<i"),
          0xC5: ("LINT", "<q"), 0xC6: ("USINT", "<B"), 0xC7: ("UINT", "<H"), 0xC8: ("UDINT", "<I"),
          0xC9: ("ULINT", "<Q"), 0xCA: ("REAL", "<f"), 0xCB: ("LREAL", "<d"), 0xD1: ("BYTE", "<B"),
          0xD2: ("WORD", "<H"), 0xD3: ("DWORD", "<I"), 0xD4: ("LWORD", "<Q")}


def symbolic_path(name: str) -> bytes:
    """'Axis_1.CIPAxisState' / 'Recipe[3].Temp' -> request path (word count first)."""
    out = b""
    for part in name.split("."):
        base, _, index = part.partition("[")
        raw = base.encode("ascii")
        out += bytes([0x91, len(raw)]) + raw + (b"\0" if len(raw) % 2 else b"")
        if index:
            for i in index.rstrip("]").split(","):
                n = int(i)
                out += bytes([0x28, n]) if n < 256 else (b"\x29\0" + struct.pack("<H", n) if n < 65536
                                                         else b"\x2A\0" + struct.pack("<I", n))
    return bytes([len(out) // 2]) + out


def decode_atomic(data: bytes):
    """Read Tag reply data (type code + value) -> (value, type name)."""
    if len(data) < 2:
        raise LogixError(f"reponse trop courte : {data.hex()}")
    code = struct.unpack("<H", data[:2])[0]
    if code not in ATOMIC:
        raise LogixError(f"type 16#{code:04X} non atomique")
    tname, fmt = ATOMIC[code]
    value = struct.unpack(fmt, data[2:2 + struct.calcsize(fmt)])[0]
    return (bool(value) if tname == "BOOL" else value), tname


class Logix:
    """One ControlLogix / GuardLogix controller, through pycomm3."""

    def __init__(self, ip: str, slot: int = 0, timeout: float = 3.0):
        try:
            from pycomm3 import LogixDriver
        except ImportError:
            raise LogixError("pycomm3 manque : pip install pycomm3") from None
        self.ip, self.slot = ip, slot
        self.raw_tags = None             # set when the full tag list could not be loaded
        self._axes = None
        self.drv = LogixDriver(f"{ip}/{slot}", init_program_tags=True)
        self.drv.socket_timeout = timeout
        try:
            self.drv.open()
        except Exception as first:
            # the tag list upload stops on a structure it cannot read (system types such as AXIS_*):
            # connect without it, keep the bare controller tag names, find the axes by probing
            try:
                self.drv.close()
            except Exception:
                pass
            self.drv = LogixDriver(f"{ip}/{slot}", init_tags=False)
            self.drv.socket_timeout = timeout
            try:
                self.drv.open()
                self.raw_tags = [t for t in self.drv._get_instance_attribute_list_service(None)
                                 if not t["tag_name"].startswith("__") and ":" not in t["tag_name"]]
            except Exception:
                raise LogixError(str(first)) from None
        self.nom = "Allen-Bradley Logix"

    def close(self):
        try:
            self.drv.close()
        except Exception:
            pass

    def info(self) -> dict:
        try:
            i = self.drv.get_plc_info()
        except Exception as e:
            raise LogixError(f"identification non lue : {e}") from None
        rev = i.get("revision") or {}
        return {"produit": i.get("product_name", ""), "revision": f"{rev.get('major', '?')}.{rev.get('minor', '?')}",
                "serie": i.get("serial", ""), "programme": self.drv.info.get("name", "") or i.get("name", ""),
                "statut": bytes(i.get("status") or b"\0\0")}

    def module(self, slot: int):
        """Identity of the module in a chassis slot, or None if the slot does not answer."""
        try:
            i = self.drv.get_module_info(slot)
        except Exception:
            return None
        rev = i.get("revision") or {}
        return {"produit": i.get("product_name", ""), "type": i.get("product_type", ""),
                "revision": f"{rev.get('major', '?')}.{rev.get('minor', '?')}", "serie": i.get("serial", ""),
                "statut": bytes(i.get("status") or b"\0\0")}

    def tag_types(self) -> dict:
        """{tag name: data type name} of the controller and program tags."""
        if self.raw_tags is not None:
            return {t["tag_name"]: "" for t in self.raw_tags}
        out = {}
        for name, t in (self.drv.tags or {}).items():
            out[name] = t.get("data_type_name") or (t.get("data_type") if isinstance(t.get("data_type"), str) else "")
        return out

    def axis_names(self) -> list:
        if self._axes is None:
            if self.raw_tags is None:
                self._axes = sorted(n for n, t in self.tag_types().items() if str(t).startswith("AXIS_CIP_DRIVE"))
            else:                        # structures that answer to .CIPAxisState are axes
                found = []
                for t in self.raw_tags:
                    if t["symbol_type"] & 0x8000 and not t["symbol_type"] & 0x6000:
                        try:
                            self._raw_read(f"{t['tag_name']}.CIPAxisState")
                            found.append(t["tag_name"])
                        except LogixError:
                            pass
                self._axes = sorted(found)
        return self._axes

    def _raw_read(self, name: str):
        from pycomm3.packets import GenericConnectedRequestPacket, SendUnitDataRequestPacket

        class Read(GenericConnectedRequestPacket):
            def _setup_message(self):
                SendUnitDataRequestPacket._setup_message(self)
                self._msg += [self.service, symbolic_path(name), self.request_data]

        from pycomm3.cip_driver import with_forward_open
        drv = self.drv
        if not drv.connected:
            raise LogixError("liaison fermee")
        with_forward_open(lambda _: None)(drv)
        reply = drv.send(Read(drv._sequence, 0x4C, 0, 0, request_data=struct.pack("<H", 1)))
        if not reply:
            raise TagRefused(f"{name} : {reply.error}")
        return decode_atomic(reply.data)

    def read(self, names: list) -> dict:
        """{name: (value, type, error)} - one bad name does not stop the others."""
        out = {}
        if not names:
            return out
        if self.raw_tags is None:
            try:
                tags = self.drv.read(*names)
                for t in (tags if isinstance(tags, list) else [tags]):
                    out[t.tag] = (t.value, t.type, t.error)
            except Exception as e:
                if not self.drv.connected:
                    raise LogixError(f"lecture : {e}") from None
        for name in names:
            out.setdefault(name, (None, None, "non lu"))
        for name, (value, typ, err) in list(out.items()):
            if err:
                try:
                    value, typ = self._raw_read(name)
                    out[name] = (value, typ, None)
                except TagRefused as e:
                    out[name] = (None, None, str(e).partition(" : ")[2] or err)
                except LogixError:
                    if not self.drv.connected:
                        raise
        return out

    def read_axis(self, axis: str) -> dict:
        """{member: value or None} for AXIS_MEMBERS."""
        got = self.read([f"{axis}.{m}" for m in AXIS_MEMBERS])
        return {m: (got.get(f"{axis}.{m}", (None, None, "absent"))[0]
                    if not got.get(f"{axis}.{m}", (None, None, "absent"))[2] else None) for m in AXIS_MEMBERS}

    def write(self, name: str, value):
        try:
            t = self.drv.write((name, value))
        except Exception as e:
            raise LogixError(f"ecriture : {e}") from None
        if t.error:
            raise TagRefused(f"{name} : {t.error}")
