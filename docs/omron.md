# Omron CJ1M (Host Link) — notes

Tool: `tools/omron.py`. Driver: `plc_online/protocols/hostlink.py`.
Wiring and PLC settings: [rapport technique §4](rapport-technique-2026-10-23.md)
(peripheral port in Host Link, 9600 7-E-2, unit 0, **DIP 4 ON**).

**Bench** = tried on the bench CJ1M. **Manual** = checked against Omron's
*CS/CJ/CP/NSJ Communications Commands Reference Manual*, **W342-E1-18** (July 2023,
[download](https://files.omron.eu/downloads/latest/manual/en/w342_cs_cj_cp_nsj_communications_commands_reference_manual_en.pdf)),
and tested against the simulator in `tests/test_omron.py`, which follows the manual's formats.
Not yet run on the PLC.

## 1. Two command sets in the same frame

```
@ 00 RD 0000 0004 FCS * CR                              C-mode: read D0..D3
@ 00 FA 0 00000000 0101 82 0000 00 0004 FCS * CR        FINS in Host Link: same read
```

- **C-mode** (2-letter header): simple, but only the old areas, 4-digit addresses (up to 9999).
- **FINS** (header `FA`, W342 §3-5): everything else. Text = response wait (1 digit) +
  ICF DA2 SA2 SID (`00` each) + FINS command + parameters. Max 1,114 characters, never split.
  Reply = `@00FA` + **`00` end code** + `40 00 00 00` (ICF DA2 SA2 SID) + command echo +
  response code (4 hex) + data, at most 538 bytes of data. **Bench, 30/10:**
  `@00FA004000000001010000000043*` (read W0). The W342 §3-5-3 diagram leaves the `00` out;
  the driver accepts both forms (the command echo tells which).
- FINS response code (W342 §5-1-3): bit 15 = network relay error, bit 7 = fatal CPU error,
  bit 6 = non-fatal CPU error. These are flags, not a failure of the command: `0040` = OK,
  but the PLC has a non-fatal error (battery...). The driver masks them.

## 2. What the tool uses

| Command | Request | Status |
|---|---|---|
| echo | `TS` | **bench** |
| read D0–D9999 | `RD` | **bench** |
| read CIO 0–6143, H 0–511, A 0–959 | `RR`, `RH`, `RJ` | **bench** |
| read W, T, C, D10000+ | FINS `0101`, areas B1, 89, 89 + 8000h, 82 | **bench** (W, T, D10000; C same code as T). C-mode cannot reach W, T/C 2048+, D10000+ (W342 §4-3-1) |
| write D, CIO, H, A | `WD`, `WR`, `WH`, `WJ` | manual. **Refused in RUN** (end code 01), OK in MONITOR and PROGRAM |
| write W, T, C, D10000+ | FINS `0102` | manual |
| mode | `SC` + `00` PROGRAM / `02` MONITOR / `03` RUN | manual |
| model | FINS `0501` + `00` (92 bytes, reply in several frames), fallback `MM` (`30` = CS/CJ) | **bench** (multi-frame reply works) |
| mode, faults, battery, error code, FAL/FALS message | FINS `0601` | **bench** |
| mode (fallback) | `MS`: status bits 9-8 = 00 PROGRAM, 10 RUN, 11 MONITOR | manual |
| clock | FINS `0701` (BCD, day of week 0 = Sunday) | **bench** |
| error log | FINS `2102`, 4 records per request, 20 stored max | **bench** (empty log only) |
| program read | FINS `0306`, program FFFF | manual + **experimental** (see §4) |

Corrections made after reading W342 (none of this had run on the PLC yet):
- `FA` reply without end code (above).
- `0601` status byte is bits: 0 = program running, 1 = writing flash, 2 = battery present,
  7 = standby. The field after the message flags is the code of the most serious error
  (e.g. 4105 = FAL 5), not a FAL number.
- `2102` returns the records with code 1104 when fewer are stored than asked; an empty log
  returns no data. Both are handled.
- `0306`: address and byte count multiples of 4, at most 528 bytes per request over Host Link.
- More end codes (21, A4, A5, A8), all fault bits of `0601`, more error log codes.

Block sizes keep every reply in one frame (131 characters max): 30 words for C-mode reads,
26 for FINS reads, 29 for C-mode writes. Longer replies (`0501`, `0306`) come in several
frames (W342 §2-2): each frame but the last ends with FCS + CR instead of FCS + `*` CR,
the PC answers with a single CR to get the next one; frames after the first are 128
characters max. The driver joins them and checks every FCS.

## 3. Memory areas (CJ1M)

From the *CJ1M CPU Units Operation Manual*, **W393-E1-14**
([download](https://files.omron.eu/downloads/latest/manual/en/w393_cj1m_g_h_cpu_units_operation_manual_en.pdf)), §2-1.

| Zone | Content | Words | Shown as |
|---|---|---|---|
| `CIO` | inputs, outputs, links, internal relays | 0–6143 | word + 16 bits |
| `W` | work relays | 0–511 | word + 16 bits |
| `H` | holding relays (kept at power off) | 0–511 (H512–H1535: function blocks only) | word + 16 bits |
| `A` | auxiliary (system) area | 0–959; **A0–A447 read-only** (the tool refuses to write there) | word + 16 bits |
| `D` | data memory (kept at power off) | 0–32767 (D20000–D29599 Special I/O units, D30000–D31599 CPU Bus units) | decimal / signed / hex |
| `T`, `C` | timer / counter current value | 0–4095 | decimal |

Where the I/O is in CIO:
- **I/O cards** (Basic I/O Units): CIO 0000–0079 by default, allocated **in rack order**, one
  or more words per card, inputs and outputs in the same sequence. There is no fixed
  "outputs at 100". Check CX-Programmer's I/O table for which word is which card.
- **Built-in I/O** (CJ1M-CPU21/22/23 only): inputs **CIO 2960** bits 00–09, outputs
  **CIO 2961** bits 00–05.
- Special I/O Units (analog...): CIO 2000–2959, 10 words per unit number.
- Internal relays free for the program: CIO 1200–1499 and CIO 3800–6143.

`surveiller CIO 0 4` prints each input / output bit that changes.

Useful A words (all read-only, readable with plain `RJ`):

| Words | Content |
|---|---|
| A100–A199 | error log: 20 records × 5 words (code, details, min/s, day/hour, year/month, BCD) |
| A300 | error log pointer (number of records) |
| A351–A354 | clock: s/min, hour/day, month/year, day of week (BCD, low byte first) |
| A400 | code of the current error |
| A401 | fatal error flags: .06 FALS, .08 cycle too long, .09 program, .10 I/O setting, .11 too many I/O, .13 duplicate unit, .14 I/O bus, .15 memory |
| A402 | non-fatal flags: .04 **battery**, .09 I/O verification, .10 PLC Setup, .12 Basic I/O unit, .13 interrupt task, .15 FAL |

**Fallback without FINS:** if the CJ1M refuses the FINS commands, `etat` reads the mode with
`MS` and the faults, error code and clock from A400–A402 and A351–A354; `erreurs` reads
A100–A199. Everything except the model and the "battery present" bit is still shown.
Order of the records in A100–A199 (newest first or oldest first): to check on the bench
against CX-Programmer's error log.

## 3b. PLC Setup, units and I/O table (`parametres`, read-only)

- **PLC Setup**: FINS `0201` area `8010`, 512 words (W342 §5-3-7). Word numbers = the
  "Programming Console address" of W393 §7-1-2. Decoded: startup mode (+81, raw), wait for
  units (+83.15), battery and interrupt-task error detection (+128.15 / .14), peripheral port
  (+144 format and mode, +145 speed, +147 unit), RS-232C port (+160, +161, +163), minimum
  cycle time (+208), watch cycle time (+209). **Self-check on the bench:** the peripheral
  port must read Host Link, 9600, 7 bits, even, 2 stop, unit 0.
- **Units**: FINS `0502` gives the model of the CPU, CPU Bus Units (address 10-1F),
  Special I/O Units (20-7F) and Inner Board (E1). Plain I/O cards (ID211, OC211) are not listed.
  **Bench:** the reply did not parse as read in W342 (not even the CPU came out); the tool now
  prints it raw, to decode.
- **Registered I/O table**: FINS `0201` area `8012`, 1280 words. Format not in the manuals.
  **Bench, rack = CPU11 + CJ1W-ID211 (16 DC inputs) + CJ1W-OC211 (16 relay outputs):**
  36 non-zero words: `+0 0402 0450`, `+746..+777` all `2020` (64 spaces: a name or comment?),
  `+988 BBBB 0003`. **Confirmed with CX-Programmer's I/O table window:** one word per slot,
  rack 0 slots 00–09 = words +0..+9 (`0000` = empty slot), `0402` = CJ1W-ID211 at CIO 0000,
  `0450` = CJ1W-OC211 at CIO 0001. CIO words are given in slot order from CIO 0000.
  Known codes are in `IO_UNIT_CODES` (`plc_online/protocols/hostlink.py`): add one line per
  new card seen (code from `parametres`, model from CX-Programmer). `etat` lists the cards
  with their CIO word and live value; an unknown card stops the CIO numbering (its size is
  unknown). Still to check: expansion racks (assumed 10 words per rack) and a changed
  first I/O word in the PLC Setup.

## 4. Program backup (experimental)

FINS `0306` reads the user program memory (compiled code, not ladder text).
Good enough to answer "has the program changed?" (`comparer`), not to read the logic.
W342 §5-3-10: allowed in RUN, MONITOR and PROGRAM; refused if the UM is read-protected or
if "prohibit transferring program from PLC" is set in the project. Address relative to the
start of the program area, multiple of 4; byte count multiple of 4; bit 15 of the count in
the reply = last address reached. The manual does not say whether an address counts bytes
or words, so the driver checks it first: it reads 64 bytes at address 0, then 16 bytes at
address 8, and sees which offset they match. Blocks are multiples of 16 bytes so the next
address is always a multiple of 4.

## 5. Bench results, 30/10/2026 (CJ1M-CPU11, unit version 04.10)

| Command | Result |
|---|---|
| `lire D 0 4` | OK (B7A9 6C55 B645 D95B: values changed since 23/10, program running) |
| `etat` | OK through FINS: model `CJ1M_CPU11` version 04.10, RUN, no fault, clock 04/02/2022 20:31:12 |
| `lire CIO 0 2`, `H 0 4`, `A 400 3` | OK (C-mode) |
| `lire W 0 4`, `T 0 4`, `D 10000 2` | OK (FINS) |
| `erreurs` | OK, log empty |
| `surveiller CIO 0 2` | ran, nothing changed (CPU11 has no built-in I/O; ID211 + OC211 added after) |
| `lire W 0 1 --trace` | reply `@00FA00 40000000 0101 0000 0000`: **`00` end code after `FA`** |
| `sauvegarder-donnees` | OK: D 32768 words in 336 s, H 512 in 4 s, W 512 in 5 s (≈ 0.3 s per 30 words) |
| `sauvegarder` (program, FINS `0306`) | OK: 19,264 bytes in 53 s |
| `monitor`, `ecrire D 1000 1234` | OK: MONITOR read back, D1000 written and read back |
| `parametres` | OK: PLC Setup, I/O table; unit list (`0502`) not decoded |
| `restaurer-donnees` | stopped by hand: it read all of D for ~5 min with no progress shown (fixed) |

Notes:
- FINS works on this CJ1M through the peripheral port, with a `00` end code after `FA`.
- **No battery** reported (status bit 2 = 0) but no battery error: the PLC Setup probably has
  battery detection off. Consistent with the clock being wrong (04/02/2022). Without a battery,
  D and H can be lost at power off.
- Windows sometimes kept COM6 busy for a moment after the previous command ("Accès refusé"):
  the tool now retries for 3 s before giving up.

## 6. Bench checklist

Close CX-Programmer, DIP 4 ON. In this order:

1. `python tools/omron.py lire D 0 4` (should match the report: F5BD 6C5D BD07 D1DD).
2. `etat`: model, mode, faults, clock. Compare with CX-Programmer.
3. `lire CIO 0 2`, `lire W 0 4`, `lire H 0 4`, `lire A 400 4`, `lire T 0 4`.
4. `surveiller CIO 0 2` and press an input.
5. `erreurs` (CX-Programmer: PLC → Error log, same list?).
6. On a test machine only: `monitor`, then `ecrire D 1000 1234`, read-back, then put it back.
7. `sauvegarder-donnees`, change one D on the test machine, `restaurer-donnees FILE`
   (MONITOR mode; only D, H, W are written, never CIO outputs or A).
8. `sauvegarder`, then `comparer FILE` (should say IDENTIQUE).

Not done: program restore (FINS `0307` program write). Only after `sauvegarder` /
`comparer` are confirmed, and ideally with a capture of CX-Programmer doing a transfer.

If a step fails, the message gives the end code or FINS code. Note it here.
