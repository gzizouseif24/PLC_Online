"""S7-300 forces, through the "programmer commands" (TIS) user data functions STEP 7 uses.

Formats from the Wireshark S7 dissector (epan/dissectors/packet-s7comm.c), not from Siemens:
to check on the bench. A force is a job kept by the CPU: "read job list" (subfunction 0x10)
gives the jobs (function, sequence number), "read job" (0x11) gives one job's request again,
which for a force job (function 0x09) is: item count, addresses (6 bytes each), values.
"""
import re
import struct

TIS_GROUP = 0x01
SUB_FORCE, SUB_DELETE_JOB, SUB_READ_JOB_LIST, SUB_READ_JOB = 0x09, 0x0F, 0x10, 0x11
JOB_NAMES = {0x01: "etat de bloc", 0x02: "etat de variables", 0x07: "selection forcage", 0x08: "modifier variable",
             0x09: "FORCAGE", 0x0A: "point d'arret", 0x13: "etat de bloc v2"}

# TIS item areas: high nibble = memory, low nibble = 0 bit, 1 byte, 2 word, 3 dword
_MEMORY = {0x0: "M", 0x1: "E", 0x2: "A", 0x3: "PE", 0x7: "DB"}
_SIZE = {0x1: "B", 0x2: "W", 0x3: "D"}


class ForceFormatError(ValueError):
    """Answer that does not match the expected layout."""


def tis_request(params: bytes = b"", data: bytes = b"") -> bytes:
    """User data part of a TIS request: parameter size, data size, parameters, data."""
    return struct.pack(">HH", len(params), len(data)) + params + data


def split_tis_answer(udata: bytes):
    """(parameters, data) of a TIS answer."""
    if len(udata) < 4:
        raise ForceFormatError(f"reponse TIS trop courte : {udata.hex(' ')}")
    tp, td = struct.unpack_from(">HH", udata)
    return udata[4:4 + tp], udata[4 + tp:4 + tp + td]


def parse_job_list(data: bytes) -> list:
    """[(function, sequence number)] from a 'read job list' answer (4 bytes per job)."""
    return [(data[i], data[i + 1]) for i in range(0, len(data) - 3, 4)]


def item_address(b: bytes) -> str:
    """6-byte TIS item address -> 'A4.0', 'MW10', 'DB5.DBD2', 'T3'..."""
    area, rep, db, start = b[0], b[1], int.from_bytes(b[2:4], "big"), int.from_bytes(b[4:6], "big")
    if area == 0x54:
        return f"T{start}"
    if area == 0x64:
        return f"Z{start}"
    mem, size = _MEMORY.get(area >> 4), area & 0x0F
    if mem is None:
        return f"zone {area:02X} {start}"
    if size == 0:
        return f"DB{db}.DBX{start}.{rep}" if mem == "DB" else f"{mem}{start}.{rep}"
    letter = _SIZE.get(size, "?")
    name = f"DB{db}.DB{letter}{start}" if mem == "DB" else f"{mem}{letter}{start}"
    return name + (f" (x{rep})" if rep > 1 else "")


def parse_force_job(job_udata: bytes) -> list:
    """[(address, value bytes)] from a 'read job' answer of a force job."""
    _, data = split_tis_answer(job_udata)
    if len(data) < 4:
        raise ForceFormatError(f"tache de forcage trop courte : {data.hex(' ')}")
    jtp, jtd = struct.unpack_from(">HH", data)
    job = data[4 + jtp:4 + jtp + jtd]
    if len(job) < 2:
        raise ForceFormatError(f"tache de forcage sans donnees : {data.hex(' ')}")
    count = int.from_bytes(job[0:2], "big")
    addrs = [job[2 + 6 * i:8 + 6 * i] for i in range(count)]
    off, out = 2 + 6 * count, []
    for a in addrs:
        if off + 4 > len(job):
            raise ForceFormatError(f"valeurs de forcage incompletes : {job.hex(' ')}")
        tsize, length = job[off + 1], int.from_bytes(job[off + 2:off + 4], "big")
        if tsize in (4, 5):                  # length in bits
            length //= 8
        value = job[off + 4:off + 4 + length]
        off += 4 + length + (length % 2)
        out.append((item_address(a), value))
    return out


def value_text(address: str, value: bytes) -> str:
    """'1' for a bit, '16#002A (42)' for a byte / word / dword."""
    if not value:
        return "?"
    if re.search(r"\d\.\d$", address):
        return str(value[0] & 1)
    n = int.from_bytes(value, "big")
    signed = int.from_bytes(value, "big", signed=True) if len(value) > 1 else n
    return f"16#{n:0{2 * len(value)}X} ({signed})"
