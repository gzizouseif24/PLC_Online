import { useEffect, useRef } from 'react'
import type { Dispatch } from 'react'
import type { SimulatorState } from '../types/simulator'
import type { Action } from './useSimulator'
import { compileRung } from '../logic/compileRung'
import { primeGraphEdgeMem, scanGraphProgram } from '../logic/evaluateGraph'
import { type EdgeMemMap } from '../logic/evaluate'

/**
 * Drives the scan cycle: compile each editing rung to its power-flow graph,
 * evaluate connectivity, then map liveness/power back onto the editing rungs.
 * Reads latest state through a ref so live variable toggles are picked up, and
 * keeps edge/timer memory in a ref (outside serializable state).
 */
export function useScanCycle(state: SimulatorState, dispatch: Dispatch<Action>) {
  const stateRef = useRef(state)
  useEffect(() => {
    stateRef.current = state
  })

  const edgeMem = useRef<EdgeMemMap>(new Map())
  const lastTs = useRef<number>(0)

  const { running, scanInterval } = state

  useEffect(() => {
    if (!running) return

    const cur = stateRef.current
    primeGraphEdgeMem(cur.variables, cur.rungs.map(compileRung), edgeMem.current)
    lastTs.current = performance.now()

    const id = window.setInterval(() => {
      const now = performance.now()
      const dt = Math.max(0, now - lastTs.current)
      lastTs.current = now

      const snap = stateRef.current
      const variables = structuredClone(snap.variables)
      const rungs = structuredClone(snap.rungs)
      const graphs = rungs.map(compileRung) // shares output Element refs with rungs

      scanGraphProgram(variables, graphs, dt, edgeMem.current)

      // map liveness + power back onto the editing rungs
      rungs.forEach((er, i) => {
        const g = graphs[i]
        er.power = g.power
        const liveById = new Map(g.edges.map((e) => [e.id, e.live]))
        for (const c of er.main) c.live = liveById.get(c.id) ?? false
        for (const b of er.branches) {
          for (const c of b.contacts) c.live = liveById.get(c.id) ?? false
          b.live = b.contacts.length > 0 && b.contacts.every((c) => c.live)
        }
      })

      dispatch({ type: 'TICK', variables, rungs, cycleMs: Math.round(dt) })
    }, scanInterval)

    return () => window.clearInterval(id)
  }, [running, scanInterval, dispatch])
}
