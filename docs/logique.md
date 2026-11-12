# Understanding the PLC logic

Goal: the diagnostic tool understands what the PLC program does, so it can explain the
machine to a technician without showing the ladder:

- **Why is this output off?** Check the conditions that drive it against live values and
  point to the one that blocks ("motor 1.03 off: door sensor 0.05 open").
- **Fault bits found automatically**: bits that trigger alarms, stop latches, timers used as
  timeouts. Show which condition set the fault.
- **Machine cycle**: step bits / step words, "stuck at step 4, waiting for sensor X".
- **Machine file generated** (docs/architecture.md §3) instead of typed by hand.
- Later: explanations in plain language from the logic plus live values.

## Where the logic comes from

At customer sites there is often **only the PLC**, no project file. So the logic is read
from the PLC itself: the program backup the tools already make (`tools/tsx.py sauvegarder`
→ `.tsxbak`, `tools/omron.py sauvegarder` → `.cjbak`). That is compiled code in a format the
vendors do not publish: it has to be decoded, brand by brand. It has no symbol names or
comments (they only exist in the project file), so the tool works with addresses
(`%I1.0`, `CIO 0.05`) and the labels the technician adds.

When a project export is available (PL7 `.FEF`, CX-Programmer mnemonics), it can also be
read: it gives names and comments. Both feed the same logic model.

Not done: anything around password-protected programs. For those, ask the customer.

**Priority changed (30/10):** logic starts with the S7-300, where the program format is already
decoded publicly: see [s7-300.md](s7-300.md). The TSX method below stays for later.

## Method: decode by comparison (TSX, Omron)

1. **Test programs that differ by one instruction.**
   `research/tsx/programmes_test/generer.py` writes them as `.FEF` (P00 empty program,
   P01 `%M0 := %I1.0`, P02 same with `%M1`, P03 with `%I1.1`, ... P16 self-holding).
   Same configuration as the bench TSX 3721, only the program changes.
2. **On the bench**, for each file: PL7 → new empty application → Fichier → Importer une
   application → transfer to the PLC (STOP) → `python tools/tsx.py sauvegarder Pxx_....tsxbak`.
3. **Compare**: `python research/tsx/compare_programmes.py P00_vide.tsxbak P01_....tsxbak`
   shows the bytes each instruction adds or changes. P01 vs P02 vs P03 vs P04 gives how
   addresses are coded; P06/P07/P08 the NOT / AND / OR; P10–P15 words, constants,
   arithmetic, comparison, IF.
4. **Ladder**: customer programs are mostly ladder, which PL7 may compile differently from
   ST. Needed: one small ladder program exported to `.FEF` (one rung: contact `%I1.0`, coil
   `%M0`) to learn the ladder syntax in `.FEF`, then the same test series in ladder.
5. **Decoder**: `.tsxbak` → list of rungs / statements (output ← conditions), checked against
   every test program, then against a real machine program.
6. **Diagnostics**: logic model + live reads → "why is it off", fault bits, cycle step.

Then the same for Omron (`.cjbak`, test programs written in CX-Programmer).

## Status

- [x] Program backup from the PLC: TSX (`.tsxbak`, restore too), Omron (`.cjbak`).
- [x] Test program generator (ST) and comparison script.
- [ ] Bench: import / transfer / back up P00–P16.
- [ ] Ladder `.FEF` sample.
- [ ] Decoder, logic model, diagnostics.
