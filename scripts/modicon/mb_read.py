#!/usr/bin/env python3
"""Modicon - lecture Modbus (RTU serie ou TCP Ethernet), lecture seule.

    python scripts/modicon/mb_read.py --port COM6 --baud 19200 --parite E      (%MW0..%MW9)
    python scripts/modicon/mb_read.py --ip 192.168.0.10 --zone M --debut 0 --nombre 16

Zones : MW = registres de maintien (03), IW = registres d'entree (04),
        M = bits internes / coils (01), I = entrees TOR (02).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from plc_online.drivers.modbus import (  # noqa: E402
    Modicon, ModbusError, RtuTransport, TcpTransport, open_serial, signed16)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    link = p.add_mutually_exclusive_group()
    link.add_argument("--port", help="port serie (Modbus RTU), ex. COM6")
    link.add_argument("--ip", help="adresse IP (Modbus TCP)")
    p.add_argument("--baud", type=int, default=19200)
    p.add_argument("--parite", default="E", choices=["E", "O", "N"])
    p.add_argument("--stop", type=int, default=1, choices=[1, 2])
    p.add_argument("--esclave", type=int, default=1, help="numero d'esclave / unite Modbus")
    p.add_argument("--zone", choices=["MW", "IW", "M", "I"], default="MW")
    p.add_argument("--debut", type=int, default=0)
    p.add_argument("--nombre", type=int, default=10)
    a = p.parse_args()

    if a.ip:
        transport = TcpTransport(a.ip)
        print(f"Modbus TCP {a.ip}:502, unite {a.esclave}\n")
    else:
        port = a.port or "COM6"
        transport = RtuTransport(open_serial(port, a.baud, a.parite, a.stop))
        print(f"Modbus RTU {port} @ {a.baud} 8{a.parite}{a.stop}, esclave {a.esclave}\n")

    plc = Modicon(transport, a.esclave)
    try:
        read = {"MW": plc.read_holding, "IW": plc.read_input_registers,
                "M": plc.read_coils, "I": plc.read_discrete_inputs}[a.zone]
        values = read(a.debut, a.nombre)
    except ModbusError as e:
        raise SystemExit(f"-> {e}")
    finally:
        plc.close()

    for i, v in enumerate(values):
        name = f"%{a.zone}{a.debut + i}"
        if a.zone in ("MW", "IW"):
            print(f"{name:<8} = {v:5d}   (signe {signed16(v):6d}, hex {v:04X})")
        else:
            print(f"{name:<8} = {int(v)}")


if __name__ == "__main__":
    main()
