"""S7-300 block download ("load into CPU") over the STEP 7 S7ONLINE driver.

libnodave has no download: the CPU drives it (after our "request download" it asks the PC for
the data, then announces the end), so the PC must answer requests. We talk to S7onlinx.dll
directly (SCP_send / SCP_receive with the 80-byte exchange header libnodave uses, see
nodave.c _daveSendMessageS7online), on the connection libnodave opened.

Sequence and parameter layouts from Snap7 (s7_micro_client.cpp opDownload / opDelete):
  PC  -> CPU  request download  1A ... "_0" type number "P" "1" load-length MC7-length
  CPU -> PC   download block    1B  (PC answers with a data slice, 01 = more, 00 = last)
  CPU -> PC   download ended    1C  (PC answers)
  PC  -> CPU  PI service 28 "_INSE" (insert the block), or "_DELE" (delete a block)
Snap7 sets the footer checksum to 0 before the download: the CPU computes it.
"""
import ctypes
import struct

REQ_DOWNLOAD, DOWNLOAD, DOWNLOAD_ENDED, CONTROL = 0x1A, 0x1B, 0x1C, 0x28
INSERT, DELETE = (0x50, b"_INSE"), (0x42, b"_DELE")
HEADER = 80                              # S7ONLINE exchange block header


class DownloadError(Exception):
    """The CPU refused or the sequence went wrong."""


# ---- PDUs ----

def request_pdu(ref: int, params: bytes, data: bytes = b"") -> bytes:
    return struct.pack(">BBHHHH", 0x32, 0x01, 0, ref, len(params), len(data)) + params + data


def ack_data_pdu(ref: int, params: bytes, data: bytes = b"") -> bytes:
    return struct.pack(">BBHHHHH", 0x32, 0x03, 0, ref, len(params), len(data), 0) + params + data


def parse_pdu(pdu: bytes) -> dict:
    """{'type', 'ref', 'params', 'data', 'error'} of an S7 PDU (job 1, ack 2, ack data 3)."""
    if len(pdu) < 10 or pdu[0] != 0x32:
        raise DownloadError(f"reponse non S7 : {pdu[:24].hex(' ')}")
    kind, ref, plen, dlen = pdu[1], *struct.unpack_from(">HHH", pdu, 4)
    head = 12 if kind in (2, 3) else 10
    error = struct.unpack_from(">H", pdu, 10)[0] if head == 12 and len(pdu) >= 12 else 0
    return {"type": kind, "ref": ref, "params": pdu[head:head + plen],
            "data": pdu[head + plen:head + plen + dlen], "error": error}


def block_name(type_char: int, number: int) -> bytes:
    """'_0' + type character + 5-digit number + 'P' (0x38 = OB, 0x41 = DB, 0x43 = FC, 0x45 = FB)."""
    return b"_0" + bytes([type_char]) + f"{number:05d}".encode() + b"P"


def request_download_params(type_char: int, number: int, block: bytes) -> bytes:
    load_len = int.from_bytes(block[8:12], "big")
    mc7_len = int.from_bytes(block[34:36], "big")
    return (bytes([REQ_DOWNLOAD, 0, 1, 0, 0, 0, 0, 0, 0x09]) + block_name(type_char, number)
            + bytes([0x0D]) + b"1" + f"{load_len:06d}".encode() + f"{mc7_len:06d}".encode())


def control_params(type_char: int, number: int, service) -> bytes:
    sfun, cmd = service
    return (bytes([CONTROL, 0, 0, 0, 0, 0, 0, 0xFD]) + struct.pack(">H", 0x0A)
            + bytes([1, 0, 0x30, type_char]) + f"{number:05d}".encode() + bytes([sfun, len(cmd)]) + cmd)


def prepare_block(block: bytes) -> bytes:
    """Block as declared by its header length, footer checksum set to 0 (as Snap7 does)."""
    total = int.from_bytes(block[8:12], "big")
    if block[:2] != b"\x70\x70" or not 72 <= total <= len(block):
        raise DownloadError("bloc non reconnu (en-tete 70 70 / longueur)")
    data = bytearray(block[:total])
    data[total - 10:total - 8] = b"\x00\x00"
    return bytes(data)


# ---- sequences, on any channel with send(pdu) and receive() -> pdu ----

class Sequence:
    def __init__(self, channel, log=None):
        self.ch, self.log, self.ref = channel, log or (lambda m: None), 0x0100

    def next_ref(self):
        self.ref = (self.ref + 1) & 0xFFFF
        return self.ref

    def exchange(self, pdu: bytes) -> dict:
        self.ch.send(pdu)
        answer = parse_pdu(self.ch.receive())
        self.log(f"   <- type {answer['type']} params {answer['params'].hex(' ')} erreur {answer['error']:04X}")
        return answer

    def download(self, type_char: int, number: int, block: bytes, max_pdu: int = 240):
        block = prepare_block(block)
        answer = self.exchange(request_pdu(self.next_ref(), request_download_params(type_char, number, block)))
        if answer["error"] or answer["params"][:1] != bytes([REQ_DOWNLOAD]):
            raise DownloadError(f"demande de chargement refusee (erreur {answer['error']:04X})")
        slice_max = max_pdu - 12 - 2 - 4
        offset = 0
        while True:
            job = parse_pdu(self.ch.receive())
            fun = job["params"][:1]
            if fun == bytes([DOWNLOAD_ENDED]):
                self.ch.send(ack_data_pdu(job["ref"], bytes([DOWNLOAD_ENDED])))
                break
            if job["type"] != 1 or fun != bytes([DOWNLOAD]):
                raise DownloadError(f"demande inattendue de la CPU : {job['params'].hex(' ')}")
            part = block[offset:offset + slice_max]
            offset += len(part)
            more = 1 if offset < len(block) else 0
            self.ch.send(ack_data_pdu(job["ref"], bytes([DOWNLOAD, more]),
                                      struct.pack(">HH", len(part), 0x00FB) + part))
        if offset < len(block):
            raise DownloadError(f"la CPU a arrete apres {offset} octets sur {len(block)}")
        self.control(type_char, number, INSERT)

    def control(self, type_char: int, number: int, service):
        answer = self.exchange(request_pdu(self.next_ref(), control_params(type_char, number, service)))
        if answer["error"] or answer["params"][:1] != bytes([CONTROL]):
            raise DownloadError(f"{service[1].decode()} refuse (erreur {answer['error']:04X})")


# ---- S7ONLINE transport ----

class S7OnlineChannel:
    """Raw PDUs on the S7ONLINE handle opened by libnodave (same layout as nodave.c)."""

    def __init__(self, handle: int, dll=None, log=None):
        self.h, self.log = handle, log or (lambda m: None)
        self.dll = dll or ctypes.WinDLL("S7onlinx.dll")
        self.dll.SCP_send.argtypes = [ctypes.c_int, ctypes.c_ushort, ctypes.c_char_p]
        self.dll.SCP_receive.argtypes = [ctypes.c_int, ctypes.c_ushort, ctypes.POINTER(ctypes.c_int),
                                         ctypes.c_ushort, ctypes.c_char_p]

    @staticmethod
    def header(field7: int, length: int) -> bytearray:
        h = bytearray(HEADER)
        h[4] = HEADER                                    # headerlength
        h[7] = 2                                         # allways2
        h[12], h[13] = 64, field7                        # field6, field7 (6 send, 7 receive)
        struct.pack_into("<H", h, 14, 16642)             # field8
        struct.pack_into("<H", h, 16, length if field7 == 6 else 0)   # validDataLength
        struct.pack_into("<H", h, 19, length)            # payloadLength
        struct.pack_into("<H", h, 21, HEADER)            # payloadStart
        h[35] = 1                                        # field10
        return h

    def _receive_block(self) -> bytes:
        buf = ctypes.create_string_buffer(HEADER + 520)
        n = ctypes.c_int(0)
        r = self.dll.SCP_receive(self.h, 0xFFFF, ctypes.byref(n), len(buf), buf)
        if r != 0:
            raise DownloadError(f"S7ONLINE : reception impossible ({r})")
        return buf.raw

    def send(self, pdu: bytes):
        self.log(f"   -> {pdu[:40].hex(' ')}{' ...' if len(pdu) > 40 else ''}")
        block = bytes(self.header(6, len(pdu))) + pdu
        if self.dll.SCP_send(self.h, len(block), block) != 0:
            raise DownloadError("S7ONLINE : envoi impossible")
        self._receive_block()                            # send confirmation, as libnodave does

    def receive(self) -> bytes:
        req = bytes(self.header(7, 480)) + bytes(480)
        if self.dll.SCP_send(self.h, len(req), req) != 0:
            raise DownloadError("S7ONLINE : demande de reception impossible")
        raw = self._receive_block()
        length = struct.unpack_from("<H", raw, 19)[0]
        pdu = raw[HEADER:HEADER + length]
        self.log(f"   <- {pdu[:40].hex(' ')}{' ...' if len(pdu) > 40 else ''}")
        return pdu
