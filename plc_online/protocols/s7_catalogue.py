"""STEP 7 catalog: order number (without spaces) -> short name.

Empty until research/siemens/extraire_noms.py is run on a PC with STEP 7.
"""

NAMES = {}


def catalog_name(order):
    """Short name for an order number ("6ES7 315-2AF03-0AB0" -> "CPU 315-2 DP") or ""."""
    return NAMES.get("".join((order or "").split()).upper(), "")
