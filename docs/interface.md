# Web interface: plan for the other brands

The S7-300 screens are done (Etat, Diagnostic, Surveillance, Programme). Next: Omron, TSX and
Panasonic in the same page, reusing what their command-line tools already do. No new protocol
work in this step: only what `tools/omron.py`, `tools/tsx.py` and `tools/panasonic.py` can do.

## Principle

- Same tabs for every brand, so the technician learns one screen. A tab the brand cannot fill is
  hidden (each brand declares its tabs in `BRANDS`).
- One driver per brand in `plc_online/web/drivers.py` with the same calls as the S7-300:
  `status`, `diagnostic`, `values`, `write`, `save_program`, `compare`, `restore`, plus `run` /
  `stop`. The page does not know which brand it talks to.
- Words, not bytes: addresses typed as in the vendor software (D100, CIO0.05, %MW10, DT100), with
  the same format menu per row (dec, hex, bin, real...).
- Serial link for all three: connection screen gets a COM port menu (ports found on the PC),
  baud rate and unit / station number, with the vendor defaults filled in.

## Screens per brand

| Tab | Omron CJ1M | TSX Micro / Premium | Panasonic FP0R |
|---|---|---|---|
| Connexion | COM port, 9600 7-E-2, unit 0 | COM port, Uni-Telway address | COM port, baud, parity, station |
| Etat | model, mode, faults, battery, clock, I/O cards; RUN / MONITOR / PROGRAM buttons | identification, faults, battery, clock; RUN / STOP buttons | (no status read yet: tab hidden) |
| Diagnostic | error log (FINS, or A100..A199), PLC Setup, I/O table | system bits %S / %SW explained | hidden |
| Surveillance | CIO W H A D T C, write one word with read back | %MW %MD %KW %KD %M %S %SW, write one word | DT, fast counters CH0..CH3 live |
| Programme | save / compare (experimental, shown as such) | save / compare / restore | save the program dump |
| Donnees (new) | save all D, H, W to CSV, restore changed words | save %MW %KW to CSV, restore %MW | hidden |

New tab **Donnees**: data backup / restore (machine recipes, counters, settings), separate from
the program. Same list, download and import as the Programme tab, CSV files.

## Order

1. **Shared serial connection** (COM port list, settings per brand, one port open at a time,
   "close the vendor software first" hint). Demo mode for each brand, like the S7-300 demo.
2. **TSX** first: every command is tested on the bench (TSX 3721), so the screens can be
   checked right away.
3. **Omron**: status, error log, watch table, Donnees. Program save / compare marked
   experimental until it runs on the CJ1M.
4. **Panasonic**: Surveillance (DT + fast counters) and program dump only.
5. Modicon later (read only, not tested on a PLC).

## To check on the bench

Each brand screen against the matching command-line output on the real PLC (same values, same
faults), then a write and a data restore with read back.
