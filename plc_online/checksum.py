"""Checksums shared by several protocols."""
from functools import reduce


def xor_checksum(text: str) -> str:
    """XOR of every character, as 2 uppercase hex digits.

    Used by MEWTOCOL (Panasonic BCC) and Host Link (Omron FCS).
    """
    return f"{reduce(lambda acc, c: acc ^ ord(c), text, 0):02X}"
