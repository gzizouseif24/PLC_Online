"""S7-200 program decoding: OB1 as the CPU uploads it -> POUs, networks, STL.

Layout found on the bench (16/11, CPU 224, Micro/WIN V4, research/s7200/):
- OB1 = 36-byte header, then the compiled code (its size at bytes 34-35), then 4 bytes, then a zlib stream.
- The zlib stream (Micro/WIN's table of contents) starts with the byte size of every item of the code, as
  16-bit little-endian words, in order, until the code size is reached. Then one record per POU:
    main:       0A <len> 00 <items> 00 00 00 00 00 <name len> <name>
    subroutine: 0B <len> <number 2 bytes> <items> 00 x 8 <name len> <name> 00 00
    interrupt:  0C <len> <number 2 bytes> <items> 00 x 8 <name len> <name> 00 00
  <items> counts the items of the POU: main = prologue + networks + MEND; SBR / INT = label + networks + RET(I).
- Code items:
    prologue    FE 24 01 4A 82 64 09 (+ compiler networks)    SBR label  D6 <n 2 bytes>   INT label  CD <n 2 bytes>
    MEND ED     RET E9    RETI E5
- Bit instructions are 2 bytes: high nibble = operation, then 12 bits: area (3) | bit (3) | byte (6).
  Areas: 0 I, 1 Q, 2 M, 4 SM, 5 T (bit = timer number % 8, byte = number // 8). Timers / counters use their
  number as a bit index (T63 = 0B C7: byte 7, bit 7).

What is CONFIRMED comes from Micro/WIN's own STL of the same program (SBR10 'Allgemein'). What is a GUESS
is marked with '?' in the output and listed in GUESSED; every unknown byte is shown in hex, never invented.
"""
import struct
import zlib

BIT_OPS = {0x0: "LD", 0x1: "LDN", 0x2: "A", 0x3: "AN", 0x4: "O", 0x5: "ON", 0x6: "="}
GUESSED_OPS = {0x4, 0x5}                     # O / ON: by position, not yet seen in Micro/WIN's STL
AREAS = {0: "I", 1: "Q", 2: "M", 4: "SM", 5: "T", 6: "C"}
GUESSED_AREAS = {6}

ONE_BYTE = {0xDD: ("OLD", True), 0xDC: ("ALD", False), 0xE1: ("EU", False), 0xED: ("MEND", True),
            0xE9: ("RET", True), 0xE5: ("RETI", True)}
PROLOGUE = bytes.fromhex("fe24014a826409")
GUESSED = ("O / ON (4x / 5x), ALD (DC), EU (E1) et la zone C : deduits de la place dans le code, "
           "a confirmer avec un export STL de Micro/WIN")


class CodeError(ValueError):
    pass


def bit_operand(word12: int):
    area, bit, byte = word12 >> 9 & 7, word12 >> 6 & 7, word12 & 0x3F
    name = AREAS.get(area)
    if name is None:
        return None, True
    if name in ("T", "C"):
        return f"{name}{byte * 8 + bit}", area in GUESSED_AREAS
    return f"{name}{byte}.{bit}", area in GUESSED_AREAS


def ext_bit(area_bit: int, byte: int):
    """Bit operand of the FE instructions (S / R): 20 | bit << 1 = M (bench: 22 0E = M14.1)."""
    if area_bit & 0xF0 == 0x20 and not area_bit & 1:
        return f"M{byte}.{area_bit >> 1 & 7}", False
    return None, True


def decode_item(code: bytes) -> list:
    """One network (or label / end item) -> [(stl, raw bytes, sure)]. Unknown bytes end the decoding of the
    item and are returned as one '??' line."""
    out, i = [], 0
    while i < len(code):
        b = code[i]
        if code[i:i + 7] == PROLOGUE:
            out.append(("// debut du programme (prologue)", code[i:i + 7], True))
            i += 7
            continue
        if b in (0xD5, 0xD6, 0xCD) and i + 3 <= len(code):
            n = code[i + 1] << 8 | code[i + 2]
            text = {0xD5: f"CALL   SBR{n}", 0xD6: f"// SBR{n}", 0xCD: f"// INT{n}"}[b]
            out.append((text, code[i:i + 3], True))
            i += 3
            continue
        if b in ONE_BYTE:
            text, sure = ONE_BYTE[b]
            out.append((text, code[i:i + 1], sure))
            i += 1
            continue
        if b == 0xA6 and code[i + 1:i + 2] == b"\x44" and i + 6 <= len(code):       # TON Tn, PT
            t, pt = code[i + 2] << 8 | code[i + 3], struct.unpack(">h", code[i + 4:i + 6])[0]
            out.append((f"TON    T{t}, {pt:+d}", code[i:i + 6], True))
            i += 6
            continue
        if b == 0xFE and i + 7 <= len(code) and code[i + 1] in (0x1F, 0x21) and code[i + 2] == 0x01 \
                and code[i + 5] == 0x40:                                          # S / R bit, N
            name, unsure = ext_bit(code[i + 3], code[i + 4])
            if name:
                op = "S" if code[i + 1] == 0x1F else "R"
                out.append((f"{op:<6} {name}, {code[i + 6]}", code[i:i + 7], not unsure))
                i += 7
                continue
        op = b >> 4
        if op in BIT_OPS and i + 2 <= len(code):
            name, unsure = bit_operand((b & 0x0F) << 8 | code[i + 1])
            if name:
                out.append((f"{BIT_OPS[op]:<6} {name}", code[i:i + 2], not unsure and op not in GUESSED_OPS))
                i += 2
                continue
        out.append(("??", code[i:], False))
        break
    return out


def split(ob1: bytes, source: bytes) -> list:
    """POUs of OB1: [{'type': 'MAIN' / 'SBR' / 'INT', 'numero', 'nom', 'items': [bytes, ...]}]."""
    if len(ob1) < 36:
        raise CodeError("OB1 trop court")
    size = ob1[34] << 8 | ob1[35]
    code = ob1[36:36 + size]
    sizes, pos = [], 0
    while sum(sizes) < size:
        if pos + 2 > len(source):
            raise CodeError("table des reseaux incomplete")
        sizes.append(struct.unpack_from("<H", source, pos)[0])
        pos += 2
    if sum(sizes) != size:
        raise CodeError(f"tailles des reseaux ({sum(sizes)}) differentes du code ({size})")
    items, at = [], 0
    for s in sizes:
        items.append(code[at:at + s])
        at += s
    pous = []
    while pos < len(source) and source[pos] in (0x0A, 0x0B, 0x0C):
        kind, length = source[pos], source[pos + 1]
        rec = source[pos:pos + length]
        if kind == 0x0A:
            number, count, tail = 0, rec[3], 0
        else:
            number, count, tail = rec[2] << 8 | rec[3], rec[4], 2
        # the name is the last length-prefixed text of the record (SBR: 8 zero bytes before it, INT: 5)
        name_at = next((k for k in range(5, len(rec)) if rec[k] and k + 1 + rec[k] + tail == len(rec)), len(rec))
        name = rec[name_at + 1:name_at + 1 + rec[name_at]].decode("latin-1") if name_at < len(rec) else "?"
        pous.append({"type": {0x0A: "MAIN", 0x0B: "SBR", 0x0C: "INT"}[kind], "numero": number, "nom": name,
                     "items": items[:count]})
        items = items[count:]
        pos += length
    if items:
        pous.append({"type": "?", "numero": 0, "nom": "(reste)", "items": items})
    return pous


def source_of(ob1: bytes) -> bytes:
    start = ob1.find(b"\x78\x9c", 36)
    if start < 0:
        raise CodeError("pas de table des reseaux (flux zlib) dans OB1")
    return zlib.decompressobj().decompress(ob1[start:])


def to_stl(ob1: bytes) -> dict:
    """Whole program as STL text, Micro/WIN export layout. Returns {'texte', 'sur', 'doute', 'inconnu'}."""
    pous = split(ob1, source_of(ob1))
    lines, counts = [], {"sur": 0, "doute": 0, "inconnu": 0}
    for p in pous:
        head = {"MAIN": "ORGANIZATION_BLOCK", "SBR": "SUBROUTINE_BLOCK", "INT": "INTERRUPT_BLOCK"}.get(p["type"], "BLOCK")
        label = {"MAIN": "OB1", "SBR": f"SBR{p['numero']}", "INT": f"INT{p['numero']}"}.get(p["type"], "?")
        lines.append(f"{head} {p['nom']}:{label}")
        lines.append("BEGIN")
        network = 0
        for k, item in enumerate(p["items"]):
            decoded = decode_item(item)
            if p["type"] == "MAIN" and k == 0:              # prologue, with the CPU type test Micro/WIN adds
                if len(decoded) > 1:
                    lines.append("// prologue ajoute par Micro/WIN : " + " | ".join(
                        t if t != "??" else "?? " + r.hex(" ") for t, r, _ in decoded[1:]))
                continue
            texts = [t for t, _, _ in decoded]
            is_frame = all(t.startswith("//") or t in ("MEND", "RET", "RETI") for t in texts)
            if not is_frame:
                network += 1
                lines.append(f"Network {network}")
            for text, raw, sure in decoded:
                if text.startswith("//") or (is_frame and text in ("MEND", "RET", "RETI")):
                    continue
                if text == "??":
                    counts["inconnu"] += 1
                    lines.append(f"// ?? {raw.hex(' ')}")
                elif sure:
                    counts["sur"] += 1
                    lines.append(text)
                else:
                    counts["doute"] += 1
                    lines.append(f"{text:<24}// ?")
        lines.append({"MAIN": "END_ORGANIZATION_BLOCK", "SBR": "END_SUBROUTINE_BLOCK",
                      "INT": "END_INTERRUPT_BLOCK"}.get(p["type"], "END_BLOCK"))
        lines.append("")
    if counts["doute"]:
        lines.append(f"// ? = {GUESSED}")
    return {"texte": "\n".join(lines), **counts}
