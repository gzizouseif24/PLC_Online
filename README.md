# PLC Online

Multi-brand PLC diagnostic tool: read live data from PLCs of several brands from a PC,
without the vendor software.

One protocol module per brand, one command-line tool per brand, a shared engine on top (table of expected values → polling → comparison).
Architecture and plan: [docs/architecture.md](docs/architecture.md).
Background and field notes: [docs/rapport-technique-2026-10-23.md](docs/rapport-technique-2026-10-23.md).
Next: S7-300 diagnostics and logic, plan in [docs/s7-300.md](docs/s7-300.md); logic understanding in general: [docs/logique.md](docs/logique.md).
Work log: [docs/journal-2026-10-29.md](docs/journal-2026-10-29.md), [docs/journal-2026-10-30.md](docs/journal-2026-10-30.md).

| Brand | Protocol | Status |
|---|---|---|
| Panasonic FP0R | MEWTOCOL-COM, serial tool port | `tools/panasonic.py`: fast counters, DT change scan, write one DT, program dump |
| Siemens S7-300 | S7 via S7ONLINE (libnodave, 32-bit Python) | `tools/siemens.py`: read (Windows only) |
| Omron CJ1M | Host Link (C-mode + FINS), peripheral port | `tools/omron.py`: status with I/O cards, read, monitor, error log, PLC Setup, write, mode, data and program backup (tested on a CJ1M-CPU11); data restore to confirm |
| Schneider Modicon | Modbus RTU (serial) / Modbus TCP | `tools/modicon.py`: read, not yet tested on a PLC |
| Schneider TSX Micro / Premium | Uni-Telway, TER port (PCX 1031) | `tools/tsx.py`: status, read, monitor, write, RUN/STOP, program and data backup/restore (tested on TSX 3721) |

## Layout

```
tools/                 WHAT YOU RUN: one command-line tool per brand (French prompts)
  omron.py             Omron CJ1M
  panasonic.py         Panasonic FP0R
  siemens.py           Siemens S7-300
  modicon.py           Schneider Modicon
  tsx.py               Schneider TSX (single file, protocol included)
plc_online/            library used by the tools (and later by the engine and web page)
  checksum.py          XOR checksum (Panasonic BCC, Omron FCS)
  protocols/           one module per protocol, never run directly
    hostlink.py        Omron Host Link + FINS: checks FCS, header, end code and length
    mewtocol.py        Panasonic MEWTOCOL: checks BCC, header and length
    s7.py              Siemens S7 (libnodave + S7ONLINE)
    modbus.py          Modbus RTU / TCP: checks CRC or MBAP, function, byte count
research/              discovery scripts kept for reference, not needed day to day
  tsx/                 Uni-Telway probes (ut_*.py), PL7 capture proxies, test programs (.FEF)
  omron/               first Host Link scripts (echo test, D read)
  hlp2txt.py           extracts text from old Windows Help (.hlp) files, e.g. PL7's help
vendor/libnodave/      libnodave.dll (32-bit)
tests/                 tests against simulated PLCs, no hardware needed
docs/                  technical report, architecture, protocol notes, work log
```

Every tool runs from the repo folder: `python tools/<brand>.py --help`.

## Panasonic tool

```
pip install -r requirements.txt
python tools/panasonic.py compteurs                  # fast counters CH0..CH3, live
python tools/panasonic.py scan --debut 0 --fin 200   # print the DT values that change
python tools/panasonic.py ecrire 914 1234            # write ONE DT, confirmation + read-back
python tools/panasonic.py programme                  # dump the program to .bin / _hex.txt (115200 baud)
```

Options: `--port`, `--baud`, `--parite`, `--station`.

## Omron tool

Tested on a CJ1M-CPU11 with a CJ1W-ID211 (16 inputs) and a CJ1W-OC211 (16 relay outputs).

PLC side: peripheral port in Host Link, 9600 baud 7-E-2, unit 0, **DIP 4 ON**
(report §4.2). Close CX-Programmer first: it uses the same COM port.

```
python tools/omron.py etat                  # model, mode, faults, battery, clock, I/O cards + live CIO
python tools/omron.py erreurs               # PLC error log
python tools/omron.py parametres            # PLC Setup (ports, battery detection, cycle), I/O table
python tools/omron.py lire D 0 4            # zones: CIO W H A D T C (or: lire D0 4)
python tools/omron.py surveiller CIO 0 2    # print each input / output bit that changes
python tools/omron.py ecrire D 100 1234     # one word, confirmation + read-back
python tools/omron.py monitor               # or run, programme, with confirmation
python tools/omron.py sauvegarder-donnees   # all D, H, W to a CSV file (--zones D,H,W,CIO)
python tools/omron.py restaurer-donnees FILE  # write back the changed D, H, W from that CSV, then check
python tools/omron.py sauvegarder           # program to a .cjbak file (compiled code, not ladder)
python tools/omron.py comparer FILE.cjbak   # has the program changed since that backup?
```

Options: `--port COM6`, `--baud 9600`, `--unite 0`, `--trace` (prints every frame).

Good to know:
- The PLC refuses writes in RUN. `ecrire` and `restaurer-donnees` offer to switch to MONITOR
  (the program keeps running) and back to RUN afterwards. They never write CIO (outputs) or
  A0–A447 (read-only system area).
- At 9600 baud, all of D (32,768 words) takes about 5 minutes to read; the program about 1 minute.
- `etat` names the cards from the registered I/O table. Known codes: `0402` = CJ1W-ID211,
  `0450` = CJ1W-OC211. For another card, `etat` shows its code: add it to `IO_UNIT_CODES` in
  `plc_online/protocols/hostlink.py` with the model shown in CX-Programmer's I/O table.
- If FINS is refused, `etat` and `erreurs` fall back to the system area (A400–A402, A351–A354,
  A100–A199).

Protocol notes, what is confirmed, bench results and checklist: [docs/omron.md](docs/omron.md).
Formats checked against Omron manuals W342 (communication commands) and W393 (CJ1M operation).

## Siemens tool

Windows only. What the PC needs:

1. **STEP 7 installed.** It installs `S7onlinx.dll` (the S7ONLINE driver), which
   `libnodave.dll` loads at run time. That file belongs to Siemens and is not in this repo.
2. **"Set PG/PC Interface" set to `USB.S7USB.1`** (PC Adapter USB).
3. **32-bit Python**: both DLLs are 32-bit. The driver stops with a clear message on 64-bit Python.
4. **SIMATIC Manager closed**: only one program can own the interface.

```
py -3.14-32 tools/siemens.py etat                             # CPU, versions, LEDs (mode, faults, forces), key switch, protection
py -3.14-32 tools/siemens.py diagnostic                       # diagnostic buffer: dated events with their text
py -3.14-32 tools/siemens.py lire                             # MB0..MB3
py -3.14-32 tools/siemens.py lire --zone DB --db 10 --debut 20 --nombre 2
```

`--trace` shows the connection steps and the raw answers. Plan for the next steps: [docs/s7-300.md](docs/s7-300.md).

`libnodave.dll` is taken from `vendor/libnodave/` by default; use `--dll` for another copy.

## Modicon tool

Close SoMachine / EcoStruxure first if it uses the same port.
Serial settings (speed, parity, slave number) must match the PLC's serial line configuration.

```
python tools/modicon.py --port COM6 --baud 19200 --parite E --esclave 1   # %MW0..%MW9
python tools/modicon.py --ip 192.168.0.10 --zone M --nombre 16           # %M0..%M15
```

## TSX tool (Uni-Telway)

Single file, `pip install pyserial`. Close PL7 first.

```
python tools/tsx.py etat                  # identification, faults, battery, clock, Flash backup
python tools/tsx.py lire MW 0 20          # zones: MW MD KW KD SW S M
python tools/tsx.py surveiller MW 0 100   # print what changes
python tools/tsx.py ecrire MW 10 1234     # one word, confirmation + read-back
python tools/tsx.py run                   # or stop, with confirmation
python tools/tsx.py sauvegarder           # copy the PLC program to a file (+ .txt summary)
python tools/tsx.py comparer FILE         # has the program changed since that backup?
python tools/tsx.py restaurer FILE        # put a backed-up program back
python tools/tsx.py sauvegarder-donnees   # all %MW and %KW values to a CSV file
python tools/tsx.py restaurer-donnees F   # write back the %MW from that CSV (changed words only)
```

Protocol notes and what is confirmed: [docs/unitelway.md](docs/unitelway.md).
All commands tested on a TSX 3721 (9600 baud, 8 bits, odd parity; PC = slave 1).
Discovery scripts used to work it out (in `research/tsx/`): `ut_listen.py`, `ut_test.py`, `ut_probe.py`, `ut_sysbits.py`,
`ut_words.py`, `ut_rack.py`, `ut_modules.py`, `ut_io_probe.py`.
Capturing PL7 traffic: `tsx_serial_proxy.py` (low-latency proxy, Python 2.7 on XP) or Portmon
with "Show Hex" on and "Max Output Bytes" ≥ 512.

## Tests

```
python -m unittest discover tests
```
