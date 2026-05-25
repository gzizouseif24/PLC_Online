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
/**
 * A parallel branch leg. It starts at a main-line node (`startNodeId`) and runs
 * left→right through its own series of contacts. It is OPEN until the user
 * closes it onto a main-line node (`closeNodeId`); an open branch carries no
 * power back to the rung and does not auto-rejoin (shown with an arrow handle).
 */
export interface Branch {
  id: string
  startNodeId: string
  contacts: Element[]
  closeNodeId: string | null // null = open
  live: boolean
}

/**
 * Editing model for one rung. The main line is `main.length` contacts wired
 * through `mainNodes` (length main.length + 1): mainNodes[0] is the left rail,
 * the last is the right side that feeds the outputs. main[i] connects
 * mainNodes[i] → mainNodes[i+1]. Node ids are stable so branch references
 * survive insertions/deletions on the main line.
 */
export interface Rung {
  id: string
  number: number
  mainNodes: string[]
  main: Element[]
  branches: Branch[]
  outputs: Element[] // coils/timers/counters/math, driven by left↔right connectivity
  power: boolean
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
