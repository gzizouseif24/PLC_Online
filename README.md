# PLC Online

Multi-brand PLC diagnostic tool: read live data from PLCs of several brands from a PC,
without the vendor software.

One driver per protocol, a shared engine on top (table of expected values → polling → comparison).
Architecture and plan: [docs/architecture.md](docs/architecture.md).
Background and field notes: [docs/rapport-technique-2026-10-23.md](docs/rapport-technique-2026-10-23.md).
Work log: [docs/journal-2026-10-29.md](docs/journal-2026-10-29.md), [docs/journal-2026-10-30.md](docs/journal-2026-10-30.md).

| Brand | Protocol | Status |
|---|---|---|
| Panasonic FP0R | MEWTOCOL-COM, serial tool port | driver + tools in this repo |
| Siemens S7-300 | S7 via S7ONLINE (libnodave, 32-bit Python) | driver + read tool (Windows only) |
| Omron CJ1M | Host Link (C-mode + FINS), peripheral port | `scripts/omron/omron.py`: status, read, monitor, error log, data backup/restore (D read and echo tested on a CJ1M, rest to verify); write, mode, program backup experimental |
| Schneider Modicon | Modbus RTU (serial) / Modbus TCP | driver + read tool, not yet tested on a PLC |
| Schneider TSX Micro / Premium | Uni-Telway, TER port (PCX 1031) | `scripts/tsx/tsx.py`: status, read, monitor, write, RUN/STOP, program and data backup/restore (tested on TSX 3721) |

## Layout

```
plc_online/
  checksum.py          XOR checksum (Panasonic BCC, Omron FCS)
  drivers/panasonic.py FP0R (MEWTOCOL): checks BCC, header and length on every reply
  drivers/omron.py     CJ1M (Host Link + FINS): checks FCS, header, end code and length
  drivers/siemens.py   S7-300 (libnodave + S7ONLINE)
  drivers/modbus.py    Modicon (Modbus RTU / TCP): checks CRC or MBAP, function, byte count
scripts/<brand>/       command-line tools (French prompts)
scripts/tsx/           tsx.py (the TSX tool, single file) + discovery scripts and proxies
tools/hlp2txt.py       extracts text from old Windows Help (.hlp) files, e.g. PL7's help
vendor/libnodave/      libnodave.dll (32-bit)
tests/                 tests against simulated PLCs, no hardware needed
docs/                  technical report, architecture, protocol notes, work log
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
python scripts/omron/omron.py etat                  # model, mode, faults, battery, clock
python scripts/omron/omron.py erreurs               # PLC error log
python scripts/omron/omron.py lire D 0 4            # zones: CIO W H A D T C (or: lire D0 4)
python scripts/omron/omron.py surveiller CIO 0 2    # print each input / output bit that changes
python scripts/omron/omron.py ecrire D 100 1234     # one word, confirmation + read-back (MONITOR mode)
python scripts/omron/omron.py monitor               # or run, programme, with confirmation
python scripts/omron/omron.py sauvegarder-donnees   # all D, H, W to a CSV file (--zones D,H,W,CIO)
python scripts/omron/omron.py restaurer-donnees FILE  # write back changed D, H, W from that CSV, then check
python scripts/omron/omron.py sauvegarder           # program to a file (experimental)
python scripts/omron/omron.py comparer FILE         # has the program changed? (experimental)
python scripts/omron/hl_test.py --port COM6         # TS echo test only
```

Notes, what is confirmed and a bench checklist: [docs/omron.md](docs/omron.md).

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

## TSX tool (Uni-Telway)

Single file, `pip install pyserial`. Close PL7 first.

```
python scripts/tsx/tsx.py etat                  # identification, faults, battery, clock, Flash backup
python scripts/tsx/tsx.py lire MW 0 20          # zones: MW MD KW KD SW S M
python scripts/tsx/tsx.py surveiller MW 0 100   # print what changes
python scripts/tsx/tsx.py ecrire MW 10 1234     # one word, confirmation + read-back
python scripts/tsx/tsx.py run                   # or stop, with confirmation
python scripts/tsx/tsx.py sauvegarder           # copy the PLC program to a file (+ .txt summary)
python scripts/tsx/tsx.py comparer FILE         # has the program changed since that backup?
python scripts/tsx/tsx.py restaurer FILE        # put a backed-up program back
python scripts/tsx/tsx.py sauvegarder-donnees   # all %MW and %KW values to a CSV file
python scripts/tsx/tsx.py restaurer-donnees F   # write back the %MW from that CSV (changed words only)
```

Protocol notes and what is confirmed: [docs/unitelway.md](docs/unitelway.md).
All commands tested on a TSX 3721 (9600 baud, 8 bits, odd parity; PC = slave 1).
Discovery scripts used to work it out: `ut_listen.py`, `ut_test.py`, `ut_probe.py`, `ut_sysbits.py`,
`ut_words.py`, `ut_rack.py`, `ut_modules.py`, `ut_io_probe.py`.
Capturing PL7 traffic: `tsx_serial_proxy.py` (low-latency proxy, Python 2.7 on XP) or Portmon
with "Show Hex" on and "Max Output Bytes" ≥ 512.

## Tests

```
python -m unittest discover tests
```
