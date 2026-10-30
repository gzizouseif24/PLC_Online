"""Extract the text of a Windows Help (.hlp) file: one block per topic (title + text).

Written from the format description in helpdeco's helpfile.txt (M. Winterhoff).
Text only: formatting, pictures and hotspots are skipped.

    python3 -I hlp2txt.py file.hlp > file.txt
"""
import struct
import sys


def lz77(data: bytes, limit=None) -> bytes:
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        bits = data[i]
        i += 1
        for b in range(8):
            if i >= n or (limit is not None and len(out) >= limit):
                break
            if bits >> b & 1:
                if i + 1 >= n:
                    i = n
                    break
                w = data[i] | data[i + 1] << 8
                i += 2
                pos, length = w & 0x0FFF, (w >> 12) + 3
                start = len(out) - pos - 1
                for k in range(length):
                    out.append(out[start + k] if start + k >= 0 else 0x20)
            else:
                out.append(data[i])
                i += 1
    return bytes(out if limit is None else out[:limit])


class Hlp:
    def __init__(self, path):
        self.d = open(path, "rb").read()
        magic, dir_start = struct.unpack_from("<II", self.d, 0)
        if magic != 0x00035F3F:
            raise ValueError("not a WinHelp file")
        self.files = dict(self._btree_leaves(dir_start + 9, entry="dir"))

    # internal file system
    def _btree_leaves(self, at, entry):
        (magic, flags, page_size) = struct.unpack_from("<HHH", self.d, at)
        root, = struct.unpack_from("<h", self.d, at + 26)
        nlevels, = struct.unpack_from("<h", self.d, at + 32)
        pages = at + 38
        page = root
        for _ in range(nlevels - 1):            # descend via PreviousPage
            page, = struct.unpack_from("<h", self.d, pages + page * page_size + 4)
        while page != -1:
            p = pages + page * page_size
            _, count, _, nxt = struct.unpack_from("<Hhhh", self.d, p)
            q = p + 8
            for _ in range(count):
                end = self.d.index(b"\0", q)
                name = self.d[q:end].decode("latin-1")
                off, = struct.unpack_from("<I", self.d, end + 1)
                yield name, off
                q = end + 5
            page = nxt

    def file(self, name) -> bytes:
        off = self.files[name]
        _, used, _ = struct.unpack_from("<IIB", self.d, off)
        return self.d[off + 9:off + 9 + used]

    # phrases
    def phrases(self):
        if "|Phrases" in self.files:
            f = self.file("|Phrases")
            num, hundred = struct.unpack_from("<HH", f, 0)
            if self.minor <= 16:
                offs = struct.unpack_from(f"<{num + 1}H", f, 4)
                text = f[4:]
                base = 0
            else:
                size, = struct.unpack_from("<I", f, 4)
                offs = struct.unpack_from(f"<{num + 1}H", f, 8)
                text = lz77(f[8 + 2 * (num + 1):], size)
                base = offs[0]
                if self.minor <= 16:
                    base = 0
            return [text[offs[k] - base:offs[k + 1] - base] for k in range(num)], "old"
        if "|PhrIndex" in self.files:
            idx = self.file("|PhrIndex")
            _, nent, _, img_size, img_csize, _ = struct.unpack_from("<6I", idx, 0)
            bitcount = struct.unpack_from("<H", idx, 24)[0] & 0x0F
            longs = idx[28:]
            state = {"ptr": -1, "mask": 0}

            def getbit():
                if state["mask"] == 0 or state["mask"] == 1 << 31:
                    state["ptr"] += 1
                    state["mask"] = 1
                else:
                    state["mask"] <<= 1
                v, = struct.unpack_from("<I", longs, 4 * state["ptr"])
                return bool(v & state["mask"])
            offs = [0]
            for _ in range(nent):
                n = 1
                while getbit():
                    n += 1 << bitcount
                if getbit():
                    n += 1
                for b in range(1, 5):
                    if bitcount > b and getbit():
                        n += 1 << b
                offs.append(offs[-1] + n)
            img = self.file("|PhrImage")
            if img_csize != img_size:
                img = lz77(img, img_size)
            return [img[offs[k]:offs[k + 1]] for k in range(nent)], "hall"
        return [], None

    def text_of(self, data2: bytes, compressed: bool) -> bytes:
        if not compressed or not self.phr:
            return data2
        out = bytearray()
        i = 0
        if self.kind == "old":
            while i < len(data2):
                c = data2[i]
                i += 1
                if c == 0 or c > 15:
                    out.append(c)
                else:
                    num = (c * 256 - 256 + data2[i]) if i < len(data2) else 0
                    i += 1
                    k = num // 2
                    if k < len(self.phr):
                        out += self.phr[k]
                    if num & 1:
                        out.append(0x20)
            return bytes(out)
        while i < len(data2):           # Hall
            c = data2[i]
            i += 1
            if c & 1 == 0:
                k = c // 2
                if k < len(self.phr):
                    out += self.phr[k]
            elif c & 3 == 1:
                k = 128 + (c // 4) * 256 + (data2[i] if i < len(data2) else 0)
                i += 1
                if k < len(self.phr):
                    out += self.phr[k]
            elif c & 7 == 3:
                n = c // 8 + 1
                out += data2[i:i + n]
                i += n
            elif c & 15 == 7:
                out += b" " * (c // 16 + 1)
            else:
                out += b"\0" * (c // 16 + 1)
        return bytes(out)

    # topics
    def topics(self):
        sysf = self.file("|SYSTEM")
        _, self.minor, _, _, flags = struct.unpack_from("<HHHIH", sysf, 0)
        compressed = self.minor > 16 and flags in (4, 8)
        block_size = 2048 if (self.minor <= 16 or flags == 8) else 4096
        dsize = 16384 if compressed else block_size - 12
        self.phr, self.kind = self.phrases()
        topic = self.file("|TOPIC")
        buf = bytearray()
        for b in range(0, len(topic), block_size):
            raw = topic[b + 12:b + block_size]
            dec = lz77(raw, dsize) if compressed else raw
            buf += dec.ljust(dsize, b"\0")
        first, = struct.unpack_from("<i", topic, 4)

        def at(pos):
            return (pos - 12) // dsize * dsize + (pos - 12) % dsize

        pos = first
        seen = set()
        title, lines = None, []
        while pos >= 12 and pos not in seen:
            seen.add(pos)
            o = at(pos)
            if o + 21 > len(buf):
                break
            bsize, len2, prev, nxt, len1, rtype = struct.unpack_from("<iiiiiB", buf, o)
            if bsize <= 0:
                break
            data2 = bytes(buf[o + len1:o + bsize])
            text = self.text_of(data2, len2 > bsize - len1)
            if rtype == 2:
                if title is not None or lines:
                    yield title, lines
                title = text.split(b"\0")[0].decode("cp1252", "replace")
                lines = []
            elif rtype in (1, 0x20, 0x23):
                s = text.replace(b"\0", b"\n").decode("cp1252", "replace")
                lines.append(s)
            if nxt <= 0 or nxt == pos:
                break
            pos = nxt
        if title is not None or lines:
            yield title, lines


def main():
    h = Hlp(sys.argv[1])
    for title, lines in h.topics():
        print(f"\n===== {title}")
        body = "".join(lines)
        body = "\n".join(l.rstrip() for l in body.splitlines() if l.strip())
        print(body)


if __name__ == "__main__":
    main()
