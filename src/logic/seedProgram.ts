import type { SimulatorState } from '../types/simulator'
import { elementNode, makeElement, makeVariable, parallelNode } from './factory'

/**
 * Preloaded motor starter with seal-in:
 *   ( Start[NO]  OR  Motor[NO] )  AND  Stop[NC]  ->  Motor (COIL)
 *
 * The parallel branch wraps a SUBSET of the rung (Start ∥ Motor seal), both in
 * series with Stop — a true seal-in that the flat "whole-rung OR" model cannot express.
 */
export function createSeedState(): SimulatorState {
  const start = makeVariable('Start', 'BOOL', false)
  const stop = makeVariable('Stop', 'BOOL', false)
  const motor = makeVariable('Motor', 'BOOL', false)

  const startContact = makeElement('NO', start.id)
  const sealContact = makeElement('NO', motor.id)
  const stopContact = makeElement('NC', stop.id)
  const motorCoil = makeElement('COIL', motor.id)

  return {
    variables: [start, stop, motor],
    rungs: [
      {
        id: 'rung-seed-1',
        number: 1,
        logic: [
          parallelNode([[elementNode(startContact)], [elementNode(sealContact)]]),
          elementNode(stopContact),
        ],
        outputs: [motorCoil],
        power: false,
        comment: 'Motor starter — Start/Stop with seal-in',
      },
    ],
    running: false,
    scanCount: 0,
    lastCycleMs: 0,
    scanInterval: 100,
  }
}
