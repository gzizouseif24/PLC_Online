import type {
  CounterValue,
  Element,
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

export function getMem(mem: EdgeMemMap, id: string): EdgeMem {
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

export function evaluateContact(
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

export function executeOutput(
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

export { readBool, readNum }
