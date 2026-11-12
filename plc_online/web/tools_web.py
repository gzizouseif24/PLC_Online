"""Shared tools of the web interface for the serial PLCs:
  - Changements: read a range of one zone, again and again, to log what changes;
  - Test E/S: the inputs of each unit, for a wiring check (each input seen at 1 then back at 0)."""

MAX_BLOCK = 256

# zones per family: (zone, words?) ; X / Y of Delta and Mitsubishi are octal
BLOCK_ZONES = {
    "panasonic": [("DT", True), ("WR", True), ("WX", True), ("WY", True)],
    "delta": [("D", True), ("T", True), ("C", True), ("M", False), ("X", False), ("Y", False), ("S", False)],
    "mitsubishi": [("D", True), ("T", True), ("C", True), ("M", False), ("X", False), ("Y", False), ("S", False)],
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
    raise _drv().DriverError("Test des entrees : Panasonic, Mitsubishi et Delta")


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
