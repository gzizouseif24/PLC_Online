/* Headless verification of the scan engine. Run with: npx tsx scripts/verify-engine.ts */
import type { CounterValue, Rung, TimerValue, Variable } from '../src/types/simulator'
import { scanProgram, type EdgeMemMap } from '../src/logic/evaluate'
import {
  elementNode,
  makeElement,
  makeVariable,
  parallelNode,
} from '../src/logic/factory'
import { createSeedState } from '../src/logic/seedProgram'

let failures = 0
function assert(cond: boolean, msg: string) {
  if (cond) {
    console.log(`  ✓ ${msg}`)
  } else {
    console.error(`  ✗ FAIL: ${msg}`)
    failures++
  }
}

const mem: EdgeMemMap = new Map()
function scan(vars: Variable[], rungs: Rung[], dt = 100) {
  const v = structuredClone(vars)
  const r = structuredClone(rungs)
  return scanProgram(v, r, dt, mem)
}
const boolOf = (vars: Variable[], name: string) =>
  vars.find((x) => x.name === name)!.value as boolean
const set = (vars: Variable[], name: string, val: boolean) => {
  vars.find((x) => x.name === name)!.value = val
}

// ---- 1. Motor seal-in ----------------------------------------------------
console.log('\n[1] Motor starter seal-in')
{
  mem.clear()
  const s = createSeedState()
  let { variables, rungs } = { variables: s.variables, rungs: s.rungs }

  // press Start
  set(variables, 'Start', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'Motor energizes when Start pressed')

  // release Start — seal-in must hold motor on
  set(variables, 'Start', false)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'Motor stays ON after Start released (seal-in)')
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'Motor still ON on subsequent scan')

  // press Stop (NC) — motor drops out
  set(variables, 'Stop', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === false, 'Motor drops out when Stop pressed')

  // release Stop — motor stays off (no Start)
  set(variables, 'Stop', false)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === false, 'Motor stays OFF after Stop released')
}

// ---- 2. TON timer --------------------------------------------------------
console.log('\n[2] TON timer (PT=300ms)')
{
  mem.clear()
  const en = makeVariable('EN', 'BOOL', false)
  const tmr = makeVariable('T1', 'TIMER', {
    IN: false, Q: false, DN: false, ET: 0, PT: 300,
  } as TimerValue)
  const rung: Rung = {
    id: 'r', number: 1,
    logic: [elementNode(makeElement('NO', en.id))],
    outputs: [makeElement('TON', tmr.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [en, tmr]
  let rungs = [rung]

  set(variables, 'EN', true)
  ;({ variables, rungs } = scan(variables, rungs, 100))
  let t = variables.find((x) => x.name === 'T1')!.value as TimerValue
  assert(t.ET === 100 && !t.DN, `ET climbs to 100, not done (ET=${t.ET})`)
  ;({ variables, rungs } = scan(variables, rungs, 100))
  ;({ variables, rungs } = scan(variables, rungs, 100))
  t = variables.find((x) => x.name === 'T1')!.value as TimerValue
  assert(t.ET === 300 && t.DN && t.Q, `ET reaches PT, DN+Q set (ET=${t.ET}, DN=${t.DN})`)

  set(variables, 'EN', false)
  ;({ variables, rungs } = scan(variables, rungs, 100))
  t = variables.find((x) => x.name === 'T1')!.value as TimerValue
  assert(t.ET === 0 && !t.DN, 'TON resets when input drops')
}

// ---- 3. CTU counter ------------------------------------------------------
console.log('\n[3] CTU counter (PV=3, rising-edge only)')
{
  mem.clear()
  const cu = makeVariable('CU', 'BOOL', false)
  const ctr = makeVariable('C1', 'COUNTER', {
    CU: false, CD: false, R: false, Q: false, ACC: 0, PV: 3,
  } as CounterValue)
  const rung: Rung = {
    id: 'r', number: 1,
    logic: [elementNode(makeElement('NO', cu.id))],
    outputs: [makeElement('CTU', ctr.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [cu, ctr]
  let rungs = [rung]
  const acc = () => (variables.find((x) => x.name === 'C1')!.value as CounterValue).ACC

  // hold CU true across two scans — should count only once (rising edge)
  set(variables, 'CU', true)
  ;({ variables, rungs } = scan(variables, rungs))
  ;({ variables, rungs } = scan(variables, rungs))
  assert(acc() === 1, `held input counts once on rising edge (ACC=${acc()})`)

  // toggle off then on twice more
  set(variables, 'CU', false)
  ;({ variables, rungs } = scan(variables, rungs))
  set(variables, 'CU', true)
  ;({ variables, rungs } = scan(variables, rungs))
  set(variables, 'CU', false)
  ;({ variables, rungs } = scan(variables, rungs))
  set(variables, 'CU', true)
  ;({ variables, rungs } = scan(variables, rungs))
  const q = (variables.find((x) => x.name === 'C1')!.value as CounterValue).Q
  assert(acc() === 3 && q, `ACC reaches PV and Q sets (ACC=${acc()}, Q=${q})`)
}

// ---- 4. Rising-edge (P) contact -----------------------------------------
console.log('\n[4] P contact one-shot')
{
  mem.clear()
  const trig = makeVariable('Trig', 'BOOL', false)
  const out = makeVariable('Pulse', 'BOOL', false)
  const rung: Rung = {
    id: 'r', number: 1,
    logic: [elementNode(makeElement('P_CONTACT', trig.id))],
    outputs: [makeElement('COIL', out.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [trig, out]
  let rungs = [rung]

  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Pulse') === false, 'no pulse while input steady low')

  set(variables, 'Trig', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Pulse') === true, 'pulse fires on 0→1 edge')
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Pulse') === false, 'pulse clears on next scan (one-shot)')
}

// ---- 5. Nested topology: A OR (B AND C) ---------------------------------
console.log('\n[5] Series-in-branch + nesting: Out = A OR (B AND C)')
{
  mem.clear()
  const a = makeVariable('A', 'BOOL', false)
  const b = makeVariable('B', 'BOOL', false)
  const c = makeVariable('C', 'BOOL', false)
  const out = makeVariable('Out', 'BOOL', false)
  const rung: Rung = {
    id: 'r', number: 1,
    logic: [
      parallelNode([
        [elementNode(makeElement('NO', a.id))],
        [elementNode(makeElement('NO', b.id)), elementNode(makeElement('NO', c.id))],
      ]),
    ],
    outputs: [makeElement('COIL', out.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [a, b, c, out]
  let rungs = [rung]

  set(variables, 'A', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'A alone energizes Out (OR leg)')

  set(variables, 'A', false)
  set(variables, 'B', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === false, 'B alone does NOT energize (series needs B AND C)')

  set(variables, 'C', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'B AND C in the branch energizes Out')
}

console.log(`\n${failures === 0 ? 'ALL PASSED' : failures + ' FAILURE(S)'}`)
process.exit(failures === 0 ? 0 : 1)
