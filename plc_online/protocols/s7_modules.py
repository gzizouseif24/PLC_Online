"""S7-300 module type codes (SZL 0D91 solltyp / isttyp) -> (order number, name).

Empty until research/siemens/extraire_modules.py is run on a PC with STEP 7."""

MODULES = {}


def module_name(code):
    """Short text for a type code: "6ES7 321-1BH02-0AA0 DI16xDC24V" or ""."""
    entries = MODULES.get(code)
    if not entries:
        return ""
    order, name = entries[0]
    more = f" (+{len(entries) - 1} autres)" if len(entries) > 1 else ""
    return f"{order} {name}".strip() + more
