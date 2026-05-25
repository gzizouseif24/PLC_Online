import type { Element, InstructionType } from './simulator'

/** A junction in a rung's power-flow graph. x/y are canvas coordinates. */
export interface GNode {
  id: string
  x: number
  y: number
}

/**
 * An edge in the power-flow graph.
 * - 'contact' edges conduct based on a contact/compare instruction.
 * - 'link' edges are plain wires that always conduct (used for branch joins
 *   and bridge/crossing connectors that make a network non-series-parallel).
 * Edges are undirected: power can flow either way (required for bridges).
 */
export interface GEdge {
  id: string
  kind: 'contact' | 'link'
  type?: InstructionType // for contact edges
  varId?: string | null
  params?: Record<string, string | number>
  from: string // node id
  to: string // node id
  live: boolean
}

export interface GraphRung {
  id: string
  number: number
  leftId: string // left power rail node
  rightId: string // node that feeds the outputs (right side)
  nodes: GNode[]
  edges: GEdge[]
  outputs: Element[] // coils/timers/counters/math, driven by connectivity(left,right)
  power: boolean
  comment: string
}
