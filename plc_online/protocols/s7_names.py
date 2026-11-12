"""Texts shared by the S7 terminal tool and the web interface: module names, CPU parameters."""
from plc_online.protocols.s7 import parse_cpu_parameters
from plc_online.protocols.s7_modules import MODULES

OB3X_DEFAULT = {30: 5000, 31: 2000, 32: 1000, 33: 500, 34: 200, 35: 100, 36: 50, 37: 20, 38: 10}
DEFAULT = "par defaut (non modifie dans HW Config)"


def module_text(code):
    """'SM 322 DO16x... : 6ES7 322-1BH01, -1BH10' : one code covers a family of modules
    (same channel count and direction), so give the common name and every order number."""
    entries = MODULES.get(code)
    if not entries:
        return ""
    names = [n for _, n in entries]
    common = names[0]
    for n in names[1:]:
        while not n.startswith(common):
            common = common[:-1]
    if len(names) > 1 and "x" in common:
        common = common[:common.rindex("x") + 1] + "..."
    if len(common.strip()) < 4:
        return f"code partage par {len(entries)} modules differents (ex. {names[0]})"
    orders = [o for o, _ in entries]
    shown = ", ".join(o if i == 0 else o[5:] for i, o in enumerate(orders[:4]))
    return f"{common.strip()} : {shown}" + (", ..." if len(orders) > 4 else "")


def cpu_parameter_lines(sdb0: bytes) -> list:
    """[(label, text)] of the CPU parameters decoded from SDB 0."""
    p = parse_cpu_parameters(sdb0)
    lines = []
    if "surveillance_cycle_ms" in p:
        lines.append(("Surveillance du cycle", f"{p['surveillance_cycle_ms']} ms "
                      f"(charge communication {p['charge_communication']} %)"))
    else:
        lines.append(("Surveillance du cycle", DEFAULT))
    mb = p.get("mem_cadence")
    lines.append(("Memento de cadence", f"MB{mb}" if mb is not None else "non utilise"))
    r = p.get("remanence")
    if r:
        parts = [f"MB0..MB{r['MB'] - 1}" if r["MB"] else "aucun MB",
                 f"T0..T{r['T'] - 1}" if r["T"] else "aucune temporisation",
                 f"Z0..Z{r['Z'] - 1}" if r["Z"] else "aucun compteur"]
        lines.append(("Remanence", ", ".join(parts)))
    else:
        lines.append(("Remanence", DEFAULT))
    ob = p.get("alarmes_cycliques")
    if ob:
        interval, phase = ob.get(35, (0, 0))
        lines.append(("Alarme cyclique OB35", f"toutes les {interval} ms" + (f", decalage {phase} ms" if phase else "")))
        changed = [f"OB{n} {v[0]} ms" for n, v in sorted(ob.items())
                   if n != 35 and (v[0] != OB3X_DEFAULT.get(n) or v[1])]
        if changed:
            lines.append(("Autres alarmes cycliques", ", ".join(changed)))
    else:
        lines.append(("Alarme cyclique OB35", f"{DEFAULT} (100 ms)"))
    if p["inconnus"]:
        lines.append(("Non decodes", "enregistrements " + ", ".join(f"{k:02X}" for k in p["inconnus"])))
    return lines


def address_range(kind: str, start: int, size: int) -> str:
    """'EB0..EB3' for inputs, 'AB4' for one output byte."""
    prefix = {"entrees": "EB", "sorties": "AB"}.get(kind, "B")
    end = start + max(size, 1) - 1
    return f"{prefix}{start}" + (f"..{prefix}{end}" if end > start else "")
