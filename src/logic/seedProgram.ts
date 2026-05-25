import type { SimulatorState } from '../types/simulator'
import { makeElement, makeVariable } from './factory'

/**
 * Preloaded motor starter with seal-in:
 *   ( Start[NO]  parallel  Motor[NO] )  in series with  Stop[NC]  ->  Motor coil
 *
 * Main line nodes n0─Start─n1─Stop─n2. Branch starts at n0, holds Motor[NO],
 * closed onto n1 (so Motor parallels Start).
 */
export function createSeedState(): SimulatorState {
  const start = makeVariable('Start', 'BOOL', false)
  const stop = makeVariable('Stop', 'BOOL', false)
  const motor = makeVariable('Motor', 'BOOL', false)

  return {
    variables: [start, stop, motor],
    rungs: [
      {
        id: 'rung-seed-1',
        number: 1,
        mainNodes: ['n0', 'n1', 'n2'],
        main: [makeElement('NO', start.id), makeElement('NC', stop.id)],
        branches: [
          {
            id: 'branch-seed-1',
            startNodeId: 'n0',
            contacts: [makeElement('NO', motor.id)],
            closeNodeId: 'n1',
            live: false,
          },
        ],
        outputs: [makeElement('COIL', motor.id)],
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
