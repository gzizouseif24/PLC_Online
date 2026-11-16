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
AREAS = {0: "I", 1: "Q", 2: "M", 4: "SM", 5: "T", 6: "C", 8: "V"}
SEEN_AREAS = {"I", "Q", "M", "SM", "T", "V"}            # seen in Micro/WIN exports; others are deduced

ONE_BYTE = {0xDD: "OLD", 0xDC: "ALD", 0xE1: "EU", 0xED: "MEND", 0xE9: "RET", 0xE5: "RETI"}
PROLOGUE = bytes.fromhex("fe24014a826409")
# word instructions: opcode -> (name, size of the operands: 1 B, 2 W, 4 D, number of operands)
MOVES = {0xD8: ("MOVB", 1), 0xD9: ("MOVW", 2), 0xDA: ("MOVD", 4)}
DEDUCED = {0xD8}            # same layout as MOVW / MOVD, not yet seen in a Micro/WIN export
COMPARES = {0x96: "LDW<=", 0x98: "LDW>="}
INCS = {0xB1: ("INCW", 2), 0xB2: ("INCD", 4)}
GUESSED = "MOVB (D8) et zone C : deduits par analogie, a confirmer avec un export STL de Micro/WIN"


class CodeError(ValueError):
    pass


def bit_operand(word12: int):
    """2-byte bit instructions: 12 bits = area (3) | bit (3) | byte (6)."""
    area, bit, byte = word12 >> 9 & 7, word12 >> 6 & 7, word12 & 0x3F
    name = AREAS.get(area)
    if name is None:
        return None, False
    if name in ("T", "C"):
        return f"{name}{byte * 8 + bit}", name in SEEN_AREAS
    return f"{name}{byte}.{bit}", name in SEEN_AREAS


def long_bit(code: bytes, i: int):
    """Long bit operand (80-86 instructions, S / R): <area nibble | bit*2> <byte>, or for a byte above 255
    <F0 | bit*2> <area byte> <byte 2 bytes> (bench: 84 0D = V13.2, F8 80 03 84 = V900.4)."""
    if i + 2 > len(code) or code[i] & 1:
        return None, 0, False
    first, bit = code[i] >> 4, code[i] >> 1 & 7
    if first == 0xF:
        if i + 4 > len(code):
            return None, 0, False
        area, byte, size = code[i + 1] >> 4, code[i + 2] << 8 | code[i + 3], 4
    else:
        area, byte, size = first, code[i + 1], 2
    name = AREAS.get(area)
    if name is None or name in ("T", "C"):
        return None, 0, False
    return f"{name}{byte}.{bit}", size, name in SEEN_AREAS


def word_operand(code: bytes, i: int, width: int):
    """Byte / word / double operand: <area nibble | address 12 bits> (bench: 83 F2 = VD1010)."""
    if i + 2 > len(code):
        return None, False
    area, addr = code[i] >> 4, (code[i] & 0x0F) << 8 | code[i + 1]
    name = AREAS.get(area)
    if name is None or name in ("T", "C"):
        return None, False
    return f"{name}{'BWxD'[width - 1] if width != 3 else '?'}{addr}", name in SEEN_AREAS


def constant(code: bytes, i: int, width: int):
    if i + width > len(code):
        return None
    return f"{int.from_bytes(code[i:i + width], 'big', signed=True):+d}"


def operands(code, i, flags, widths):
    """Operands after a flags byte: bit 6 = first operand is a constant, bit 2 = second one."""
    out, sure = [], True
    for k, width in enumerate(widths):
        is_const = flags & (0x40 if k == 0 else 0x04)
        if is_const:
            text = constant(code, i, width)
            step = width
        else:
            text, ok = word_operand(code, i, width)
            sure &= ok
            step = 2
        if text is None:
            return None, i, False
        out.append(text)
        i += step
    return out, i, sure


def decode_item(code: bytes) -> list:
    """One network (or label / end item) -> [(stl, raw bytes, sure)]. Unknown bytes end the decoding of the
    item and are returned as one '??' line."""
    out, i = [], 0
    while i < len(code):
        b, start = code[i], i
        text, sure = None, True
        if code[i:i + 7] == PROLOGUE:
            text, i = "// debut du programme (prologue)", i + 7
        elif b in (0xD5, 0xD6, 0xCD) and i + 3 <= len(code):
            n = code[i + 1] << 8 | code[i + 2]
            text, i = {0xD5: f"CALL   SBR{n}", 0xD6: f"// SBR{n}", 0xCD: f"// INT{n}"}[b], i + 3
        elif b in ONE_BYTE:
            text, i = ONE_BYTE[b], i + 1
        elif b == 0xA6 and i + 2 <= len(code) and code[i + 1] & 0x40:          # TON Tn, PT (constant or word)
            ops, j, sure = operands(code, i + 2, code[i + 1], (2, 2))
            if ops:
                text, i = f"TON    T{int(ops[0])}, {ops[1]}", j
        elif b in MOVES and i + 2 <= len(code):
            name, width = MOVES[b]
            ops, j, sure = operands(code, i + 2, code[i + 1], (width, width))
            sure &= b not in DEDUCED
            if ops:
                text, i = f"{name:<6} {ops[0]}, {ops[1]}", j
        elif b in COMPARES and i + 2 <= len(code):
            ops, j, sure = operands(code, i + 2, code[i + 1], (2, 2))
            if ops:
                text, i = f"{COMPARES[b]:<6} {ops[0]}, {ops[1]}", j
        elif b in INCS and code[i + 1:i + 2] == b"\x0c":
            name, width = INCS[b]
            op, sure = word_operand(code, i + 2, width)
            if op:
                text, i = f"{name:<6} {op}", i + 4
        elif b == 0xFE and i + 3 <= len(code) and code[i + 1] in (0x1F, 0x21) and code[i + 2] == 0x01:
            name, size, sure = long_bit(code, i + 3)                         # S / R bit, N
            j = i + 3 + size
            if name and code[j:j + 1] == b"\x40" and j + 2 <= len(code):
                text, i = f"{'S' if code[i + 1] == 0x1F else 'R':<6} {name}, {code[j + 1]}", j + 2
        elif b >> 4 == 0x8 and b & 0x0F in BIT_OPS:
            name, size, sure = long_bit(code, i + 1)                         # LD / A / … on V (long form)
            if name:
                text, i = f"{BIT_OPS[b & 0x0F]:<6} {name}", i + 1 + size
        elif b >> 4 in BIT_OPS and i + 2 <= len(code):
            name, sure = bit_operand((b & 0x0F) << 8 | code[i + 1])
            if name:
                text, i = f"{BIT_OPS[b >> 4]:<6} {name}", i + 2
        if text is None:
            out.append(("??", code[start:], False))
            break
        out.append((text, code[start:i], sure))
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
            if p["type"] == "MAIN" and k == 0 and item.startswith(PROLOGUE):
                decoded = decoded[1:]                    # the first item = prologue + Network 1
                if not decoded:
                    network += 1
                    lines.append(f"Network {network}")
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
