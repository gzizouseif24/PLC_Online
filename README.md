# PLC Online

Multi-brand PLC diagnostic tool for maintenance: status, faults, live values, program backup
and restore, from a PC, without the vendor software.

One protocol module per brand, one command-line tool per brand (French prompts); a web
interface comes later on top of the same library.

| Brand | Link | What works (tested on the bench) |
|---|---|---|
| Siemens S7-300 | MPI, USB adapter + STEP 7's S7ONLINE driver (Windows) | status, diagnostic buffer, modules configured vs plugged in, HW config and CPU parameters, program backup / compare / restore, watch table, modify (315-2DP) |
| Omron CJ1M | Host Link (C-mode + FINS), peripheral port | status with I/O cards, error log, PLC Setup, read, monitor, write, mode, data and program backup (CJ1M-CPU11); data restore to confirm |
| Schneider TSX Micro / Premium | Uni-Telway, TER port (PCX 1031) | status, read, monitor, write, RUN/STOP, program and data backup / restore (TSX 3721) |
| Panasonic FP0R | MEWTOCOL-COM, tool port | fast counters, DT change scan, write one DT, program dump |
| Schneider Modicon | Modbus RTU / TCP | read; not yet tested on a PLC |

Docs:
- [architecture](docs/architecture.md)
- [technical report and field notes](docs/rapport-technique-2026-10-23.md)
- [S7-300 plan and bench results](docs/s7-300.md)
- [Omron notes](docs/omron.md)
- [Uni-Telway notes](docs/unitelway.md)
- [understanding the PLC logic](docs/logique.md)
- work logs: [29/10](docs/journal-2026-10-29.md), [30/10](docs/journal-2026-10-30.md)

## Layout

```
tools/                 WHAT YOU RUN: one command-line tool per brand
  siemens.py           Siemens S7-300
  omron.py             Omron CJ1M
  tsx.py               Schneider TSX (single file, protocol included)
  panasonic.py         Panasonic FP0R
  modicon.py           Schneider Modicon
plc_online/            library used by the tools (and later by the web interface)
  checksum.py          XOR checksum (Panasonic BCC, Omron FCS)
  protocols/           one module per protocol, never run directly
    s7.py              S7: connection (libnodave + S7ONLINE), status lists, blocks, SDB decoding
    s7_vars.py         S7 watch table: addresses, formats, grouped reads
    s7_download.py     S7 block download (restore), straight on S7ONLINE
    s7_events.py       S7 diagnostic event texts (Siemens manual, chapter 35)
    s7_modules.py      S7 module type codes -> names   (generated from STEP 7)
    s7_catalogue.py    S7 order numbers -> names        (generated from STEP 7)
    s7_forces.py       S7 force reading (parked: refused by the bench CPU)
    hostlink.py        Omron Host Link + FINS
    mewtocol.py        Panasonic MEWTOCOL
    modbus.py          Modbus RTU / TCP
research/              discovery scripts and bench samples, not needed day to day
  siemens/             STEP 7 catalog extraction, SDB samples from the bench, force probe
  tsx/                 Uni-Telway probes, PL7 capture proxies, test programs (.FEF)
  omron/               first Host Link scripts
  hlp2txt.py           text out of old Windows Help (.hlp) files
exemples/              sample watch table
vendor/libnodave/      libnodave.dll (32-bit)
tests/                 tests against simulated PLCs, no hardware needed
docs/                  report, architecture, protocol notes, work logs
```

Every tool runs from the repo folder: `python tools/<brand>.py --help`.

## Siemens tool

Windows only. The PC needs:

1. **STEP 7 installed**, for `S7onlinx.dll` (the S7ONLINE driver, Siemens' file, not in this repo).
2. **"Set PG/PC Interface" = `USB.S7USB.1`** (PC Adapter USB).
3. **32-bit Python** (`py -3.14-32`): both DLLs are 32-bit.
4. **SIMATIC Manager closed**: only one program can own the interface.

```
py -3.14-32 tools/siemens.py etat                    # CPU name, firmware, mode, fault LEDs (FRCE = forces), key, protection, checksums
py -3.14-32 tools/siemens.py diagnostic              # diagnostic buffer: dated events in plain text
py -3.14-32 tools/siemens.py modules                 # rack 0: module configured vs plugged in, with names
py -3.14-32 tools/siemens.py config                  # HW config: CPU parameters, configured modules and addresses, vs rack
py -3.14-32 tools/siemens.py sauvegarder-config      # copy the HW config (SDB blocks) to a folder
py -3.14-32 tools/siemens.py sauvegarder-programme   # copy every block (OB FB FC DB SDB) to a folder
py -3.14-32 tools/siemens.py comparer DOSSIER        # which blocks changed in the CPU since that copy
py -3.14-32 tools/siemens.py restaurer DOSSIER       # load the copy back (STOP, load, check, restart)
py -3.14-32 tools/siemens.py surveiller EB0:bin AB4 MW10 DB10.DBD4:real T1 Z2   # watch table, live
py -3.14-32 tools/siemens.py surveiller --table exemples/table_s7_banc.txt --journal
py -3.14-32 tools/siemens.py ecrire MW10 123 M0.1 1  # modify values (confirmation, read back, undo command)
py -3.14-32 tools/siemens.py ecrire M0.1 1 --maintenir   # keep writing until Ctrl+C
```

Good to know:
- `config --depuis DOSSIER` and `comparer A --depuis B` work on copies, without a CPU (normal Python).
- `restaurer` loads OB, FB and FC. DB only with `--avec-db` (they hold the machine's current values),
  the HW config never. Blocks in the CPU but not in the copy are kept unless `--supprimer-en-trop`.
- Addresses: `E0.0`/`I0.0`, `EB0` `EW0` `ED0`, `A4.1`/`Q4.1`, `M10.3` `MB10` `MW10` `MD10`,
  `DB10.DBX2.1` `DB10.DBB2` `DB10.DBW2` `DB10.DBD2`, `T5`, `Z2`/`C2`. Formats after `:`: `hex`, `bin`,
  `real`, `ud` (unsigned), `char`.
- Modify writes once, like "Modify" in a STEP 7 watch table: in RUN the program can overwrite it,
  and inputs / outputs are rewritten every cycle. Forces are not supported (only the FRCE LED).
- Module and CPU names come from tables generated from a STEP 7 installation
  (`research/siemens/extraire_modules.py`, `research/siemens/extraire_noms.py`).
- `--trace` shows the connection steps and raw answers; `--mpi` (default 2) for another CPU.

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
Discovery scripts and PL7 capture proxies: `research/tsx/`.

## Panasonic tool

```
pip install -r requirements.txt
python tools/panasonic.py compteurs                  # fast counters CH0..CH3, live
python tools/panasonic.py scan --debut 0 --fin 200   # print the DT values that change
python tools/panasonic.py ecrire 914 1234            # write ONE DT, confirmation + read-back
python tools/panasonic.py programme                  # dump the program to .bin / _hex.txt (115200 baud)
```

Options: `--port`, `--baud`, `--parite`, `--station`.

## Modicon tool

Close SoMachine / EcoStruxure first if it uses the same port.
Serial settings (speed, parity, slave number) must match the PLC's serial line configuration.

```
python tools/modicon.py --port COM6 --baud 19200 --parite E --esclave 1   # %MW0..%MW9
python tools/modicon.py --ip 192.168.0.10 --zone M --nombre 16           # %M0..%M15
```

## Tests

```
python -m unittest discover tests
```

No hardware needed: every protocol is tested against simulated PLCs and real answers recorded on the bench.
