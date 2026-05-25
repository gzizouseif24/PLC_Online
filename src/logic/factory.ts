import type {
  CounterValue,
  Element,
  InstructionType,
  LadderNode,
  TimerValue,
  Variable,
  VarType,
  VarValue,
} from '../types/simulator'
import { INSTRUCTION_MAP } from './instructions'

export const uid = (): string =>
  typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2) + Date.now().toString(36)

export function defaultValue(type: VarType): VarValue {
  switch (type) {
    case 'BOOL':
      return false
    case 'INT':
      return 0
    case 'TIMER':
      return { IN: false, Q: false, DN: false, ET: 0, PT: 1000 } as TimerValue
    case 'COUNTER':
      return { CU: false, CD: false, R: false, Q: false, ACC: 0, PV: 5 } as CounterValue
  }
}

export function makeVariable(
  name: string,
  type: VarType,
  value?: VarValue,
  id?: string,
): Variable {
  return { id: id ?? uid(), name, type, value: value ?? defaultValue(type) }
}

export function makeElement(
  type: InstructionType,
  varId: string | null = null,
  params: Record<string, string | number> = {},
  id?: string,
): Element {
  return { id: id ?? uid(), type, varId, params, live: false }
}

export function elementNode(element: Element, id?: string): LadderNode {
  return { id: id ?? uid(), kind: 'element', element }
}

export function parallelNode(branches: LadderNode[][], id?: string): LadderNode {
  return { id: id ?? uid(), kind: 'parallel', branches }
}

export function varTypeForInstruction(type: InstructionType): VarType | null {
  return INSTRUCTION_MAP[type].varType
}
