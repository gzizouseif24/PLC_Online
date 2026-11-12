# PLC Online

A maintenance tool that connects a PC to PLCs of several brands, **without the vendor software**:
status and faults, live values, input test, program and data backups.

It runs as a local web page (French or English) and also as one command-line tool per brand.
Both use the same library.

## Start

```
pip install -r requirements.txt
python -m plc_online.web
```

The browser opens on `http://localhost:8000`. The server only listens on this PC.

- **Siemens S7-300:** use **32-bit Python** (`py -3.14-32 -m plc_online.web`), because the MPI
  driver DLLs are 32-bit.
- Options: `--port 8000`, `--sans-navigateur` (don't open the browser), `--sauvegardes FOLDER`.
- Copies are saved in `~/PLC Online/sauvegardes/<brand>/`.
- To try the tool without a PLC, pick a **Demo** in the list. Demos are only listed for brands
  that have not yet been tested on a real PLC.

## Supported PLCs

| Brand | Connection | Default settings | Tested on a real PLC |
|---|---|---|---|
| Siemens S7-300 | USB MPI adapter + STEP 7 driver (Windows) | MPI 2, rack 0, slot 2 | yes (315-2 DP) |
| Schneider TSX Micro / Premium | TSX PCX 1031 cable, TER port | 9600 8O1 | yes (TSX 3721) |
| WAGO 750 (Ethernet coupler) | Ethernet, Modbus TCP | automatic search | yes (750-352) |
| Panasonic FP0R | tool port (mini-DIN 5) | 9600 8O1, station 1 | yes |
| Mitsubishi FX1N | SC-09 / USB-SC09 cable, programming port (mini-DIN 8) | 9600 7E1 | yes (FX1N-40MR) |
| Delta DVP | COM1 RS-232 or COM2 RS-485 | ASCII 9600 7E1, station 1 | not yet |
| Omron CJ1M | peripheral port, Host Link | 9600 7E2, unit 0, DIP 4 ON | partly (reads) |
| Allen-Bradley ControlLogix / GuardLogix | Ethernet (EtherNet/IP) | IP + CPU slot | not yet |
| Modicon / any Modbus slave | Modbus RTU or TCP, read only | 19200 8E1, slave 1 | not yet |

Always **close the vendor software** first (STEP 7, PL7, FPWIN, GX Developer, CX-Programmer…):
it would hold the same port.

## The tabs

| Tab | What it does |
|---|---|
| **Etat** | Model, RUN / STOP, LEDs, active faults in plain words, scan time, clock. Buttons: RUN / STOP, set the clock to the PC time. |
| **Diagnostic** | System bits / registers explained, error log or diagnostic buffer, rack: each module with its addresses and live values. |
| **Surveillance** | Watch table: any address, live, with a format per row (dec, hex, bin…). Writing with confirmation and read-back. |
| **Changements** | Reads a whole range (e.g. D0..D199) again and again and logs every value that changes, with the time. Use it to find which register a setting uses. |
| **Test E/S** | Wiring check: every input of every unit. An input turns green once it has been seen at 1 then back at 0, with the wiring notes. |
| **Programme** | Save the program with a note, compare the PLC with a copy, download / import copies. Restore: S7-300 and TSX only. |
| **Donnees** | Copy of the data words (recipes, counters, settings) to CSV, then restore only the words that changed. |
| **Axes** | Allen-Bradley only: Kinetix drives, faults (same codes as the drive display), inhibits, fault history. Read only. |

In the **Surveillance** tab:
- **Journal des changements** records every change with the time and exports it to CSV. Use it
  for intermittent faults.
- **Maintenir** writes the values again every 0.5 s until you stop. When you stop, it offers to
  put the old values back. It is **not a real force**: the program can still change the value
  in between.

Not every tab exists for every brand. The page shows only the tabs that work with the connected PLC.

## Safety

- Every write, RUN / STOP and restore asks for confirmation. Values are read back after writing.
- Before a program restore, save the PLC first (Programme tab). The TSX restore does it by itself.
- The program copies for Panasonic, Mitsubishi and Omron are **read only**: to compare and keep a record.
  Load programs with the vendor software.
- **Omron** refuses writes in RUN: use the **Passer en MONITOR** button (the program keeps running).
- Allen-Bradley safety tags and Kinetix drives are never written.

## Notes per brand

- **Siemens S7-300**:
  - Needs STEP 7 installed, for its S7ONLINE driver.
  - "Set PG/PC Interface" must be `USB.S7USB.1`, and SIMATIC Manager must be closed.
  - Restore loads OB, FB and FC, and DB only if asked. It never loads the hardware configuration.
  - A STEP 7 V5 project (its folder or a `.zip` archive) can be imported as a copy.
- **TSX**:
  - Program and data copies also read PL7 files (`.stx`, `.dat`).
  - Live `%I` / `%Q` are not readable yet. The *Recherche des %I* wizard at the bottom of the
    Diagnostic tab collects what is needed.
- **Panasonic FP0R**:
  - If the link is lost, the **Chercher les reglages** button tries every speed, format and station.
  - Enter the CPU model and the expansion units at connection: the FP0R does not report them.
  - Details: [docs/fp0r.md](docs/fp0r.md).
- **Mitsubishi FX1N**:
  - Enter the base unit and the expansions in order. I/O numbers are octal (X0..X7, X10…).
  - Readable areas: D0..D511, M0..M1023, and the special M8000.. / D8000...
  - RUN / STOP uses M8035..M8037; the PLC's own RUN / STOP switch may take priority.
  - Details: [docs/mitsubishi.md](docs/mitsubishi.md).
- **Delta DVP**:
  - **Chercher les reglages** finds the format and station.
  - **Ecouter l'IHM** listens to an HMI on the RS-485 line and gives the settings to use.
- **Omron CJ1M**:
  - Cards are named from the PLC's I/O table. Known codes: CJ1W-ID211, CJ1W-OC211.
  - For a new card, add its code in `plc_online/protocols/hostlink.py` (`IO_UNIT_CODES`).
  - Details: [docs/omron.md](docs/omron.md).
- **WAGO**:
  - **Recherche automatique** finds the coupler and can give the PC an address in its network.
- **Allen-Bradley**:
  - Needs `pycomm3` (in `requirements.txt`).
  - The PC must be in the same network as the controller.

## Command-line tools

The same functions are available in a terminal, with French prompts. They are useful without a browser or for scripts.

```
python tools/<brand>.py --help
```

| Tool | Commands |
|---|---|
| `tools/siemens.py` (32-bit Python) | etat, diagnostic, modules, config, sauvegarder-config, sauvegarder-programme, comparer, restaurer, importer-projet, surveiller, ecrire, lire |
| `tools/tsx.py` | etat, lire, surveiller, ecrire, run, stop, horloge, sauvegarder, comparer, restaurer, sauvegarder-donnees, restaurer-donnees, chercher-es |
| `tools/omron.py` | etat, erreurs, parametres, lire, surveiller, ecrire, run, monitor, programme, sauvegarder-donnees, restaurer-donnees, sauvegarder, comparer |
| `tools/panasonic.py` | chercher, scan, ecrire, programme, test-entrees |
| `tools/mitsubishi.py` | info, lire, ecrire, test-entrees, programme |
| `tools/delta.py` | chercher, ecouter-ihm, etat, lire, surveiller, ecrire, horloge, sauvegarder-donnees, restaurer-donnees |
| `tools/wago.py` | chercher, etat, surveiller, registres |
| `tools/modicon.py` | read %MW / %IW / %M / %I (RTU with `--port`, TCP with `--ip`) |

Each of these has its equivalent in the web page.

## Folder layout

```
plc_online/protocols/   one module per protocol (s7*, unitelway, hostlink, mewtocol, melsec_fx, delta, modbus, wago, logix)
plc_online/web/         web server (standard library only), one module per brand, page in static/
tools/                  command-line tools, one per brand
tests/                  tests against simulated PLCs, no hardware needed
docs/                   protocol notes, bench results, technical report
research/               discovery scripts and bench samples (not needed day to day)
vendor/libnodave/       libnodave.dll (32-bit, for the S7-300)
exemples/               sample watch table
```

## Documentation

- [Architecture](docs/architecture.md)
- [Web interface: status per brand](docs/interface.md)
- Protocol notes:
  - [S7-300](docs/s7-300.md)
  - [TSX / Uni-Telway](docs/unitelway.md)
  - [Omron](docs/omron.md)
  - [Panasonic FP0R](docs/fp0r.md)
  - [Mitsubishi FX](docs/mitsubishi.md)
- [Technical report and field notes](docs/rapport-technique-2026-10-23.md)
- [Understanding the PLC logic](docs/logique.md)

## Tests

```
python -m unittest discover tests
```

No hardware needed: every protocol is tested against simulated PLCs and against real replies
recorded on the bench.
