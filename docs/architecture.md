# Architecture

Multi-brand PLC diagnostic tool. One PC (laptop), one PLC at a time, over serial,
the Siemens USB MPI adapter or Ethernet. Web interface served locally by the laptop.

Inspired by Ignition: **drivers are interchangeable modules, everything above them
only knows tags** (a name, an address, a type, a value and its quality).

```
 Browser (localhost)
     │  HTTP + WebSocket (live values)
 ┌───▼──────────────────────────────────────────────┐
 │ Web server                                        │
 ├───────────────────────────────────────────────────┤
 │ Engine                                            │
 │   machine file → tags → read plan (blocks)        │
 │   poller (one thread for the connected PLC)       │
 │   value cache: tag → value, quality, time         │
 │   checks: expected / range → OK, NOK, BAD         │
 ├───────────────────────────────────────────────────┤
 │ Driver interface (same for every brand)           │
 ├──────────┬──────────┬───────────┬─────────────────┤
 │Panasonic │ Omron    │ Modicon   │ Siemens proxy   │──JSON lines──► 32-bit Python
 │MEWTOCOL  │ Host Link│ Modbus    │                 │                └ Siemens driver
 │serial    │ serial   │ RTU / TCP │                 │                  (libnodave + S7ONLINE)
 └──────────┴──────────┴───────────┴─────────────────┘
   + Simulator driver (development and demos without a PLC)
```

## 1. Drivers

Each brand implements the same small interface. The engine never sees frames,
checksums or byte order.

```python
class Driver(Protocol):
    brand: str
    def connect(self) -> None: ...
    def close(self) -> None: ...
    def status(self) -> dict: ...                        # model, RUN/PROG, error codes
    def parse_address(self, text: str) -> Address: ...  # "DT100", "X3", "DB10.DBW20"...
    def read_block(self, area: str, start: int, count: int) -> list[int]: ...
    def max_block(self, area: str) -> int: ...           # frame limit for this area
```

Rules every driver already follows:
- check checksum, header and length of every reply before using its data,
- retry a corrupted reply, never retry an error code from the PLC,
- split long reads into blocks the PLC can handle,
- keep its own byte order (report §6.3),
- read-only, except an explicit single write method used by separate guarded tools.

| Brand | Link | Words | 32-bit | Block limit |
|---|---|---|---|---|
| Panasonic FP0R | MEWTOCOL, serial | little-endian | low word first | 24 words |
| Omron CJ1M | Host Link C-mode, serial | MSD first | low word first | 30 words |
| Siemens S7-300 | libnodave + S7ONLINE, MPI | big-endian, byte-addressed | high word first | to measure |
| Modicon | Modbus RTU serial / TCP | big-endian | low word first | 125 registers, 2000 bits |

### Addresses, including inputs and outputs

The user types the address as it appears in the vendor software.

| Brand | Inputs | Outputs | Internal bits | Data |
|---|---|---|---|---|
| Panasonic | `X0`..`XF`, `WX0` | `Y0`, `WY0` | `R0`, `WR0` | `DT0` |
| Omron | `0.00` (CIO) | `100.00` (CIO) | `W0.00`, `H0.00` | `D0` |
| Siemens | `I0.0`, `IB0`, `IW0` | `Q0.0`, `QB0` | `M0.0`, `MW0` | `DB10.DBW20` |
| Modicon | `%I0.0` * | `%Q0.0` * | `%M0` | `%MW0` |

Bits are read as the word or byte that contains them (one read gives 16 inputs),
then split by the engine.

\* Modicon: %I/%Q are often not in the Modbus map, or only through a mapping
table set in the project. To check per model.

Still to add to the existing drivers for I/O: contact/word reads for X/Y/R
(Panasonic), CIO/W/H areas (Omron), I/Q areas are already supported by libnodave.

### Siemens: separate 32-bit process

libnodave.dll and S7onlinx.dll are 32-bit (report §6.2). The engine runs on normal
64-bit Python and starts a 32-bit Python process for the Siemens driver. They
exchange one JSON object per line on stdin/stdout:

```
→ {"id": 1, "call": "read_block", "args": ["DB", 20, 4], "db": 10}
← {"id": 1, "ok": [4, 210, 0, 0]}
← {"id": 2, "error": "pas de CPU à l'adresse MPI 2"}
```

The engine talks to a `SiemensProxy` that has the same interface as any driver.

## 2. Engine

- **Tag:** name, address, type (BOOL, INT, UINT, DINT, UDINT, REAL), scale, unit,
  label, optional expected value or range.
- **Read plan:** tags sorted by area, neighbouring addresses merged into one block,
  within the driver's `max_block`. 40 tags often become 3 or 4 requests.
- **Poller:** one thread reads the plan in a loop (serial calls block). Fast group
  (I/O, counters) and slow group (setpoints, parameters).
- **Quality:** each value is GOOD, BAD (with the error message) or STALE (not
  refreshed for 3 cycles). A failed block marks only its own tags BAD.
- **Reconnect:** after a lost link, retry with a growing delay; the UI shows
  "déconnecté" instead of freezing.
- **Checks:** value vs expected / range gives OK or NOK, shown next to the value.

## 3. Machine file

One file per customer machine (TOML), reused at every visit:

```toml
[machine]
name = "Ligne 3 - ensacheuse"
customer = "..."

[connection]
driver = "panasonic"
port = "COM6"
baud = 9600
parity = "O"
station = 1

[[tags]]
address = "X3"
label = "Codeur lame"

[[tags]]
address = "DT90308:DINT"
label = "Compteur lame"
group = "fast"

[[tags]]
address = "DT100"
label = "Consigne longueur"
unit = "mm"
expected = 450
tolerance = 5
```

Later, the AI analysis of the program will write this file (labels, expected values,
output conditions). The engine does not depend on the AI: the file can also be
written by hand or built on site with the change finder.

## 4. Web interface (local)

Python web server on the laptop, page opened at `http://localhost:8000`.
Live values pushed to the page over a WebSocket.

Version 1 screens:
1. **Connexion:** brand, port and settings, test, PLC status (model, mode, errors).
2. **E/S:** inputs and outputs as LEDs, with labels.
3. **Valeurs:** watch list with type, unit, OK/NOK.
4. **Recherche:** change finder on an address range; click a changing address to
   add it to the machine file with a label.
5. **Machine:** open / save the machine file.

Later: recording a good cycle and comparing, guarded write, program backup,
"why is this output off" from the output conditions, AI questions with the live state.

## 5. Build order

1. Core model: `Address`, `DataType`, `Tag`, `Quality`; address parsing per brand;
   adapt the 4 drivers to the interface; I/O reads (X/Y/R, CIO, I/Q, %M).
2. Simulator driver, read plan, poller, cache, quality, reconnect. Tested without a PLC.
3. Siemens 32-bit process and proxy.
4. Machine file load / save / validate; checks.
5. Web interface, screens 1 to 5.
