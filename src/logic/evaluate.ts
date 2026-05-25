import type {
  CounterValue,
  Element,
  LadderNode,
  Rung,
  TimerValue,
  Variable,
  VarValue,
} from '../types/simulator'

/** Per-element runtime memory that must NOT live in serializable state. */
export interface EdgeMem {
  prevVar?: boolean // previous bool value of the element's variable (P/N contacts)
  prevPower?: boolean // previous incoming power (counter rising-edge detection)
}

export type EdgeMemMap = Map<string, EdgeMem>

function getMem(mem: EdgeMemMap, id: string): EdgeMem {
  let m = mem.get(id)
  if (!m) {
    m = {}
    mem.set(id, m)
  }
  return m
}

// ---- value helpers -------------------------------------------------------

const isTimer = (v: VarValue): v is TimerValue =>
  typeof v === 'object' && v !== null && 'ET' in v && 'PT' in v
const isCounter = (v: VarValue): v is CounterValue =>
  typeof v === 'object' && v !== null && 'ACC' in v && 'PV' in v

/** Boolean reading of a variable. TIMER→DN, COUNTER→Q, BOOL→value, INT→value≠0. */
function readBool(v: Variable | undefined): boolean {
  if (!v) return false
  const val = v.value
  if (typeof val === 'boolean') return val
  if (typeof val === 'number') return val !== 0
  if (isTimer(val)) return val.DN
  if (isCounter(val)) return val.Q
  return false
}

/** Numeric reading of a variable. TIMER→ET, COUNTER→ACC, BOOL→0/1, INT→value. */
function readNum(v: Variable | undefined): number {
  if (!v) return 0
  const val = v.value
  if (typeof val === 'number') return val
  if (typeof val === 'boolean') return val ? 1 : 0
  if (isTimer(val)) return val.ET
  if (isCounter(val)) return val.ACC
  return 0
}

/** A source param is either an immediate number or a variable-id string. */
function resolveSource(
  raw: string | number | undefined,
  varsById: Map<string, Variable>,
): number {
  if (raw === undefined || raw === '') return 0
  if (typeof raw === 'number') return raw
  const asNum = Number(raw)
  if (varsById.has(raw)) return readNum(varsById.get(raw))
  // string that isn't a known var id but parses as a number → treat as immediate
  return Number.isFinite(asNum) ? asNum : 0
}

// ---- input evaluation (contacts + compares) ------------------------------

function evaluateContact(
  el: Element,
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
): boolean {
  const v = el.varId ? varsById.get(el.varId) : undefined
  const cur = readBool(v)

  switch (el.type) {
    case 'NO':
      return cur
    case 'NC':
      return !cur
    case 'P_CONTACT': {
      const m = getMem(mem, el.id)
      return cur && !m.prevVar
    }
    case 'N_CONTACT': {
      const m = getMem(mem, el.id)
      return !cur && m.prevVar === true
    }
    // compares
    case 'EQU':
    case 'NEQ':
    case 'GRT':
    case 'LES':
    case 'GEQ':
    case 'LEQ': {
      const a = resolveSource(el.params.srcA, varsById)
      const b = resolveSource(el.params.srcB, varsById)
      switch (el.type) {
        case 'EQU':
          return a === b
        case 'NEQ':
          return a !== b
        case 'GRT':
          return a > b
        case 'LES':
          return a < b
        case 'GEQ':
          return a >= b
        case 'LEQ':
          return a <= b
      }
    }
  }
  return false
}

/** Evaluate a series of nodes; result is incomingPower AND-chained through each. */
function evaluateSeries(
  nodes: LadderNode[],
  incoming: boolean,
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
): boolean {
  let power = incoming
  for (const node of nodes) {
    power = evaluateNode(node, power, varsById, mem)
    if (!power) {
      // continue evaluating so `live` flags update, but power stays false
    }
  }
  return power
}

export function evaluateNode(
  node: LadderNode,
  incoming: boolean,
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
): boolean {
  if (node.kind === 'element') {
    const passes = evaluateContact(node.element, varsById, mem)
    const out = incoming && passes
    node.element.live = out
    return out
  }
  // parallel: OR of each branch, each branch is a series fed by `incoming`
  let any = false
  for (const branch of node.branches) {
    const branchPower = evaluateSeries(branch, incoming, varsById, mem)
    if (branchPower) any = true
  }
  return any
}

// ---- output execution ----------------------------------------------------

function setVarValue(v: Variable | undefined, value: VarValue) {
  if (v) v.value = value
}

function execTimer(el: Element, power: boolean, v: Variable, dtMs: number) {
  const t = { ...(v.value as TimerValue) }
  const PT = Number(el.params.PT ?? t.PT ?? 0)
  t.PT = PT
  t.IN = power

  switch (el.type) {
    case 'TON': {
      if (power) {
        t.ET = Math.min(PT, t.ET + dtMs)
      } else {
        t.ET = 0
      }
      t.DN = t.ET >= PT && PT > 0
      t.Q = t.DN
      break
    }
    case 'TOF': {
      if (power) {
        t.ET = 0
        t.Q = true
        t.DN = false
      } else {
        t.ET = Math.min(PT, t.ET + dtMs)
        if (t.ET >= PT) {
          t.Q = false
          t.DN = true
        }
      }
      break
    }
    case 'RTO': {
      if (power) t.ET = Math.min(PT, t.ET + dtMs) // retains ET when power drops
      t.DN = t.ET >= PT && PT > 0
      t.Q = t.DN
      break
    }
  }
  v.value = t
}

function execCounter(
  el: Element,
  power: boolean,
  v: Variable,
  mem: EdgeMemMap,
) {
  const c = { ...(v.value as CounterValue) }
  const PV = Number(el.params.PV ?? c.PV ?? 0)
  c.PV = PV
  const m = getMem(mem, el.id)
  const rising = power && !m.prevPower

  if (el.type === 'CTU') {
    if (rising) c.ACC += 1
    c.Q = c.ACC >= PV
  } else if (el.type === 'CTD') {
    if (rising) c.ACC -= 1
    c.Q = c.ACC <= 0
  }
  v.value = c
}

function execReset(v: Variable) {
  if (isTimer(v.value)) {
    v.value = { ...v.value, ET: 0, Q: false, DN: false, IN: false }
  } else if (isCounter(v.value)) {
    v.value = { ...v.value, ACC: 0, Q: false }
  } else if (typeof v.value === 'number') {
    v.value = 0
  } else {
    v.value = false
  }
}

function execMath(el: Element, varsById: Map<string, Variable>) {
  const dest = el.varId ? varsById.get(el.varId) : undefined
  if (!dest) return
  const a = resolveSource(el.params.srcA, varsById)
  const b = resolveSource(el.params.srcB, varsById)
  let result = 0
  switch (el.type) {
    case 'MOV':
      result = resolveSource(el.params.src ?? el.params.srcA, varsById)
      break
    case 'ADD':
      result = a + b
      break
    case 'SUB':
      result = a - b
      break
    case 'MUL':
      result = a * b
      break
    case 'DIV':
      result = b === 0 ? 0 : a / b
      break
  }
  setVarValue(dest, Math.round(result))
}

function executeOutput(
  el: Element,
  power: boolean,
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
  dtMs: number,
) {
  const v = el.varId ? varsById.get(el.varId) : undefined
  el.live = power

  switch (el.type) {
    case 'COIL':
      setVarValue(v, power)
      break
    case 'COIL_NEG':
      setVarValue(v, !power)
      el.live = !power
      break
    case 'SET':
      if (power) setVarValue(v, true)
      el.live = readBool(v)
      break
    case 'RST':
      if (power) setVarValue(v, false)
      el.live = !readBool(v)
      break
    case 'TON':
    case 'TOF':
    case 'RTO':
      if (v) execTimer(el, power, v, dtMs)
      el.live = readBool(v)
      break
    case 'CTU':
    case 'CTD':
      if (v) execCounter(el, power, v, mem)
      el.live = readBool(v)
      break
    case 'RES':
      if (power && v) execReset(v)
      break
    case 'MOV':
    case 'ADD':
    case 'SUB':
    case 'MUL':
    case 'DIV':
      if (power) execMath(el, varsById)
      break
  }
}

// ---- whole-program scan --------------------------------------------------

/**
 * Evaluate every rung against a working copy of variables, mutating in place.
 * Returns the new variables + rungs (already cloned by the caller).
 * Edge memory (prev values) is updated for the NEXT scan after evaluation.
 */
export function scanProgram(
  variables: Variable[],
  rungs: Rung[],
  dtMs: number,
  mem: EdgeMemMap,
): { variables: Variable[]; rungs: Rung[] } {
  const varsById = new Map<string, Variable>()
  for (const v of variables) varsById.set(v.id, v)

  // snapshot per-variable bool BEFORE evaluation, for P/N edge memory update
  const preBool = new Map<string, boolean>()
  for (const v of variables) preBool.set(v.id, readBool(v))

  // capture incoming power per output element for counter edges (set during exec)
  const powerForElement = new Map<string, boolean>()

  for (const rung of rungs) {
    const power = evaluateSeries(rung.logic, true, varsById, mem)
    rung.power = power
    for (const out of rung.outputs) {
      powerForElement.set(out.id, power)
      executeOutput(out, power, varsById, mem, dtMs)
    }
  }

  // update edge memory for next scan
  for (const rung of rungs) {
    updateNodeEdgeMem(rung.logic, varsById, mem)
    for (const out of rung.outputs) {
      const m = getMem(mem, out.id)
      m.prevPower = powerForElement.get(out.id) ?? false
    }
  }

  return { variables, rungs }
}

function updateNodeEdgeMem(
  nodes: LadderNode[],
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
) {
  for (const node of nodes) {
    if (node.kind === 'element') {
      if (node.element.type === 'P_CONTACT' || node.element.type === 'N_CONTACT') {
        const m = getMem(mem, node.element.id)
        m.prevVar = readBool(node.element.varId ? varsById.get(node.element.varId) : undefined)
      }
    } else {
      for (const branch of node.branches) updateNodeEdgeMem(branch, varsById, mem)
    }
  }
}

/**
 * Seed edge memory from the current state at Run, so an input that is already
 * true when the simulation starts does NOT produce a spurious one-shot / count
 * on the first scan. Operates on clones — does not mutate live state.
 */
export function primeEdgeMem(variables: Variable[], rungs: Rung[], mem: EdgeMemMap) {
  mem.clear()
  const v = structuredClone(variables)
  const r = structuredClone(rungs)
  const varsById = new Map<string, Variable>()
  for (const variable of v) varsById.set(variable.id, variable)

  // prevVar = current value, so steady-state inputs show no edge
  for (const rung of r) updateNodeEdgeMem(rung.logic, varsById, mem)
  // prevPower = steady-state rung power
  for (const rung of r) {
    const power = evaluateSeries(rung.logic, true, varsById, mem)
    for (const out of rung.outputs) getMem(mem, out.id).prevPower = power
  }
}

export { readBool, readNum }
