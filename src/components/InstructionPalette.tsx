import type { InstructionType } from '../types/simulator'
import { CATEGORY_ORDER, instructionsByCategory } from '../logic/instructions'

interface Props {
  hasTarget: boolean
  onPick: (instr: InstructionType) => void
}

const byCat = instructionsByCategory()

export default function InstructionPalette({ hasTarget, onPick }: Props) {
  return (
    <aside className="panel panel-left">
      <div className="panel-header">Instructions</div>

      <div className="palette-hint">
        {hasTarget
          ? 'Click an instruction to add it after the selected element (or to the end).'
          : 'Select a rung, then click an instruction to add it.'}
      </div>

      {CATEGORY_ORDER.map((cat) => (
        <div className="palette-group" key={cat}>
          <div className="palette-group-title">{cat}</div>
          {byCat[cat].map((m) => (
            <button
              key={m.type}
              className="instr-tile"
              disabled={!hasTarget}
              onClick={() => onPick(m.type)}
              title={m.description}
            >
              <span className="instr-glyph">{m.symbol}</span>
              <span className="instr-name">{m.label}</span>
            </button>
          ))}
        </div>
      ))}
    </aside>
  )
}
