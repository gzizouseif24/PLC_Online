import type { Rung } from '../types/simulator'
import type { GEdge, GNode, GraphRung } from '../types/graph'

export const COL = 92 // px between main-line nodes
export const ROW = 72 // px between branch rows

/**
 * Compile an editing rung (main line + branches) into the power-flow graph the
 * evaluator consumes. Contact edges reuse the Element `id` so liveness can be
 * mapped back after evaluation. Output Element refs are shared (not cloned) so
 * executeOutput mutates them directly. Open branches end at a dangling node
 * that connects to nothing (carries no power).
 */
export function compileRung(rung: Rung): GraphRung {
  const colOf = (nodeId: string) => Math.max(0, rung.mainNodes.indexOf(nodeId))

  const nodes: GNode[] = rung.mainNodes.map((id, i) => ({ id, x: i * COL, y: 0 }))

  const edges: GEdge[] = []
  for (let i = 0; i < rung.main.length; i++) {
    const c = rung.main[i]
    edges.push({
      id: c.id,
      kind: 'contact',
      type: c.type,
      varId: c.varId,
      params: c.params,
      from: rung.mainNodes[i],
      to: rung.mainNodes[i + 1],
      live: false,
    })
  }

  rung.branches.forEach((b, bi) => {
    const y = (bi + 1) * ROW
    const startCol = colOf(b.startNodeId)
    let prev = b.startNodeId
    b.contacts.forEach((c, ci) => {
      const isLast = ci === b.contacts.length - 1
      const next = isLast
        ? b.closeNodeId ?? `${b.id}:end`
        : `${b.id}:n${ci}`
      if (!nodes.some((n) => n.id === next)) {
        nodes.push({ id: next, x: (startCol + ci + 1) * COL, y })
      }
      edges.push({
        id: c.id,
        kind: 'contact',
        type: c.type,
        varId: c.varId,
        params: c.params,
        from: prev,
        to: next,
        live: false,
      })
      prev = next
    })
  })

  const K = rung.main.length
  return {
    id: rung.id,
    number: rung.number,
    leftId: rung.mainNodes[0],
    rightId: rung.mainNodes[K],
    nodes,
    edges,
    outputs: rung.outputs,
    power: false,
    comment: rung.comment,
  }
}
