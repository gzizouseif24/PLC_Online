import type { Variable } from '../types/simulator'
import type { GEdge, GraphRung } from '../types/graph'
import {
  evaluateContact,
  executeOutput,
  getMem,
  readBool,
  type EdgeMemMap,
} from './evaluate'

/** Union-find for undirected connectivity (power can flow either way). */
class UnionFind {
  private parent = new Map<string, string>()
  find(x: string): string {
    const p = this.parent.get(x)
    if (p === undefined) {
      this.parent.set(x, x)
      return x
    }
    if (p === x) return x
    const root = this.find(p)
    this.parent.set(x, root)
    return root
  }
  union(a: string, b: string) {
    this.parent.set(this.find(a), this.find(b))
  }
  connected(a: string, b: string) {
    return this.find(a) === this.find(b)
  }
}

function edgeConducts(
  edge: GEdge,
  varsById: Map<string, Variable>,
  mem: EdgeMemMap,
): boolean {
  if (edge.kind === 'link') return true
  return evaluateContact(
    {
      id: edge.id,
      type: edge.type!,
      varId: edge.varId ?? null,
      params: edge.params ?? {},
      live: false,
    },
    varsById,
    mem,
  )
}

/**
 * Evaluate every rung as a power-flow graph: a contact/link edge conducts per
 * its condition, and the rung has power when the right terminal is connected to
 * the left rail through conducting edges (handles bridges/crossings naturally).
 * Mutates the passed (cloned) variables/rungs; updates edge memory for the next scan.
 */
export function scanGraphProgram(
  variables: Variable[],
  rungs: GraphRung[],
  dtMs: number,
  mem: EdgeMemMap,
): { variables: Variable[]; rungs: GraphRung[] } {
  const varsById = new Map<string, Variable>()
  for (const v of variables) varsById.set(v.id, v)

  for (const rung of rungs) {
    const uf = new UnionFind()
    uf.find(rung.leftId)
    uf.find(rung.rightId)

    const conductsById = new Map<string, boolean>()
    for (const edge of rung.edges) {
      const conducts = edgeConducts(edge, varsById, mem)
      conductsById.set(edge.id, conducts)
      if (conducts) uf.union(edge.from, edge.to)
    }

    const power = uf.connected(rung.leftId, rung.rightId)
    rung.power = power

    // an edge is energized if it conducts and sits in the hot (left-connected) component
    for (const edge of rung.edges) {
      edge.live =
        (conductsById.get(edge.id) ?? false) && uf.connected(edge.from, rung.leftId)
    }

    for (const out of rung.outputs) {
      executeOutput(out, power, varsById, mem, dtMs)
    }

    // edge memory for next scan
    for (const edge of rung.edges) {
      if (edge.kind === 'contact' && (edge.type === 'P_CONTACT' || edge.type === 'N_CONTACT')) {
        getMem(mem, edge.id).prevVar = readBool(edge.varId ? varsById.get(edge.varId) : undefined)
      }
    }
    for (const out of rung.outputs) {
      getMem(mem, out.id).prevPower = power
    }
  }

  return { variables, rungs }
}

/** Seed edge memory from current state so steady inputs don't pulse on scan 1. */
export function primeGraphEdgeMem(
  variables: Variable[],
  rungs: GraphRung[],
  mem: EdgeMemMap,
) {
  mem.clear()
  const varsById = new Map<string, Variable>()
  for (const v of variables) varsById.set(v.id, v)
  for (const rung of rungs) {
    for (const edge of rung.edges) {
      if (edge.kind === 'contact' && (edge.type === 'P_CONTACT' || edge.type === 'N_CONTACT')) {
        getMem(mem, edge.id).prevVar = readBool(edge.varId ? varsById.get(edge.varId) : undefined)
      }
    }
  }
}
