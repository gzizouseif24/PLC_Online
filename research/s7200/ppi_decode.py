#!/usr/bin/env python3
"""Decode a capture of research/s7200/ppi_proxy.py: PPI frames, then the S7 PDUs inside.

    python research/s7200/ppi_decode.py s7200_capture.log [--tout]

Prints one line per S7 PDU (direction, function, parameters, data) and the notes typed during the
capture. Without --tout, read / write of memory (the cyclic status of Micro/WIN) are summed up."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.protocols.ppi import SC, SD1, SD2, fcs, parse_pdu  # noqa: E402

FUNCTIONS = {0x04: "lecture", 0x05: "ecriture", 0xF0: "negociation PDU", 0x1A: "debut chargement (download)",
             0x1B: "chargement bloc", 0x1C: "fin chargement", 0x1D: "debut televersement (upload)",
             0x1E: "televersement", 0x1F: "fin televersement", 0x28: "service PI (RUN, _INSE, ...)",
             0x29: "STOP"}
LINE = re.compile(r"^\[(\S+)\s+([\d.]+) ms\] (PC->PLC|PLC->PC|#####) (?:\[\s*\d+\] )?(.*)$")


def frames(stream: bytes):
    """Split one direction's bytes into PPI frames: ('SD2', body) / ('SD1', body) / ('E5', b'')."""
    i = 0
    while i < len(stream):
        b = stream[i]
        if b == SC:
            yield "E5", b""
            i += 1
        elif b == SD1 and i + 5 < len(stream) + 1:
            yield "SD1", stream[i + 1:i + 4]
            i += 6
        elif b == SD2 and i + 3 < len(stream) and stream[i + 3] == SD2:
            n = stream[i + 1]
            body = stream[i + 4:i + 4 + n]
            ok = i + 4 + n + 1 < len(stream) + 1 and fcs(body) == stream[i + 4 + n]
            yield ("SD2" if ok else "SD2?"), body
            i += 6 + n
        else:
            yield "?", bytes([b])
            i += 1


def describe(body: bytes) -> str:
    try:
        p = parse_pdu(body[3:])
    except Exception as e:
        return f"PDU illisible ({e}) {body.hex(' ')}"
    f = p["param"][0] if p["param"] else None
    name = "userdata" if p["type"] == 7 else FUNCTIONS.get(f, f"fonction {f:02X}" if f is not None else "?")
    err = f" ERREUR {p['erreur']:04X}" if p["erreur"] else ""
    text = p["param"].decode("latin-1")
    readable = "".join(c if 32 <= ord(c) < 127 else "." for c in text)
    return (f"type {p['type']} {name}{err} | param {p['param'].hex(' ')} ({readable}) | "
            f"data[{len(p['data'])}] {p['data'][:48].hex(' ')}{' ...' if len(p['data']) > 48 else ''}")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    everything = "--tout" in sys.argv
    pending = {"PC->PLC": b"", "PLC->PC": b""}
    out, skipped = [], 0
    for line in Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(line)
        if not m:
            continue
        stamp, ms, direction, rest = m.groups()
        if direction == "#####":
            out.append(f"{stamp}  ##### {rest}")
            continue
        pending[direction] += bytes.fromhex(rest.replace(" ", ""))
        # a frame ends with 16 (SD1 / SD2) or is a lone E5: decode what is complete
        data = pending[direction]
        cut = max(data.rfind(b"\x16") + 1, len(data) if data.endswith(b"\xe5") else 0)
        if not cut:
            continue
        pending[direction] = data[cut:]
        for kind, body in frames(data[:cut]):
            if kind.startswith("SD2"):
                text = describe(body)
                if not everything and (" lecture " in text or " ecriture " in text) and "ERREUR" not in text:
                    skipped += 1
                    continue
                out.append(f"{stamp} {ms:>11} ms  {direction}  {kind}  {text}")
            elif everything:
                out.append(f"{stamp} {ms:>11} ms  {direction}  {kind}  {body.hex(' ')}")
    print("\n".join(out))
    if skipped:
        print(f"\n({skipped} lectures / ecritures memoire masquees : --tout pour tout voir)")


if __name__ == "__main__":
    main()
