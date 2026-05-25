import { useEffect, useRef } from 'react'
import type { Dispatch } from 'react'
import type { SimulatorState } from '../types/simulator'
import type { Action } from './useSimulator'
import { primeEdgeMem, scanProgram, type EdgeMemMap } from '../logic/evaluate'

/**
 * Drives the scan cycle on a setInterval. Reads the latest state through a ref
 * so user variable toggles between scans are picked up, and keeps edge/timer
 * memory in a ref (outside serializable state).
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

    // seed edge memory from current state so steady inputs don't pulse on scan 1
    primeEdgeMem(stateRef.current.variables, stateRef.current.rungs, edgeMem.current)
    lastTs.current = performance.now()

    const id = window.setInterval(() => {
      const now = performance.now()
      const dt = Math.max(0, now - lastTs.current)
      lastTs.current = now

      const cur = stateRef.current
      const variables = structuredClone(cur.variables)
      const rungs = structuredClone(cur.rungs)

      const result = scanProgram(variables, rungs, dt, edgeMem.current)

      dispatch({
        type: 'TICK',
        variables: result.variables,
        rungs: result.rungs,
        cycleMs: Math.round(dt),
      })
    }, scanInterval)

    return () => window.clearInterval(id)
  }, [running, scanInterval, dispatch])
}
