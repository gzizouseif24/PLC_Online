# PLC Online

Multi-brand PLC diagnostic tool: read live data from PLCs of several brands from a PC,
without the vendor software.

One driver per protocol, a shared engine on top (table of expected values → polling → comparison).
Architecture and plan: [docs/architecture.md](docs/architecture.md).
Background and field notes: [docs/rapport-technique-2026-10-23.md](docs/rapport-technique-2026-10-23.md).

| Brand | Protocol | Status |
|---|---|---|
| Panasonic FP0R | MEWTOCOL-COM, serial tool port | driver + tools in this repo |
| Siemens S7-300 | S7 via S7ONLINE (libnodave, 32-bit Python) | driver + read tool (Windows only) |
| Omron CJ1M | Host Link (C-mode), peripheral port | driver + echo test and read tool |
| Schneider Modicon | Modbus RTU (serial) / Modbus TCP | driver + read tool, not yet tested on a PLC |

## Layout

```
plc_online/
  checksum.py          XOR checksum (Panasonic BCC, Omron FCS)
  drivers/panasonic.py FP0R (MEWTOCOL): checks BCC, header and length on every reply
  drivers/omron.py     CJ1M (Host Link): checks FCS, header, end code and length
  drivers/siemens.py   S7-300 (libnodave + S7ONLINE)
  drivers/modbus.py    Modicon (Modbus RTU / TCP): checks CRC or MBAP, function, byte count
scripts/<brand>/       command-line tools (French prompts)
vendor/libnodave/      libnodave.dll (32-bit)
tests/                 tests against a simulated PLC, no hardware needed
docs/                  technical report
```

## Panasonic tools

```
pip install -r requirements.txt
python scripts/panasonic/fp0r_scan.py --port COM6            # fast counters / changing DT values
python scripts/panasonic/fp0r_write.py --port COM6           # write ONE DT, with confirmation and read-back
python scripts/panasonic/fp0r_read_program.py --port COM6    # dump the program to .bin / _hex.txt
```

Each tool accepts `--port`, `--baud`, `--parite`, `--station`.

## Omron tools

PLC side: peripheral port in Host Link, 9600 baud 7-E-2, unit 0, **DIP 4 ON**
(report §4.2). Close CX-Programmer first: it uses the same COM port.

```
python scripts/omron/hl_test.py --port COM6                        # TS echo test
python scripts/omron/hl_read.py --port COM6 --debut 0 --nombre 4   # read D0..D3
```

## Siemens tool

Windows only. What the PC needs:

1. **STEP 7 installed.** It installs `S7onlinx.dll` (the S7ONLINE driver), which
   `libnodave.dll` loads at run time. That file belongs to Siemens and is not in this repo.
2. **"Set PG/PC Interface" set to `USB.S7USB.1`** (PC Adapter USB).
3. **32-bit Python**: both DLLs are 32-bit. The driver stops with a clear message on 64-bit Python.
4. **SIMATIC Manager closed**: only one program can own the interface.

```
py -3.14-32 scripts/siemens/s7read.py                                  # MB0..MB3
py -3.14-32 scripts/siemens/s7read.py --zone DB --db 10 --debut 20 --nombre 2
```

`libnodave.dll` is taken from `vendor/libnodave/` by default; use `--dll` for another copy.

## Modicon tool

Close SoMachine / EcoStruxure first if it uses the same port.
Serial settings (speed, parity, slave number) must match the PLC's serial line configuration.

```
python scripts/modicon/mb_read.py --port COM6 --baud 19200 --parite E --esclave 1   # %MW0..%MW9
python scripts/modicon/mb_read.py --ip 192.168.0.10 --zone M --nombre 16           # %M0..%M15
```

## Tests

```
python -m unittest discover tests
```
