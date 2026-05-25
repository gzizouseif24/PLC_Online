/* Verifies the editing model → compileRung → graph evaluator path.
   Run with: npx tsx scripts/verify-editor.ts */
import type { Rung, Variable } from '../src/types/simulator'
import { compileRung } from '../src/logic/compileRung'
import { scanGraphProgram, type EdgeMemMap } from '../src/logic/evaluateGraph'
import { makeElement, makeVariable } from '../src/logic/factory'
import { createSeedState } from '../src/logic/seedProgram'

let failures = 0
const assert = (c: boolean, m: string) => {
  console.log(`  ${c ? '✓' : '✗ FAIL:'} ${m}`)
  if (!c) failures++
}
const mem: EdgeMemMap = new Map()
function scan(vars: Variable[], rungs: Rung[], dt = 100) {
  const v = structuredClone(vars)
  const r = structuredClone(rungs)
  const graphs = r.map(compileRung)
  scanGraphProgram(v, graphs, dt, mem)
  r.forEach((er, i) => (er.power = graphs[i].power))
  return { variables: v, rungs: r }
}
const set = (vars: Variable[], n: string, val: boolean) => {
  vars.find((x) => x.name === n)!.value = val
}
const boolOf = (vars: Variable[], n: string) => vars.find((x) => x.name === n)!.value as boolean

// ---- 1. Seed seal-in via the editing model ------------------------------
console.log('\n[1] Seed motor seal-in (editing model → graph)')
{
  mem.clear()
  const s = createSeedState()
  let { variables, rungs } = { variables: s.variables, rungs: s.rungs }
  set(variables, 'Start', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'Start energizes Motor')
  set(variables, 'Start', false)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === true, 'seal-in holds')
  set(variables, 'Stop', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Motor') === false, 'Stop drops Motor')
}

// ---- 2. Open branch carries NO power until closed ------------------------
console.log('\n[2] Open vs closed branch')
{
  mem.clear()
  const a = makeVariable('A', 'BOOL', false)
  const b = makeVariable('B', 'BOOL', false)
  const out = makeVariable('Out', 'BOOL', false)
  // main: n0 -A- n1 ; output at n1. branch from n0 holding B, OPEN.
  const rung: Rung = {
    id: 'r', number: 1,
    mainNodes: ['n0', 'n1'],
    main: [makeElement('NO', a.id)],
    branches: [{ id: 'b1', startNodeId: 'n0', contacts: [makeElement('NO', b.id)], closeNodeId: null, live: false }],
    outputs: [makeElement('COIL', out.id)],
    power: false, comment: '',
  }
  let variables: Variable[] = [a, b, out]
  let rungs = [rung]

  set(variables, 'A', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'main path A conducts')

  set(variables, 'A', false); set(variables, 'B', true)
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === false, 'OPEN branch B does NOT energize output (no auto-close)')

  // user closes the branch onto n1
  rungs = rungs.map((r) => ({
    ...r,
    branches: r.branches.map((br) => ({ ...br, closeNodeId: 'n1' })),
  }))
  ;({ variables, rungs } = scan(variables, rungs))
  assert(boolOf(variables, 'Out') === true, 'after closing onto n1, B energizes output (parallel with A)')
}

console.log(`\n${failures === 0 ? 'ALL PASSED' : failures + ' FAILURE(S)'}`)
process.exit(failures === 0 ? 0 : 1)
