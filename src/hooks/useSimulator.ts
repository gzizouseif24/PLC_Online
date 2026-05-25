import { useReducer } from 'react'
import type {
  Branch,
  Element,
  Rung,
  SimulatorState,
  Variable,
  VarValue,
} from '../types/simulator'
import { isInput } from '../logic/instructions'
import { uid } from '../logic/factory'
import { createSeedState } from '../logic/seedProgram'

export type Action =
  | { type: 'SET_RUNNING'; running: boolean }
  | { type: 'SET_INTERVAL'; interval: number }
  | { type: 'ADD_RUNG' }
  | { type: 'DELETE_RUNG'; rungId: string }
  | {
      type: 'ADD_INSTRUCTION'
      rungId: string
      element: Element
      afterElementId?: string | null
      atMainPos?: number
    }
  | {
      type: 'ADD_BRANCH'
      rungId: string
      startNodeId: string
      element: Element
      closeNodeId?: string | null
    }
  | { type: 'CLOSE_BRANCH'; rungId: string; branchId: string; closeNodeId: string | null }
  | { type: 'DELETE_ELEMENT'; elementId: string }
  | { type: 'DELETE_BRANCH'; rungId: string; branchId: string }
  | {
      type: 'CONFIGURE_ELEMENT'
      elementId: string
      varId: string | null
      params: Record<string, string | number>
    }
  | { type: 'ADD_VARIABLE'; variable: Variable }
  | { type: 'RENAME_VARIABLE'; id: string; name: string }
  | { type: 'SET_VARIABLE_VALUE'; id: string; value: VarValue }
  | { type: 'DELETE_VARIABLE'; id: string }
  | { type: 'TICK'; variables: Variable[]; rungs: Rung[]; cycleMs: number }
  | { type: 'RESET_SCAN' }

// ---- helpers -------------------------------------------------------------

function mapRungElements(rung: Rung, fn: (el: Element) => Element): Rung {
  return {
    ...rung,
    main: rung.main.map(fn),
    branches: rung.branches.map((b) => ({ ...b, contacts: b.contacts.map(fn) })),
    outputs: rung.outputs.map(fn),
  }
}

function mainIndexOf(rung: Rung, elId: string): number {
  return rung.main.findIndex((c) => c.id === elId)
}

function branchOf(rung: Rung, elId: string): { branch: Branch; index: number } | null {
  for (const b of rung.branches) {
    const i = b.contacts.findIndex((c) => c.id === elId)
    if (i !== -1) return { branch: b, index: i }
  }
  return null
}

/** Insert a contact into the main line at contact position `pos` (adds one node). */
function insertMainContact(rung: Rung, pos: number, el: Element): Rung {
  const main = [...rung.main]
  main.splice(pos, 0, el)
  const mainNodes = [...rung.mainNodes]
  mainNodes.splice(pos + 1, 0, uid())
  return { ...rung, main, mainNodes }
}

function removeMainContact(rung: Rung, i: number): Rung {
  const removedNode = rung.mainNodes[i + 1]
  const main = rung.main.filter((_, idx) => idx !== i)
  const mainNodes = rung.mainNodes.filter((_, idx) => idx !== i + 1)
  if (mainNodes.length < 2) mainNodes.push(uid()) // keep left rail != right rail
  const survivor = mainNodes[Math.min(i, mainNodes.length - 1)]
  const branches = rung.branches.map((b) => ({
    ...b,
    startNodeId: b.startNodeId === removedNode ? survivor : b.startNodeId,
    closeNodeId: b.closeNodeId === removedNode ? survivor : b.closeNodeId,
  }))
  return { ...rung, main, mainNodes, branches }
}

function nextRungNumber(rungs: Rung[]): number {
  return rungs.reduce((max, r) => Math.max(max, r.number), 0) + 1
}

// ---- reducer -------------------------------------------------------------

function reducer(state: SimulatorState, action: Action): SimulatorState {
  switch (action.type) {
    case 'SET_RUNNING':
      return { ...state, running: action.running }

    case 'SET_INTERVAL':
      return { ...state, scanInterval: action.interval }

    case 'ADD_RUNG': {
      const rung: Rung = {
        id: uid(),
        number: nextRungNumber(state.rungs),
        mainNodes: [uid(), uid()],
        main: [],
        branches: [],
        outputs: [],
        power: false,
        comment: '',
      }
      return { ...state, rungs: [...state.rungs, rung] }
    }

    case 'DELETE_RUNG': {
      const rungs = state.rungs
        .filter((r) => r.id !== action.rungId)
        .map((r, i) => ({ ...r, number: i + 1 }))
      return { ...state, rungs }
    }

    case 'ADD_INSTRUCTION': {
      const el = action.element
      const after = action.afterElementId
      const rungs = state.rungs.map((r) => {
        if (r.id !== action.rungId) return r
        if (!isInput(el.type)) return { ...r, outputs: [...r.outputs, el] }

        if (action.atMainPos != null) {
          return insertMainContact(r, Math.max(0, Math.min(action.atMainPos, r.main.length)), el)
        }

        if (after) {
          const mi = mainIndexOf(r, after)
          if (mi !== -1) return insertMainContact(r, mi + 1, el)
          const found = branchOf(r, after)
          if (found) {
            const branches = r.branches.map((b) => {
              if (b.id !== found.branch.id) return b
              const contacts = [...b.contacts]
              contacts.splice(found.index + 1, 0, el)
              return { ...b, contacts }
            })
            return { ...r, branches }
          }
        }
        return insertMainContact(r, r.main.length, el)
      })
      return { ...state, rungs }
    }

    case 'ADD_BRANCH': {
      const branch: Branch = {
        id: uid(),
        startNodeId: action.startNodeId,
        contacts: [action.element],
        closeNodeId: action.closeNodeId ?? null,
        live: false,
      }
      const rungs = state.rungs.map((r) =>
        r.id === action.rungId ? { ...r, branches: [...r.branches, branch] } : r,
      )
      return { ...state, rungs }
    }

    case 'CLOSE_BRANCH': {
      const rungs = state.rungs.map((r) =>
        r.id === action.rungId
          ? {
              ...r,
              branches: r.branches.map((b) =>
                b.id === action.branchId ? { ...b, closeNodeId: action.closeNodeId } : b,
              ),
            }
          : r,
      )
      return { ...state, rungs }
    }

    case 'DELETE_BRANCH': {
      const rungs = state.rungs.map((r) =>
        r.id === action.rungId
          ? { ...r, branches: r.branches.filter((b) => b.id !== action.branchId) }
          : r,
      )
      return { ...state, rungs }
    }

    case 'DELETE_ELEMENT': {
      const rungs = state.rungs.map((r) => {
        const mi = mainIndexOf(r, action.elementId)
        if (mi !== -1) return removeMainContact(r, mi)
        const found = branchOf(r, action.elementId)
        if (found) {
          const branches = r.branches
            .map((b) =>
              b.id === found.branch.id
                ? { ...b, contacts: b.contacts.filter((c) => c.id !== action.elementId) }
                : b,
            )
            .filter((b) => b.contacts.length > 0)
          return { ...r, branches }
        }
        if (r.outputs.some((o) => o.id === action.elementId)) {
          return { ...r, outputs: r.outputs.filter((o) => o.id !== action.elementId) }
        }
        return r
      })
      return { ...state, rungs }
    }

    case 'CONFIGURE_ELEMENT': {
      const apply = (el: Element): Element =>
        el.id === action.elementId
          ? { ...el, varId: action.varId, params: action.params }
          : el
      return { ...state, rungs: state.rungs.map((r) => mapRungElements(r, apply)) }
    }

    case 'ADD_VARIABLE':
      return { ...state, variables: [...state.variables, action.variable] }

    case 'RENAME_VARIABLE':
      return {
        ...state,
        variables: state.variables.map((v) =>
          v.id === action.id ? { ...v, name: action.name } : v,
        ),
      }

    case 'SET_VARIABLE_VALUE':
      return {
        ...state,
        variables: state.variables.map((v) =>
          v.id === action.id ? { ...v, value: action.value } : v,
        ),
      }

    case 'DELETE_VARIABLE': {
      const variables = state.variables.filter((v) => v.id !== action.id)
      const clearRef = (el: Element): Element =>
        el.varId === action.id ? { ...el, varId: null } : el
      return {
        ...state,
        variables,
        rungs: state.rungs.map((r) => mapRungElements(r, clearRef)),
      }
    }

    case 'TICK':
      return {
        ...state,
        variables: action.variables,
        rungs: action.rungs,
        scanCount: state.scanCount + 1,
        lastCycleMs: action.cycleMs,
      }

    case 'RESET_SCAN':
      return { ...state, scanCount: 0, lastCycleMs: 0 }

    default:
      return state
  }
}

export function useSimulator() {
  return useReducer(reducer, undefined, createSeedState)
}
