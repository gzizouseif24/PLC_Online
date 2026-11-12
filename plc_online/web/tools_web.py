"""Shared tools of the web interface for the serial PLCs:
  - Changements: read a range of one zone, again and again, to log what changes;
  - Test E/S: the inputs of each unit, for a wiring check (each input seen at 1 then back at 0)."""

MAX_BLOCK = 256

# zones per family: (zone, words?) ; X / Y of Delta and Mitsubishi are octal
BLOCK_ZONES = {
    "panasonic": [("DT", True), ("WR", True), ("WX", True), ("WY", True)],
    "delta": [("D", True), ("T", True), ("C", True), ("M", False), ("X", False), ("Y", False), ("S", False)],
    "mitsubishi": [("D", True), ("T", True), ("C", True), ("M", False), ("X", False), ("Y", False), ("S", False)],
    "omron": [("D", True), ("CIO", True), ("W", True), ("H", True), ("A", True), ("T", True), ("C", True)],
    "tsx": [("MW", True), ("MD", True), ("KW", True), ("KD", True), ("SW", True), ("M", False), ("S", False)],
}


def _drv():
    from plc_online.web import drivers
    return drivers


def signed(v: int, bits: int = 16) -> int:
    return v - (1 << bits) if v >> (bits - 1) & 1 else v


def read_block(family: str, plc, zone: str, start: int, count: int) -> dict:
    zones = dict(BLOCK_ZONES.get(family, []))
    zone = str(zone or "").upper().lstrip("%")
    if zone not in zones:
        raise _drv().DriverError(f"Zone {zone} : {', '.join(zones) or 'aucune pour cet automate'}")
    if not (0 <= start and 1 <= count <= MAX_BLOCK):
        raise _drv().DriverError(f"Debut >= 0, nombre 1..{MAX_BLOCK}")
    if family == "panasonic":
        values = (plc.read_dt(start, count) if zone == "DT"
                  else plc.read_contact_words(zone[1], start, count))
        names = [f"{zone}{start + i}" for i in range(count)]
        values = [signed(v) if zone == "DT" else v for v in values]
    elif family == "delta":
        from plc_online.protocols import delta as dl
        try:
            values = (plc.read_words(zone, start, count) if zones[zone] else plc.read_bits(zone, start, count))
        except dl.DeltaError as e:
            raise _drv().DriverError(str(e)) from None
        names = [dl.name(zone, start + i) for i in range(count)]
        values = [signed(v) if zones[zone] else int(v) for v in values]
    elif family == "omron":
        from plc_online.protocols import hostlink as hl
        try:
            values = [hl.signed16(v) for v in plc.read(zone, start, count)]
        except ValueError as e:
            raise _drv().DriverError(str(e)) from None
        names = [f"{'CIO ' if zone == 'CIO' else zone}{start + i}" for i in range(count)]
    elif family == "mitsubishi":
        from plc_online.protocols import melsec_fx as fx
        try:
            fx.parse_address(fx.name(zone, start))
            fx.parse_address(fx.name(zone, start + count - 1))
        except fx.FxError as e:
            raise _drv().DriverError(str(e)) from None
        if zones[zone]:
            size = fx.data_address(zone, start)[1]
            values = [signed(v, 8 * size) for v in plc.read_words(zone, start, count)]
        else:
            values = [int(v) for v in plc.read_bits(zone, start, count)]
        names = [fx.name(zone, start + i) for i in range(count)]
    else:                                  # tsx
        from plc_online.protocols.unitelway import WORD_ZONES
        values = plc.read(zone, start, count)
        if zone in WORD_ZONES:
            values = [signed(v, 8 * WORD_ZONES[zone][2]) for v in values]
        names = [f"%{zone}{start + i}" for i in range(count)]
    return {"adresses": names, "valeurs": [int(v) for v in values]}


# ---- inputs per unit ----

def inputs(family: str, plc) -> dict:
    if family == "panasonic":
        return _panasonic_inputs(plc)
    if family == "mitsubishi":
        return _fx_inputs(plc)
    if family == "delta":
        return _delta_inputs(plc)
    if family == "omron":
        return _omron_inputs(plc)
    raise _drv().DriverError("Test des entrees : Panasonic, Mitsubishi, Delta et Omron")


def _omron_inputs(plc) -> dict:
    from plc_online.protocols import hostlink as hl
    units, notes = [], []
    for c in hl.rack_layout(plc.io_table(40)):
        if c["cio"] is None:
            notes.append(f"Rack {c['rack']} emplacement {c['slot']} : {c['model']}, pas testee.")
            continue
        if "entree" not in c["kind"]:
            continue
        words = plc.read("CIO", c["cio"], c["words"])
        units.append({"nom": f"Rack {c['rack']} emplacement {c['slot']} ({c['model']})",
                      "entrees": [{"adresse": f"{c['cio'] + k}.{b:02d}", "valeur": words[k] >> b & 1}
                                  for k in range(c["words"]) for b in range(16)]})
    if not units:
        notes.append("Aucune carte d'entrees connue dans la table E/S.")
    return {"unites": units, "notes": notes, "cablage": ""}


def _panasonic_inputs(plc) -> dict:
    from plc_online.web import panasonic_web as pw
    cpu_inputs = getattr(plc, "cpu_inputs", None) or 16
    units = [("CPU", 0, cpu_inputs, "")]
    notes = []
    if not getattr(plc, "cpu_inputs", None):
        notes.append("Modele de CPU non renseigne : X0..XF affichees (C10 : X0..X5, C14 / C16 : X0..X7).")
    for pos, model in enumerate(getattr(plc, "expansions", [None, None, None]), 1):
        if model == "":
            continue
        kind = pw.EXPANSIONS.get(model) if model else None
        if model is None:
            notes.append(f"Extension {pos} : type non renseigne, pas testee.")
        elif kind != "mots" and kind[0]:
            units.append((f"Extension {pos} (FP0-{model})", 2 * pos, kind[0], model))
    out = []
    for label, word, count, _ in units:
        w = plc.read_contact_words("X", word, 1 + (count - 1) // 16)
        out.append({"nom": label, "entrees": [
            {"adresse": pw.bit_name("X", (word + i // 16, i % 16)), "valeur": (w[i // 16] >> (i % 16)) & 1}
            for i in range(count)]})
    return {"unites": out, "notes": notes,
            "cablage": "Entrees 24 V DC, polarite libre sur COM (8 entrees par commun, les deux bornes COM sont "
                       "reliees). Connecteur E16P : X0 X1 / X2 X3 / X4 X5 / X6 X7 / COM COM (docs/fp0r.md)."}


def _fx_inputs(plc) -> dict:
    from plc_online.protocols import melsec_fx as fx
    from plc_online.web import melsec_web as mw
    units = [(label, first, count) for label, _, (first, count), _ in mw.layout(plc.modele, plc.expansions) if count]
    top = max(first + count for _, first, count in units)
    bits = plc.read_bits("X", 0, top)
    return {"unites": [{"nom": label, "entrees": [{"adresse": fx.name("X", first + i), "valeur": int(bits[first + i])}
                                                  for i in range(count)]} for label, first, count in units],
            "notes": [],
            "cablage": "Entrees sur le commun S/S (manuel materiel FX1N 5.1.2). NPN ou contact vers 0V : S/S relie au "
                       "24V. PNP ou contact vers +24V : S/S relie au 0V."}


def _delta_inputs(plc) -> dict:
    from plc_online.protocols import delta as dl
    from plc_online.web import delta_web as dw
    n_in = dw.DEFAULT_IO[0]
    try:
        ext = plc.read_words("D", 1142, 1)[0]
    except Exception:
        ext = 0
    units = [("Entrees integrees", 0, n_in)] + ([("Entrees d'extension", n_in, ext)] if 0 < ext <= 256 - n_in else [])
    bits = plc.read_bits("X", 0, n_in + (ext if len(units) > 1 else 0))
    return {"unites": [{"nom": label, "entrees": [{"adresse": dl.name("X", first + i), "valeur": int(bits[first + i])}
                                                  for i in range(count)]} for label, first, count in units],
            "notes": [], "cablage": ""}


# ---- read-only program copies (Panasonic, Mitsubishi): save, compare, details, export, import ----

def ranges_text(indexes, label="pas") -> list:
    """[3, 4, 5, 9] -> ['pas 3..5', 'pas 9']."""
    out, start, prev = [], None, None
    for k in sorted(set(indexes)):
        if start is not None and k == prev + 1:
            prev = k
            continue
        if start is not None:
            out.append(f"{label} {start}" if start == prev else f"{label} {start}..{prev}")
        start = prev = k
    if start is not None:
        out.append(f"{label} {start}" if start == prev else f"{label} {start}..{prev}")
    return out


class ProgramCopies:
    """A folder of program images (one file each + info.json). read(plc, progress) -> bytes,
    describe(blob) -> summary, diff(now, saved) -> list of differing places."""

    def __init__(self, session, folder_fn, file_name, read, describe, diff, prefix, import_ok, refuse_restore):
        self.s, self.folder_fn, self.file = session, folder_fn, file_name
        self.read, self.describe, self.diff = read, describe, diff
        self.prefix, self.import_ok, self.refuse_restore = prefix, import_ok, refuse_restore

    @property
    def programs(self):
        return self.folder_fn()

    def _save(self, blob, automate, note, cpu=""):
        import hashlib
        import time
        from plc_online.web import tsx_web
        target = _drv()._new_folder(self.programs, self.prefix)
        target.mkdir(parents=True)
        (target / self.file).write_bytes(blob)
        meta = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "automate": automate, "cible": cpu, "taille": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(), "note": note.strip()[:200], "resume": self.describe(blob)}
        tsx_web._write_meta(target, meta)
        return {"id": target.name, "comptes": meta["resume"]}

    def _image(self, backup_id) -> bytes:
        return (_drv().backup_path(self.programs, backup_id) / self.file).read_bytes()

    def list_backups(self):
        from plc_online.web import tsx_web
        return {"dossier": str(self.programs), "sauvegardes": tsx_web.list_copies(self.programs, self.file)}

    def save_program(self, note, cpu_fn=lambda plc: ""):
        def work(plc, progress):
            blob = self.read(plc, progress)
            return self._save(blob, plc.nom, note, cpu_fn(plc))
        return self.s.start_job("sauvegarde du programme", work)

    def compare(self, backup_id):
        saved = self._image(backup_id)

        def work(plc, progress):
            now = self.read(plc, progress)
            if now == saved:
                return {"id": backup_id, "identique": True, "differences": [], "valeurs_db": []}
            places = self.diff(now, saved)
            detail = ", ".join(places[:15]) + (f" (+ {len(places) - 15} autres)" if len(places) > 15 else "")
            if len(now) != len(saved):
                detail = f"automate {self.describe(now)}, copie {self.describe(saved)}" + (f" ; {detail}" if detail else "")
            return {"id": backup_id, "identique": False, "valeurs_db": [],
                    "differences": [{"bloc": "programme", "type": "", "numero": 0, "verdict": "modifie", "detail": detail}]}
        return self.s.start_job("comparaison avec l'automate", work)

    def backup_blocks(self, backup_id):
        import hashlib
        from plc_online.web import tsx_web
        meta = tsx_web._meta(_drv().backup_path(self.programs, backup_id))
        blob = self._image(backup_id)
        rows = [("CPU", meta.get("cible", "")), ("automate", meta.get("automate", "")), ("programme", self.describe(blob)),
                ("sha256", hashlib.sha256(blob).hexdigest()[:16] + "...")]
        return {"id": backup_id, "blocs": [{"bloc": k, "taille": v, "date": "", "somme": ""} for k, v in rows if v]}

    def restore_plan(self, backup_id, with_db=False):
        raise _drv().DriverError(self.refuse_restore)

    def restore(self, backup_id, with_db=False, delete_extra=False):
        raise _drv().DriverError(self.refuse_restore)

    def export_backup(self, backup_id):
        return _drv().export_zip(self.programs, backup_id, self.prefix.replace("-", "_") + "_programme")

    def import_backup(self, files, note):
        import io
        import json
        import zipfile
        from pathlib import Path
        self.programs.mkdir(parents=True, exist_ok=True)
        made, ignored = [], []
        for name, data in files:
            base = Path(name.replace("\\", "/")).name
            if base.lower().endswith(".zip") and data[:4] == b"PK\x03\x04":
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    inner = {Path(n).name: n for n in z.namelist()}
                    if self.file not in inner:
                        ignored.append(base)
                        continue
                    meta = {}
                    if "info.json" in inner:
                        try:
                            meta = json.loads(z.read(inner["info.json"]).decode("utf-8"))
                        except (ValueError, UnicodeDecodeError):
                            pass
                    made.append(self._save(z.read(inner[self.file]), str(meta.get("automate", "")),
                                           note or meta.get("note") or f"copie importee ({base})", str(meta.get("cible", ""))))
            elif base.lower().endswith(".bin") and self.import_ok(data):
                made.append(self._save(data, "", note or f"importe de {base}"))
            else:
                ignored.append(base)
        if not made:
            raise _drv().DriverError("Aucune copie de programme reconnue (.zip de cette page ou .bin de l'outil en ligne "
                                     "de commande)")
        return {"id": made[-1]["id"], "comptes": ", ".join(m["comptes"] for m in made), "ignores": ignored}
