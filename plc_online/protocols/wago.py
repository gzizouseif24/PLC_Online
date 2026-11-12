"""WAGO 750 fieldbus couplers over Modbus TCP (750-352, 750-342, 750-362, 750-341...).

Coupler registers (WAGO 750 Modbus map, same on the ETHERNET couplers; to confirm on the 750-352):
  0x0000..      process image, inputs: complex modules (analog...) first, in rack order, one
                or more words each; digital inputs as bits (function 02 from bit 0)
  0x0200..      process image, outputs read back (words, function 03); coils read back at 0x0200
  0x1020        I/O LED error code (blink code), 0x1021 its argument (module position...)
  0x1022/1023   analog (word) outputs / inputs in the process image, in bits
  0x1024/1025   digital outputs / inputs, in bits
  0x2000..2008  constants 0000 FFFF 1234 AAAA 5555 7FFF 8000 3FFF 4000 (WAGO check: 0x2002 = 0x1234)
  0x2010        firmware index, 0x2011 series (750), 0x2012 item number (352),
  0x2013/2014   firmware major / minor
  0x2030        module list (up to 65 words): coupler first, then per module its item number
                (461), or for a digital module bit 15 set, bits 8..14 = number of bits,
                bit 0 = input, bit 1 = output

750-461 (manual 1.1.2, Pt100): one word per channel, 0.1 deg C per digit, two's complement;
0x2134 (850.0) with error LED = overrange or broken wire, 0x8001 = short circuit / below -200.
"""
import concurrent.futures
import ipaddress
import socket

from plc_online.protocols.modbus import Modicon, ModbusError, PlcRefused, TcpTransport, signed16  # noqa: F401

CONSTANT_CHECK = (0x2002, 0x1234)
LED_ERROR = 0x1020
IMAGE_SIZES = 0x1022
FIRMWARE = 0x2010
MODULES = 0x2030
OUTPUT_OFFSET = 0x0200

# LED error codes of the I/O LED (WAGO 750 coupler manuals, "LED signalling")
LED_ERRORS = {
    1: "erreur materielle ou de configuration du coupleur",
    2: "erreur de configuration du bus interne (modules)",
    3: "erreur de protocole du bus interne",
    4: "erreur physique du bus interne : module defectueux ou module de fin absent",
    5: "erreur d'initialisation du bus interne",
    6: "erreur de configuration du bus de terrain (adresse IP...)",
    7: "module non supporte",
    8: "module non supporte / diagnostic",
    9: "erreur de programme (coupleur programmable)",
}

# error code 6 "fieldbus specific errors": arguments (750-352 manual 1.4.0, table 48)
LED6_ARGS = {
    1: "MAC ID invalide (remplacer le coupleur)",
    2: "erreur d'initialisation du materiel Ethernet",
    3: "erreur d'initialisation TCP/IP",
    4: "pas d'adresse IP (configuration reseau / serveur BootP)",
    5: "erreur d'initialisation du protocole applicatif",
    6: "image process trop grande (trop de modules)",
    7: "adresse IP en double sur le reseau",
    8: "erreur de construction de l'image process",
    9: "erreur de correspondance modules / bus de terrain : verifier le fichier EA-config.xml du coupleur",
}


def led_text(code, arg) -> str:
    if code == 6 and arg in LED6_ARGS:
        return f"erreur bus de terrain : {LED6_ARGS[arg]}"
    return LED_ERRORS.get(code, "code inconnu")


# words per channel group in the input / output image, by item number (no status byte)
ANALOG_IN = {
    "452": 2, "453": 4, "454": 2, "455": 4, "456": 2, "457": 4, "459": 4, "460": 4, "461": 2, "462": 2,
    "463": 4, "464": 2, "465": 2, "466": 2, "467": 2, "468": 4, "469": 2, "470": 2, "471": 2, "472": 2,
    "473": 2, "474": 2, "475": 2, "476": 2, "477": 2, "478": 2, "479": 2, "480": 2, "481": 2, "483": 2,
    "485": 2, "487": 2, "491": 2, "492": 2,
}
ANALOG_OUT = {"550": 2, "552": 2, "554": 2, "555": 4, "556": 2, "557": 4, "559": 4, "560": 2,
              "562": 2, "563": 2, "585": 2, "597": 2}

MODULE_NAMES = {
    "461": "2 entrees Pt100 / RTD", "460": "4 entrees Pt100 / RTD", "469": "2 entrees thermocouple",
    "467": "2 entrees 0-10 V", "468": "4 entrees 0-10 V", "466": "2 entrees 4-20 mA",
    "472": "2 entrees 0-20 mA", "453": "4 entrees 0-20 mA", "455": "4 entrees 4-20 mA",
    "550": "2 sorties 0-10 V", "552": "2 sorties 0-20 mA", "554": "2 sorties 4-20 mA",
    "559": "4 sorties 0-10 V", "600": "module de fin", "602": "alimentation 24 V",
}


# passive modules (supply, end, distribution...): no process data
PASSIVE = {"600", "601", "602", "609", "610", "611", "612", "613", "614", "615", "616", "621", "622",
           "624", "625", "626", "627", "628"}


def describe(word: int) -> dict:
    """One word of the module list -> {'nom', 'type', 'entrees_bits', 'sorties_bits', 'mots_e', 'mots_s'}."""
    if word & 0x8000:
        bits = (word >> 8) & 0x7F
        ins, outs = bool(word & 1), bool(word & 2)
        kind = "entrees TOR" if ins and not outs else "sorties TOR" if outs and not ins else "TOR"
        return {"nom": f"module {bits} {kind}", "code": f"{word:04X}", "type": kind,
                "entrees_bits": bits if ins else 0, "sorties_bits": bits if outs else 0, "mots_e": 0, "mots_s": 0,
                "digital": True}
    item = str(word)
    if item in PASSIVE:
        return {"nom": f"750-{item}", "code": item, "type": MODULE_NAMES.get(item, "module passif"),
                "entrees_bits": 0, "sorties_bits": 0, "mots_e": 0, "mots_s": 0, "digital": False}
    return {"nom": f"750-{item}", "code": item, "type": MODULE_NAMES.get(item, "module"),
            "entrees_bits": 0, "sorties_bits": 0, "mots_e": ANALOG_IN.get(item), "mots_s": ANALOG_OUT.get(item, 0), "digital": False}


def layout(modules: list) -> list:
    """Adds 'iw' (first input word), 'qw' (first output word), 'ix' / 'qx' (first digital bit) per
    module, in rack order: words for the complex modules first, then the digital bits.
    A module of unknown size stops the word count ('iw' None from there)."""
    iw = qw = ix = qx = 0
    known = True
    out = []
    for m in modules:
        m = dict(m)
        if m["digital"]:
            m["ix"] = ix if m["entrees_bits"] else None
            m["qx"] = qx if m["sorties_bits"] else None
            ix += m["entrees_bits"]
            qx += m["sorties_bits"]
        else:
            if m["mots_e"] is None and m["code"] not in ANALOG_OUT:
                known = False
            m["iw"] = iw if known and m["mots_e"] else None
            m["qw"] = qw if known and m["mots_s"] else None
            iw += m["mots_e"] or 0
            qw += m["mots_s"] or 0
        out.append(m)
    return out


def pt100(raw: int) -> tuple:
    """750-461 word -> (text, ok)."""
    if raw == 0x2134:
        return "850.0 °C : rupture de fil ou hors plage haute (LED rouge)", False
    if raw == 0x8001:
        return "court-circuit ou sous -200 °C (LED rouge)", False
    return f"{signed16(raw) / 10:.1f} °C", True


class Wago:
    def __init__(self, host: str, unit: int = 1, timeout: float = 2.0, transport=None):
        self.host = host
        self.mb = Modicon(transport or TcpTransport(host, 502, timeout), unit, retries=1)

    def regs(self, start, count):
        return self.mb.read_holding(start, count)

    def try_regs(self, start, count):
        """Words or None if the coupler refuses: function 03, then 04 (some firmwares give the
        0x1000 / 0x2000 registers only one way)."""
        for read in (self.mb.read_holding, self.mb.read_input_registers):
            try:
                return read(start, count)
            except PlcRefused:
                continue
        return None

    def is_wago(self) -> bool:
        try:
            got = self.try_regs(CONSTANT_CHECK[0], 1)
        except ModbusError:
            return False
        return bool(got) and got[0] == CONSTANT_CHECK[1]

    def identity(self) -> dict:
        """Each word read on its own: a refused one shows as '?'."""
        words = [(self.try_regs(FIRMWARE + i, 1) or [None])[0] for i in range(5)]
        index, series, item, major, minor = words
        if item is None:                         # 750-352: 0x2010.. refused, the coupler is first in 0x2030
            item = (self.try_regs(MODULES, 1) or [None])[0]
        fw = f"{major:02d}.{minor:02d}" if major is not None and minor is not None else "non lu"
        return {"modele": f"{series or 750}-{item if item is not None else '?'}", "serie": series, "article": item,
                "firmware": fw + (f" (index {index})" if index is not None else "")}

    def led_error(self) -> tuple:
        """(code, argument), (None, None) if not readable."""
        got = self.try_regs(LED_ERROR, 2)
        if got is None:
            got = [(self.try_regs(LED_ERROR, 1) or [None])[0], (self.try_regs(LED_ERROR + 1, 1) or [0])[0]]
        return got[0], got[1]

    def image_sizes(self) -> dict:
        got = self.try_regs(IMAGE_SIZES, 4) or [(self.try_regs(IMAGE_SIZES + i, 1) or [None])[0] for i in range(4)]
        ao, ai, do, di = got
        return {"analog_out_bits": ao, "analog_in_bits": ai, "digital_out_bits": do, "digital_in_bits": di}

    def modules(self) -> list:
        """[coupler item, then one description per module], from 0x2030 (65 words, or word by word
        until 0 / refused if the coupler does not give the whole block)."""
        words = self.try_regs(MODULES, 65)
        if words is None:
            words = []
            for i in range(65):
                w = self.try_regs(MODULES + i, 1)
                if not w:
                    break
                words.append(w[0])
                if w[0] == 0 and i:
                    break
        if not words:
            raise ModbusError("liste des modules (0x2030) refusee par le coupleur")
        coupler, rest = words[0], words[1:]
        mods = []
        for w in rest:
            if w == 0:
                break
            mods.append(describe(w))
        return [coupler] + layout(mods)

    def input_words(self, start, count):
        return self.mb.read_input_registers(start, count)

    def output_words(self, start, count):
        return self.mb.read_holding(OUTPUT_OFFSET + start, count)

    def input_bits(self, start, count):
        return self.mb.read_discrete_inputs(start, count)

    def output_bits(self, start, count):
        return self.mb.read_coils(OUTPUT_OFFSET + start, count)

    def write_output_word(self, n, value):
        self.mb.write_register(n, value)

    def write_output_bit(self, n, on):
        self.mb.write_coil(n, on)

    def close(self):
        self.mb.close()


# ---- search on the network ----

def local_networks() -> list:
    """IPv4 /24 networks of the PC (all adapters), link-local and loopback left out."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    nets = []
    for ip in sorted(ips):
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        net = ipaddress.ip_network(ip + "/24", strict=False)
        if net not in [n for n, _ in nets]:
            nets.append((net, ip))
    return nets


def _probe(ip: str, timeout: float) -> dict:
    try:
        sock = socket.create_connection((ip, 502), timeout=timeout)
    except OSError:
        return None
    out = {"ip": ip, "modele": "Modbus TCP (pas WAGO ?)", "wago": False}
    try:
        sock.settimeout(1.0)
        w = Wago(ip, transport=TcpTransport(ip, sock=sock))
        if w.is_wago():
            ident = w.identity()
            out.update(modele=f"WAGO {ident['modele']}", wago=True, firmware=ident["firmware"])
    except (ModbusError, OSError):
        pass
    finally:
        sock.close()
    return out


def scan(targets, timeout: float = 0.4) -> list:
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for r in pool.map(lambda ip: _probe(ip, timeout), targets):
            if r:
                found.append(r)
    return found


def search(extra_ips=(), timeout: float = 0.4) -> dict:
    """Modbus TCP devices (port 502) on the PC's networks; WAGO ones identified."""
    nets = local_networks()
    targets = [str(h) for net, own in nets for h in net.hosts() if str(h) != own]
    targets += [ip for ip in extra_ips if ip not in targets]
    found = scan(targets, timeout)
    return {"reseaux": [f"{net} (PC {own})" for net, own in nets], "trouves": found}
