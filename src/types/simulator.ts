export type VarType = 'BOOL' | 'INT' | 'TIMER' | 'COUNTER'

export interface TimerValue {
  IN: boolean
  Q: boolean
  DN: boolean
  ET: number // elapsed time, ms
  PT: number // preset time, ms
}

export interface CounterValue {
  CU: boolean
  CD: boolean
  R: boolean
  Q: boolean
  ACC: number
  PV: number
}

export type VarValue = number | boolean | TimerValue | CounterValue

export interface Variable {
  id: string
  name: string
  type: VarType
  value: VarValue
}

export type InstructionType =
  // contacts
  | 'NO'
  | 'NC'
  | 'P_CONTACT'
  | 'N_CONTACT'
  // coils
  | 'COIL'
  | 'COIL_NEG'
  | 'SET'
  | 'RST'
  // timers
  | 'TON'
  | 'TOF'
  | 'RTO'
  // counters
  | 'CTU'
  | 'CTD'
  | 'RES'
  // math
  | 'MOV'
  | 'ADD'
  | 'SUB'
  | 'MUL'
  | 'DIV'
  // compare
  | 'EQU'
  | 'NEQ'
  | 'GRT'
  | 'LES'
  | 'GEQ'
  | 'LEQ'

export type InstructionCategory =
  | 'Contacts'
  | 'Coils'
  | 'Timers'
  | 'Counters'
  | 'Math'
  | 'Compare'

/** Whether an instruction conditions the rung (input) or is driven by it (output). */
export type InstructionRole = 'input' | 'output'

export interface Element {
  id: string
  type: InstructionType
  /** primary variable the element operates on (contact var, coil var, timer/counter var) */
  varId: string | null
  /** extra config: math sources/dest, compare sources, etc. Strings reference var ids; numbers are immediates. */
  params: Record<string, string | number>
  /** updated each scan for rendering */
  live: boolean
}

/**
 * A rung's input-conditioning logic is a SERIES of nodes evaluated left -> right.
 * A node is either a single element, or a PARALLEL group of sub-series (OR).
 */
export type LadderNode =
  | { id: string; kind: 'element'; element: Element }
  | { id: string; kind: 'parallel'; branches: LadderNode[][] }

export interface Rung {
  id: string
  number: number
  logic: LadderNode[] // input region (contacts, compares)
  outputs: Element[] // output region (coils, timers, counters, math)
  power: boolean // updated each scan
  comment: string
}

export interface SimulatorState {
  variables: Variable[]
  rungs: Rung[]
  running: boolean
  scanCount: number
  lastCycleMs: number
  scanInterval: number // ms
}
