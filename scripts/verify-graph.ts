/* Headless verification of the graph (power-flow) evaluator.
   Run with: npx tsx scripts/verify-graph.ts */
import type { Variable } from '../src/types/simulator'
import type { GEdge, GraphRung } from '../src/types/graph'
import { scanGraphProgram } from '../src/logic/evaluateGraph'
import { type EdgeMemMap } from '../src/logic/evaluate'
import { makeElement, makeVariable } from '../src/logic/factory'

let failures = 0
function assert(cond: boolean, msg: string) {
  console.log(`  ${cond ? '✓' : '✗ FAIL:'} ${msg}`)
  if (!cond) failures++
}

const mem: EdgeMemMap = new Map()
function scan(vars: Variable[], rungs: GraphRung[], dt = 100) {
  return scanGraphProgram(structuredClone(vars), structuredClone(rungs), dt, mem)
}
const set = (vars: Variable[], name: string, val: boolean) => {
  vars.find((x) => x.name === name)!.value = val
}
const boolOf = (vars: Variable[], name: string) =>
  vars.find((x) => x.name === name)!.value as boolean

const contact = (id: string, type: GEdge['type'], varId: string, from: string, to: string): GEdge => ({
  id, kind: 'contact', type, varId, from, to, live: false,
})
const link = (id: string, from: string, to: string): GEdge => ({ id, kind: 'link', from, to, live: false })

// ---- 1. Bridge / crossing: the picture's network -------------------------
// L--TagIn_1--A, L--TagIn_3--B, A--TagIn_2--R, B--TagIn_4--R, plus bridge A--B.
// Coil reachable iff (1&2) | (3&4) | (1&4) | (2&3)  [NO contacts, link always conducts]
console.log('\n[1] Bridge topology (crossing) — Out = (1&2)|(3&4)|(1&4)|(2&3)')
{
  mem.clear()
  const t1 = makeVariable('T1', 'BOOL', false)
  const t2 = makeVariable('T2', 'BOOL', false)
  const t3 = makeVariable('T3', 'BOOL', false)
  const t4 = makeVariable('T4', 'BOOL', false)
  const out = makeVariable('Out', 'BOOL', false)
  const rung: GraphRung = {
    id: 'r', number: 1, leftId: 'L', rightId: 'R',
    nodes: [
      { id: 'L', x: 0, y: 0 }, { id: 'A', x: 1, y: 0 },
      { id: 'B', x: 1, y: 1 }, { id: 'R', x: 2, y: 0 },
    ],
    edges: [
      contact('e1', 'NO', t1.id, 'L', 'A'),
      contact('e3', 'NO', t3.id, 'L', 'B'),
      contact('e2', 'NO', t2.id, 'A', 'R'),
      contact('e4', 'NO', t4.id, 'B', 'R'),
      link('bridge', 'A', 'B'),
    ],
    outputs: [makeElement('COIL', out.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [t1, t2, t3, t4, out]
  let rungs = [rung]

  set(variables, 'T1', true); set(variables, 'T2', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'normal path T1&T2 conducts')

  set(variables, 'T2', false); set(variables, 'T4', true) // T1 & T4 only
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'cross path T1&T4 conducts THROUGH the bridge')

  set(variables, 'T1', false); set(variables, 'T3', true); set(variables, 'T4', false); set(variables, 'T2', true) // T3 & T2
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'cross path T3&T2 conducts THROUGH the bridge')

  set(variables, 'T2', false) // only T3 now
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === false, 'T3 alone does not conduct')
}

// ---- 2. Same network WITHOUT the bridge proves the crossing matters ------
console.log('\n[2] Without the bridge, T1&T4 must NOT conduct (pure series-parallel)')
{
  mem.clear()
  const t1 = makeVariable('T1', 'BOOL', true)
  const t2 = makeVariable('T2', 'BOOL', false)
  const t3 = makeVariable('T3', 'BOOL', false)
  const t4 = makeVariable('T4', 'BOOL', true)
  const out = makeVariable('Out', 'BOOL', false)
  const rung: GraphRung = {
    id: 'r', number: 1, leftId: 'L', rightId: 'R',
    nodes: [{ id: 'L', x: 0, y: 0 }, { id: 'A', x: 1, y: 0 }, { id: 'B', x: 1, y: 1 }, { id: 'R', x: 2, y: 0 }],
    edges: [
      contact('e1', 'NO', t1.id, 'L', 'A'),
      contact('e3', 'NO', t3.id, 'L', 'B'),
      contact('e2', 'NO', t2.id, 'A', 'R'),
      contact('e4', 'NO', t4.id, 'B', 'R'),
      // no bridge
    ],
    outputs: [makeElement('COIL', out.id)],
    power: false, comment: '',
  }
  const { variables } = scan([t1, t2, t3, t4, out], [rung])
  assert(boolOf(variables, 'Out') === false, 'T1&T4 with no bridge stays OFF (series-parallel only)')
}

// ---- 3. Motor seal-in on the graph model ---------------------------------
console.log('\n[3] Motor seal-in (graph form)')
{
  mem.clear()
  const start = makeVariable('Start', 'BOOL', false)
  const stop = makeVariable('Stop', 'BOOL', false)
  const motor = makeVariable('Motor', 'BOOL', false)
  const rung: GraphRung = {
    id: 'r', number: 1, leftId: 'L', rightId: 'R',
    nodes: [{ id: 'L', x: 0, y: 0 }, { id: 'A', x: 1, y: 0 }, { id: 'R', x: 2, y: 0 }],
    edges: [
      contact('start', 'NO', start.id, 'L', 'A'),
      contact('seal', 'NO', motor.id, 'L', 'A'), // parallel seal
      contact('stop', 'NC', stop.id, 'A', 'R'),
    ],
    outputs: [makeElement('COIL', motor.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [start, stop, motor]
  let rungs = [rung]

  set(variables, 'Start', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'Start energizes Motor')
  set(variables, 'Start', false)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'seal-in holds after Start released')
  set(variables, 'Stop', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === false, 'Stop drops Motor')
}

console.log(`\n${failures === 0 ? 'ALL PASSED' : failures + ' FAILURE(S)'}`)
process.exit(failures === 0 ? 0 : 1)
