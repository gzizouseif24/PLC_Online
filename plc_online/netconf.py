"""PC network settings (Windows): adapters, ARP table, extra IPv4 addresses on an adapter.

Changing an address needs admin rights: if the server does not run as admin, the netsh commands
run in an elevated PowerShell (one UAC prompt per change). Output goes to a temp file read back.
"""
import base64
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time

WAGO_OUI = "00-30-DE"


class NetError(Exception):
    pass


def windows() -> bool:
    return sys.platform.startswith("win")


def _run(args, timeout=30) -> str:
    flags = 0x08000000 if windows() else 0        # CREATE_NO_WINDOW
    r = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=flags)
    out = r.stdout.decode("cp850" if windows() else "utf-8", "replace")
    return out


def _ps(command: str, timeout=30) -> str:
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], timeout)


def _json_list(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    return data if isinstance(data, list) else [data]


def is_admin() -> bool:
    if not windows():
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


# ---- adapters ----

def parse_adapters(adapters_json: str, addresses_json: str, dhcp_json: str = "") -> list:
    """Get-NetAdapter + Get-NetIPAddress (+ Get-NetIPInterface) JSON -> adapters with their IPv4."""
    addrs = {}
    for a in _json_list(addresses_json):
        addrs.setdefault(a.get("InterfaceAlias"), []).append(
            {"ip": a.get("IPAddress"), "prefixe": a.get("PrefixLength"),
             "origine": {1: "Other", 2: "Manual", 3: "WellKnown", 4: "Dhcp", 5: "RouterAdvertisement"}.get(
                 a.get("PrefixOrigin"), str(a.get("PrefixOrigin")))})
    dhcp = {d.get("InterfaceAlias"): d.get("Dhcp") in (1, "Enabled") for d in _json_list(dhcp_json)}
    out = []
    for a in _json_list(adapters_json):
        name = a.get("Name")
        status = a.get("Status")
        status = {1: "Up", 2: "Down", 7: "Not Present"}.get(status, status)
        out.append({"nom": name, "description": a.get("InterfaceDescription") or "",
                    "mac": a.get("MacAddress") or "", "etat": "connecte" if status == "Up" else "debranche",
                    "adresses": addrs.get(name, []), "dhcp": dhcp.get(name, True)})
    return out


def adapters() -> list:
    """Wired / wireless adapters of the PC with their IPv4 addresses."""
    if not windows():
        return []
    a = _ps("Get-NetAdapter -Physical | Select-Object Name,InterfaceDescription,MacAddress,Status | ConvertTo-Json")
    b = _ps("Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias,IPAddress,PrefixLength,PrefixOrigin"
            " | ConvertTo-Json")
    c = _ps("Get-NetIPInterface -AddressFamily IPv4 | Select-Object InterfaceAlias,Dhcp | ConvertTo-Json")
    try:
        return parse_adapters(a, b, c)
    except ValueError as e:
        raise NetError(f"cartes reseau illisibles : {e}") from None


def adapter(name: str) -> dict:
    for a in adapters():
        if a["nom"] == name:
            return a
    raise NetError(f"carte reseau {name} introuvable")


# ---- ARP ----

ARP_LINE = re.compile(r"^\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})\s+(\w+)")


def parse_arp(text: str) -> list:
    out = []
    for line in text.splitlines():
        m = ARP_LINE.match(line)
        if m:
            mac = m.group(2).upper().replace(":", "-")
            out.append({"ip": m.group(1), "mac": mac, "wago": mac.startswith(WAGO_OUI)})
    return out


def arp_table() -> list:
    try:
        return parse_arp(_run(["arp", "-a"], 10))
    except (OSError, subprocess.SubprocessError):
        return []


# ---- changing addresses ----

def _q(text: str) -> str:
    """PowerShell single-quoted string."""
    return "'" + str(text).replace("'", "''") + "'"


def _elevated(lines: list) -> str:
    """Run PowerShell lines as admin (UAC prompt if needed); returns their output."""
    if not windows():
        raise NetError("changement d'adresse IP : seulement sous Windows")
    log = os.path.join(tempfile.gettempdir(), f"plc_online_net_{os.getpid()}.txt")
    script = "& {\n" + "\n".join(lines) + "\n} *> " + _q(log)
    try:
        os.remove(log)
    except OSError:
        pass
    if is_admin():
        _ps(script, timeout=60)
    else:
        inner = base64.b64encode(script.encode("utf-16-le")).decode()
        _ps("Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
            f"-ArgumentList '-NoProfile','-EncodedCommand','{inner}'", timeout=120)
    try:
        with open(log, encoding="utf-16", errors="replace") as f:
            text = f.read()
    except (OSError, UnicodeError):
        try:
            with open(log, encoding="cp850", errors="replace") as f:
                text = f.read()
        except OSError:
            raise NetError("changement refuse (fenetre administrateur annulee ?)") from None
    try:
        os.remove(log)
    except OSError:
        pass
    return text


def _mask(prefix: int) -> str:
    return str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask)


def add_addresses(name: str, ips: list, prefix: int = 24) -> dict:
    """Put the PC in the coupler's network: the adapter gets fixed addresses ips (an adapter in
    automatic mode / DHCP is switched to fixed first). Returns what was there before, for undo."""
    before = adapter(name)
    lines = []
    if before["dhcp"]:
        lines.append(f"Set-NetIPInterface -InterfaceAlias {_q(name)} -AddressFamily IPv4 -Dhcp Disabled")
    for ip in ips:
        lines.append(f"New-NetIPAddress -InterfaceAlias {_q(name)} -IPAddress {_q(ip)} -PrefixLength {int(prefix)}"
                     " | Out-Null")
    out = _elevated(lines)
    time.sleep(4)                                       # duplicate address detection
    after = adapter(name)
    have = {a["ip"] for a in after["adresses"]}
    missing = [ip for ip in ips if ip not in have]
    if missing:
        raise NetError(f"adresse(s) {', '.join(missing)} non ajoutee(s) : {out.strip()[-300:] or 'refuse'}")
    return {"avant_dhcp": before["dhcp"], "avant": before["adresses"], "ajoutees": ips}


def remove_addresses(name: str, ips: list) -> None:
    _elevated([f"Remove-NetIPAddress -InterfaceAlias {_q(name)} -IPAddress {_q(ip)} -Confirm:$false"
               for ip in ips])


def set_dhcp(name: str) -> None:
    """Back to automatic address (as most PCs are): fixed addresses removed, DHCP on."""
    _elevated([f"Get-NetIPAddress -InterfaceAlias {_q(name)} -AddressFamily IPv4 -PrefixOrigin Manual "
               "-ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false",
               f"Set-NetIPInterface -InterfaceAlias {_q(name)} -AddressFamily IPv4 -Dhcp Enabled"])
