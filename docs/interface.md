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

## Done

- **TSX (02/11), used on a client TSX 3722:** connection (COM port list), Etat (RUN / STOP / empty
  decoded, I/O fault flag, system-bit faults, clock, cycle, Flash backup, RUN/STOP buttons, set
  the clock), Diagnostic (system bits and words; rack: configured modules from the application,
  module in place, absent / different / external fault per position), Surveillance (%MW %MD %KW
  %KD %SW %M %S, write %MW / %MD), Programme (save / compare / restore with a copy taken first,
  import .stx / .tsxbak, delete), Donnees (%MW / %KW to CSV, restore changed %MW, import PL7
  .dat, delete). Confirmed on the client PLC: diagnostic and rack, clock, data save and restore,
  program restore. Demo TSX.
- **Surveillance, modules en place (03/11):** "Lire le rack" lists the I/O modules found in the
  rack with their channels (TSX %I / %Q / %IW per position, S7 input / output bytes in binary) and
  adds them to the table in one click. Absent modules are left out.
- **TSX still missing:** live %I / %Q (`python tools/tsx.py chercher-es`: read-only probe, one
  input toggled 0 -> 1 -> 0, prints the answers that follow it and writes a log) (PL7 reads them through its debug buffer: needs a capture
  with an input toggled), changing module parameters (supply monitoring: needs two .stx that
  differ only by that tick).

- **Delta DVP (03/11), demo only so far:** connection (COM port, ASCII / RTU, speed, parity,
  station), Etat (RUN from M1000, faults from M1004 / M1008 / M1009 / M1067 with codes and program
  step, cycle, clock + set clock), Diagnostic (special M / D explained, built-in X / Y, expansion
  I/O and right-side modules from D1140..D1143 / D1320..), Surveillance (D M X Y T C S, write D M Y
  T C S), Donnees (all D to CSV, restore the changed D, import .csv). **Program save missing:** the
  WPLSoft upload uses Delta commands outside Modbus; needs one capture of WPLSoft PLC -> PC.
- **Panasonic FP0R (04/11), demo only so far:** connection (COM port, speed, parity, station; tool
  port 9600 8-O-1 by default), Etat (RUN / PROG from R9020, self-diagnostic error R9000 + DT90000
  code, operation error R9007 / R9008 + step DT90017 / DT90018, forcing R9029, scan time
  DT90022..24; RUN / PROG buttons with RMG / RMP), Diagnostic (special R / DT explained, built-in
  X / Y, raw RT reply), Surveillance (DT WX WY WR X Y R, dint format for 32-bit counters; write DT
  WY WR Y R), Donnees (all DT to CSV, 12315 or 32765 words, restore the changed DT, import .csv).
  **To confirm on the PLC:** the RT field layout (shown raw), the contact commands RCC / RCS / WCS
  / WCC and RMG / RMP (from the MEWTOCOL-COM manual, not yet sent to a real FP0R). Program save
  stays in `python tools/panasonic.py programme`.

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
