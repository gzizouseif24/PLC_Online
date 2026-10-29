# PLC Online

Multi-brand PLC diagnostic tool: read live data from PLCs of several brands from a PC,
without the vendor software.

One driver per protocol, a shared engine on top (table of expected values → polling → comparison).
Background and field notes: [docs/rapport-technique-2026-10-23.md](docs/rapport-technique-2026-10-23.md).

| Brand | Protocol | Status |
|---|---|---|
| Panasonic FP0R | MEWTOCOL-COM, serial tool port | driver + tools in this repo |
| Siemens S7-300 | S7 via S7ONLINE (libnodave, 32-bit Python) | to add |
| Omron CJ1M | Host Link (C-mode), peripheral port | to add |

## Layout

```
plc_online/
  checksum.py          XOR checksum (Panasonic BCC, Omron FCS)
  drivers/panasonic.py FP0R driver: checks BCC, header and length on every reply
scripts/panasonic/     command-line tools (French prompts)
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

## Tests

```
python -m unittest discover tests
```
