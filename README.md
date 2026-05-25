# LadderSim

A local, browser-based **PLC ladder logic simulator** — built to look and feel
like professional industrial software (TIA Portal / Studio 5000 dark mode), not
a toy. Build ladder rungs, drive them with a live scan cycle, and watch power
flow energize contacts and coils in real time. Everything runs in the browser;
no backend, no database, all state in memory.

This is **Layer 1 (the engine-first foundation)**. The scan engine, instruction
set, and data model are complete and verified. Rungs are built via the
instruction palette and a configuration popover (drag-and-drop is planned — see
roadmap).

## Run it

```bash
npm install
npm run dev
```

Then open the printed local URL (default http://localhost:5173). A **motor
starter with seal-in** is preloaded on first launch as a working example.

Other scripts:

```bash
npm run build   # type-check + production build
npm run lint    # eslint
npx tsx scripts/verify-engine.ts   # headless engine self-tests (seal-in, timer, counter, edge)
```

## How to use

1. Press **Run** (top toolbar) to start the scan cycle.
2. Toggle a **BOOL variable** in the right-hand Variable panel — e.g. flip
   `Start` and watch `Motor` energize and *stay* energized via the seal-in
   branch. Flip `Stop` to drop it out.
3. **Add instructions:** select a rung (click it), then click an instruction in
   the left palette. A config popover lets you pick/create the variable and set
   parameters (preset time, preset value, math sources, etc.).
4. **Parallel (OR) branches:** select a contact in a rung, click **OR branch**,
   then pick the instruction to place on the new parallel path. This is how the
   seal-in is modeled — a branch wrapping a *subset* of the rung.
5. **Edit / delete:** right-click any element for *Edit properties* / *Delete*.

### Instruction set

- **Contacts:** NO `[ ]`, NC `[/]`, rising-edge `[P]`, falling-edge `[N]`
- **Coils:** output `( )`, negated `(/)`, set/latch `(S)`, reset/unlatch `(R)`
- **Timers:** `TON`, `TOF`, `RTO` (real elapsed-ms accumulation, ET/PT progress bar)
- **Counters:** `CTU`, `CTD`, `RES` (rising-edge counting)
- **Math:** `MOV`, `ADD`, `SUB`, `MUL`, `DIV` (sources may be variables or immediates)
- **Compare:** `EQU`, `NEQ`, `GRT`, `LES`, `GEQ`, `LEQ`

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Delete` | Delete the selected element; if no element is selected, delete the selected rung |
| `Enter` | Apply the open configuration popover |
| `Escape` | Close the open configuration popover |

## Architecture

```
src/
  components/   UI: Toolbar, InstructionPalette, LadderCanvas, Rung,
                LadderNodeView, Element, ElementSymbol (SVG), ElementConfigPopover,
                VariablePanel, StatusBar
  hooks/        useSimulator (useReducer state), useScanCycle (setInterval engine)
  logic/        evaluate (rung/element evaluation), instructions (metadata),
                factory (element/variable builders), seedProgram (motor example)
  types/        all TypeScript types
```

The rung logic uses a small recursive node model so a parallel branch can wrap a
*subset* of a rung (a true seal-in), rather than ORing around the whole rung:

```ts
type LadderNode =
  | { id: string; kind: 'element'; element: Element }
  | { id: string; kind: 'parallel'; branches: LadderNode[][] }
```

Edge-triggered behavior (P/N contacts, counter edges) and timer elapsed-time use
per-element memory kept outside the serializable state, primed at Run so a
steady input does not produce a spurious one-shot on the first scan.

## Roadmap — Layer 2 (AI copilot)

Planned for a future layer:

- Drag-and-drop rung building and reordering (`@dnd-kit`)
- An AI copilot that can author and explain ladder logic from natural language
- Animated power-flow transitions and richer IEC symbol styling
- Save / load and export

## What this is not

Not a hardware/PLC communication tool, not a code generator for RSLogix or TIA
Portal, and not a backend application — it is an in-browser logic simulator.
