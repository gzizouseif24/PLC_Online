"""Minimal DHCP / BootP server to give an address to one device (WAGO coupler in BootP/DHCP mode).

Listens on UDP 67 on the PC's addresses, answers DISCOVER with OFFER, REQUEST with ACK, a plain
BootP request with a BOOTREPLY, only for the MAC asked. Replies are broadcast to port 68.
"""
import ipaddress
import socket
import struct
import time

MAGIC = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, DECLINE, ACK = 1, 2, 3, 4, 5
TYPES = {1: "DHCP DISCOVER", 3: "DHCP REQUEST", None: "BootP"}


def parse(packet: bytes):
    """-> {'mac', 'xid', 'type' (1, 3 or None for BootP), 'flags'} or None."""
    if len(packet) < 240 or packet[0] != 1:
        return None
    xid, = struct.unpack_from(">I", packet, 4)
    flags, = struct.unpack_from(">H", packet, 10)
    mac = "-".join(f"{b:02X}" for b in packet[28:34])
    kind = None
    if packet[236:240] == MAGIC:
        i = 240
        while i < len(packet) and packet[i] != 255:
            if packet[i] == 0:
                i += 1
                continue
            code, n = packet[i], packet[i + 1]
            if code == 53 and n:
                kind = packet[i + 2]
            i += 2 + n
    return {"mac": mac, "xid": xid, "type": kind, "flags": flags, "raw": packet}


def reply(request: dict, ip: str, server: str, prefix: int = 24, lease: int = 86400) -> bytes:
    raw = request["raw"]
    msg = None if request["type"] is None else (OFFER if request["type"] == DISCOVER else ACK)
    head = bytearray(236)
    head[0:4] = bytes([2, 1, 6, 0])
    head[4:8] = raw[4:8]                          # xid
    head[10:12] = raw[10:12]                      # flags
    head[16:20] = socket.inet_aton(ip)            # yiaddr
    head[20:24] = socket.inet_aton(server)        # siaddr
    head[28:44] = raw[28:44]                      # chaddr
    mask = socket.inet_aton(str(ipaddress.ip_network(f"0.0.0.0/{prefix}").netmask))
    opts = b""
    if msg:
        opts += bytes([53, 1, msg]) + bytes([54, 4]) + socket.inet_aton(server) + bytes([51, 4]) + struct.pack(">I", lease)
    opts += bytes([1, 4]) + mask + b"\xff"
    return bytes(head) + MAGIC + opts


def _sockets(addresses):
    socks, errors = [], []
    for a in addresses:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind((a, 67))
            s.settimeout(0.2)
            socks.append((a, s))
        except OSError as e:
            errors.append(f"{a}:67 {e}")
            s.close()
    return socks, errors


def listen(addresses, seconds: float = 12.0) -> dict:
    """Requests heard: {'demandes': [{'mac', 'type', 'carte'}], 'erreurs': [...]}."""
    socks, errors = _sockets(addresses)
    seen = {}
    end = time.time() + seconds
    try:
        while socks and time.time() < end:
            for a, s in socks:
                try:
                    data, _ = s.recvfrom(1500)
                except (socket.timeout, OSError):
                    continue
                p = parse(data)
                if p:
                    seen[p["mac"]] = {"mac": p["mac"], "type": TYPES.get(p["type"], f"DHCP {p['type']}"), "carte": a}
    finally:
        for _, s in socks:
            s.close()
    return {"demandes": list(seen.values()), "erreurs": errors}


def serve(mac: str, ip: str, server: str, prefix: int = 24, seconds: float = 60.0) -> dict:
    """Answer the device with that MAC until it has its address (ACK sent or BootP reply)."""
    socks, errors = _sockets([server])
    if not socks:
        raise OSError("port 67 non disponible : " + "; ".join(errors))
    _, s = socks[0]
    end = time.time() + seconds
    sent = []
    try:
        while time.time() < end:
            try:
                data, _ = s.recvfrom(1500)
            except (socket.timeout, OSError):
                continue
            p = parse(data)
            if not p or p["mac"] != mac.upper():
                continue
            s.sendto(reply(p, ip, server, prefix), ("255.255.255.255", 68))
            sent.append(TYPES.get(p["type"], str(p["type"])))
            if p["type"] in (None, REQUEST):
                return {"ok": True, "echanges": sent}
    finally:
        s.close()
    return {"ok": False, "echanges": sent}
