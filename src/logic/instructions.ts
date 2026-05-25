import type {
  InstructionType,
  InstructionCategory,
  InstructionRole,
  VarType,
} from '../types/simulator'

export interface InstructionMeta {
  type: InstructionType
  label: string // short name shown in palette
  symbol: string // ASCII-ish glyph for the palette tile
  category: InstructionCategory
  role: InstructionRole
  /** variable type the primary varId should have (for auto-create defaults) */
  varType: VarType | null
  description: string
}

export const INSTRUCTIONS: InstructionMeta[] = [
  // Contacts
  { type: 'NO', label: 'NO Contact', symbol: '--[ ]--', category: 'Contacts', role: 'input', varType: 'BOOL', description: 'Normally open contact' },
  { type: 'NC', label: 'NC Contact', symbol: '--[/]--', category: 'Contacts', role: 'input', varType: 'BOOL', description: 'Normally closed contact' },
  { type: 'P_CONTACT', label: 'Rising Edge', symbol: '--[P]--', category: 'Contacts', role: 'input', varType: 'BOOL', description: 'One-shot on 0→1 transition' },
  { type: 'N_CONTACT', label: 'Falling Edge', symbol: '--[N]--', category: 'Contacts', role: 'input', varType: 'BOOL', description: 'One-shot on 1→0 transition' },

  // Coils
  { type: 'COIL', label: 'Output Coil', symbol: '--( )--', category: 'Coils', role: 'output', varType: 'BOOL', description: 'Energize coil with rung power' },
  { type: 'COIL_NEG', label: 'Negated Coil', symbol: '--(/)--', category: 'Coils', role: 'output', varType: 'BOOL', description: 'Energize coil with NOT rung power' },
  { type: 'SET', label: 'Set / Latch', symbol: '--(S)--', category: 'Coils', role: 'output', varType: 'BOOL', description: 'Latch coil on (holds)' },
  { type: 'RST', label: 'Reset / Unlatch', symbol: '--(R)--', category: 'Coils', role: 'output', varType: 'BOOL', description: 'Unlatch coil off (holds)' },

  // Timers
  { type: 'TON', label: 'Timer On Delay', symbol: '[TON]', category: 'Timers', role: 'output', varType: 'TIMER', description: 'On-delay timer' },
  { type: 'TOF', label: 'Timer Off Delay', symbol: '[TOF]', category: 'Timers', role: 'output', varType: 'TIMER', description: 'Off-delay timer' },
  { type: 'RTO', label: 'Retentive Timer', symbol: '[RTO]', category: 'Timers', role: 'output', varType: 'TIMER', description: 'Retentive on-delay timer' },

  // Counters
  { type: 'CTU', label: 'Count Up', symbol: '[CTU]', category: 'Counters', role: 'output', varType: 'COUNTER', description: 'Count up on rising edge' },
  { type: 'CTD', label: 'Count Down', symbol: '[CTD]', category: 'Counters', role: 'output', varType: 'COUNTER', description: 'Count down on rising edge' },
  { type: 'RES', label: 'Reset', symbol: '[RES]', category: 'Counters', role: 'output', varType: 'COUNTER', description: 'Reset counter/timer accumulator' },

  // Math
  { type: 'MOV', label: 'Move', symbol: '[MOV]', category: 'Math', role: 'output', varType: 'INT', description: 'dest = source' },
  { type: 'ADD', label: 'Add', symbol: '[ADD]', category: 'Math', role: 'output', varType: 'INT', description: 'dest = A + B' },
  { type: 'SUB', label: 'Subtract', symbol: '[SUB]', category: 'Math', role: 'output', varType: 'INT', description: 'dest = A - B' },
  { type: 'MUL', label: 'Multiply', symbol: '[MUL]', category: 'Math', role: 'output', varType: 'INT', description: 'dest = A * B' },
  { type: 'DIV', label: 'Divide', symbol: '[DIV]', category: 'Math', role: 'output', varType: 'INT', description: 'dest = A / B' },

  // Compare
  { type: 'EQU', label: 'Equal', symbol: '[EQU]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A = B' },
  { type: 'NEQ', label: 'Not Equal', symbol: '[NEQ]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A ≠ B' },
  { type: 'GRT', label: 'Greater Than', symbol: '[GRT]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A > B' },
  { type: 'LES', label: 'Less Than', symbol: '[LES]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A < B' },
  { type: 'GEQ', label: 'Greater/Equal', symbol: '[GEQ]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A ≥ B' },
  { type: 'LEQ', label: 'Less/Equal', symbol: '[LEQ]', category: 'Compare', role: 'input', varType: null, description: 'Pass when A ≤ B' },
]

export const INSTRUCTION_MAP: Record<InstructionType, InstructionMeta> =
  INSTRUCTIONS.reduce(
    (acc, m) => {
      acc[m.type] = m
      return acc
    },
    {} as Record<InstructionType, InstructionMeta>,
  )

export const CATEGORY_ORDER: InstructionCategory[] = [
  'Contacts',
  'Coils',
  'Timers',
  'Counters',
  'Math',
  'Compare',
]

export function instructionsByCategory(): Record<InstructionCategory, InstructionMeta[]> {
  const out = {} as Record<InstructionCategory, InstructionMeta[]>
  for (const cat of CATEGORY_ORDER) out[cat] = []
  for (const m of INSTRUCTIONS) out[m.category].push(m)
  return out
}

export const isInput = (t: InstructionType) => INSTRUCTION_MAP[t].role === 'input'
export const isOutput = (t: InstructionType) => INSTRUCTION_MAP[t].role === 'output'
