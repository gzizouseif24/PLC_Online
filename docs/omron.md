# Omron CJ1M (Host Link) — notes

Tool: `scripts/omron/omron.py`. Driver: `plc_online/drivers/omron.py`.
Wiring and PLC settings: [rapport technique §4](rapport-technique-2026-10-23.md)
(peripheral port in Host Link, 9600 7-E-2, unit 0, **DIP 4 ON**).

**Confirmed** = tried on the bench CJ1M. **To verify** = from the Omron manuals
(W342 *Communications Commands*, W227 *FINS*), tested only against the simulator in
`tests/test_omron.py`.

## 1. Two command sets in the same frame

```
@ 00 RD 0000 0004 FCS * CR                              C-mode: read D0..D3
@ 00 FA 0 00000000 0101 82 0000 00 0004 FCS * CR        FINS in Host Link: same read
```

- **C-mode** (2-letter header): simple, but only the old areas, 4-digit addresses (up to 9999).
- **FINS** (header `FA`): everything else. Text = response wait (1 digit) + ICF DA2 SA2 SID
  (4 bytes, all `00`) + FINS command + parameters.
  Reply = `@00FA` + end code `00` + ICF DA2 SA2 SID + command echo + response code (4 hex) + data.
- FINS response code: bit 7 of each byte is a flag (relay error, CPU fatal / non-fatal
  error), not a failure. Example: `0040` = OK, but the PLC has a non-fatal error (battery...).
  The driver masks those bits.

## 2. What the tool uses

| Command | Request | Status |
|---|---|---|
| echo | `TS` | **confirmed** |
| read D0–D9999 | `RD` | **confirmed** |
| read CIO, H, A | `RR`, `RH`, `RJ` | to verify (same format as `RD`) |
| read W, T, C, D10000+ | FINS `0101` (area B1, 89, 89+8000h, 82) | to verify |
| write D, CIO, H, A | `WD`, `WR`, `WH`, `WJ` | to verify. **Refused in RUN** (end code 01): use MONITOR |
| write W, T, C, D10000+ | FINS `0102` | to verify |
| mode | `SC` + `00` PROGRAM / `02` MONITOR / `03` RUN | to verify |
| model | FINS `0501` (reply in several frames), fallback `MM` | to verify |
| mode, faults, FAL/FALS, message | FINS `0601` | to verify |
| clock | FINS `0701` (BCD) | to verify |
| error log | FINS `2102`, 4 records per request | to verify |
| program read | FINS `0306`, program FFFF | **experimental** (see §4) |

Block sizes keep every reply in one frame (131 characters max): 30 words for C-mode reads,
26 for FINS reads, 29 for C-mode writes. Longer replies (`0501`, `0306`) come in several
frames: each intermediate frame ends with FCS + CR (no `*`), the PC sends a CR to get the
next one. The driver joins them and checks every FCS.

## 3. Memory areas (CJ1M)

| Zone | Content | Words | Shown as |
|---|---|---|---|
| `CIO` | inputs, outputs, internal relays | 0–6143 | word + 16 bits |
| `W` | work relays | 0–511 | word + 16 bits |
| `H` | holding relays (kept at power off) | 0–511 | word + 16 bits |
| `A` | auxiliary (system) relays | 0–959 | word + 16 bits |
| `D` | data memory | 0–32767 | decimal / signed / hex |
| `T`, `C` | timer / counter current value | 0–4095 | decimal |

CJ1M inputs usually start at CIO 0, outputs at CIO 100 (built-in I/O of the CPU21/22/23:
inputs CIO 2960–2961, outputs CIO 2964). `surveiller CIO 0 4` prints each input bit that changes.

## 4. Program backup (experimental)

FINS `0306` reads the user program memory (compiled code, not ladder text).
Good enough to answer "has the program changed?" (`comparer`), not to read the logic.
The manual is unclear whether the address counts bytes or words, so the driver checks it
first: it reads 64 bytes at address 0, then 16 bytes at address 8, and sees which offset
they match. If the PLC refuses (`2201`/`2202`: mode, `3001`: access right), try in PROGRAM
mode and note the code here.

## 5. Bench checklist

Close CX-Programmer, DIP 4 ON. In this order:

1. `python scripts/omron/omron.py lire D 0 4` (should match the report: F5BD 6C5D BD07 D1DD).
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
