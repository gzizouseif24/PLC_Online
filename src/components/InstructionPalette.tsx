import type { InstructionType } from '../types/simulator'
import { CATEGORY_ORDER, instructionsByCategory } from '../logic/instructions'

interface Props {
  branchMode: boolean
  hasTarget: boolean
  onPick: (instr: InstructionType) => void
}

const byCat = instructionsByCategory()

export default function InstructionPalette({ branchMode, hasTarget, onPick }: Props) {
  return (
    <aside className="panel panel-left">
      <div className="panel-header">Instructions</div>

      {branchMode ? (
        <div className="palette-hint" style={{ color: 'var(--accent)' }}>
          Branch mode: pick a contact to place on a new parallel branch.
        </div>
      ) : !hasTarget ? (
        <div className="palette-hint">Select a rung, then click an instruction to add it.</div>
      ) : (
        <div className="palette-hint">Click an instruction to add it to the selected rung.</div>
      )}

      {CATEGORY_ORDER.map((cat) => (
        <div className="palette-group" key={cat}>
          <div className="palette-group-title">{cat}</div>
          {byCat[cat].map((m) => {
            const disabled = branchMode ? m.role !== 'input' : !hasTarget
            return (
              <button
                key={m.type}
                className="instr-tile"
                disabled={disabled}
                onClick={() => onPick(m.type)}
                title={m.description}
              >
                <span className="instr-glyph">{m.symbol}</span>
                <span className="instr-name">{m.label}</span>
              </button>
            )
          })}
        </div>
      ))}
    </aside>
  )
}
