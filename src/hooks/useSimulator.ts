import { useReducer } from 'react'
import type {
  Element,
  LadderNode,
  Rung,
  SimulatorState,
  Variable,
  VarValue,
} from '../types/simulator'
import { isInput } from '../logic/instructions'
import { elementNode, parallelNode, uid } from '../logic/factory'
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
    }
  | { type: 'ADD_PARALLEL'; rungId: string; targetElementId: string; element: Element }
  | { type: 'DELETE_ELEMENT'; elementId: string }
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

// ---- recursive node helpers ---------------------------------------------

function mapNodeElements(node: LadderNode, fn: (el: Element) => Element): LadderNode {
  if (node.kind === 'element') {
    return { ...node, element: fn(node.element) }
  }
  return {
    ...node,
    branches: node.branches.map((branch) => branch.map((n) => mapNodeElements(n, fn))),
  }
}

/** Remove the element with `elId` anywhere in a node series, collapsing empty parallels. */
function removeElement(nodes: LadderNode[], elId: string): LadderNode[] {
  const result: LadderNode[] = []
  for (const node of nodes) {
    if (node.kind === 'element') {
      if (node.element.id === elId) continue
      result.push(node)
    } else {
      const branches = node.branches
        .map((b) => removeElement(b, elId))
        .filter((b) => b.length > 0)
      if (branches.length === 0) continue
      if (branches.length === 1) {
        // collapse single-branch parallel back into a plain series
        result.push(...branches[0])
        continue
      }
      result.push({ ...node, branches })
    }
  }
  return result
}

function containsElement(nodes: LadderNode[], elId: string): boolean {
  return nodes.some((n) =>
    n.kind === 'element'
      ? n.element.id === elId
      : n.branches.some((b) => containsElement(b, elId)),
  )
}

/**
 * Add a parallel (OR) branch related to `elId`, at any depth:
 * - if `elId` is the sole element of a parallel leg → add a sibling leg to that parallel
 * - otherwise wrap the element itself in a new 2-leg parallel (nesting / crossing)
 */
function addParallel(nodes: LadderNode[], elId: string, newEl: Element): LadderNode[] {
  return nodes.map((node) => {
    if (node.kind === 'element') {
      return node.element.id === elId
        ? parallelNode([[node], [elementNode(newEl)]])
        : node
    }
    const legIndex = node.branches.findIndex(
      (b) => b.length === 1 && b[0].kind === 'element' && b[0].element.id === elId,
    )
    if (legIndex !== -1) {
      return { ...node, branches: [...node.branches, [elementNode(newEl)]] }
    }
    return { ...node, branches: node.branches.map((b) => addParallel(b, elId, newEl)) }
  })
}

/** Insert `newNode` in series immediately after the element `elId`, at any depth. */
function insertAfterElement(
  nodes: LadderNode[],
  elId: string,
  newNode: LadderNode,
): LadderNode[] {
  const out: LadderNode[] = []
  for (const node of nodes) {
    if (node.kind === 'element') {
      out.push(node)
      if (node.element.id === elId) out.push(newNode)
    } else {
      out.push({
        ...node,
        branches: node.branches.map((b) => insertAfterElement(b, elId, newNode)),
      })
    }
  }
  return out
}

// ---- reducer -------------------------------------------------------------

function nextRungNumber(rungs: Rung[]): number {
  return rungs.reduce((max, r) => Math.max(max, r.number), 0) + 1
}

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
        logic: [],
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
        if (isInput(el.type)) {
          // insert in series after the selected logic element, else append
          const logic =
            after && containsElement(r.logic, after)
              ? insertAfterElement(r.logic, after, elementNode(el))
              : [...r.logic, elementNode(el)]
          return { ...r, logic }
        }
        return { ...r, outputs: [...r.outputs, el] }
      })
      return { ...state, rungs }
    }

    case 'ADD_PARALLEL': {
      const rungs = state.rungs.map((r) =>
        r.id === action.rungId
          ? { ...r, logic: addParallel(r.logic, action.targetElementId, action.element) }
          : r,
      )
      return { ...state, rungs }
    }

    case 'DELETE_ELEMENT': {
      const rungs = state.rungs.map((r) => ({
        ...r,
        logic: removeElement(r.logic, action.elementId),
        outputs: r.outputs.filter((o) => o.id !== action.elementId),
      }))
      return { ...state, rungs }
    }

    case 'CONFIGURE_ELEMENT': {
      const apply = (el: Element): Element =>
        el.id === action.elementId
          ? { ...el, varId: action.varId, params: action.params }
          : el
      const rungs = state.rungs.map((r) => ({
        ...r,
        logic: r.logic.map((n) => mapNodeElements(n, apply)),
        outputs: r.outputs.map(apply),
      }))
      return { ...state, rungs }
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
      // null out references to the deleted variable
      const clearRef = (el: Element): Element =>
        el.varId === action.id ? { ...el, varId: null } : el
      const rungs = state.rungs.map((r) => ({
        ...r,
        logic: r.logic.map((n) => mapNodeElements(n, clearRef)),
        outputs: r.outputs.map(clearRef),
      }))
      return { ...state, variables, rungs }
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
