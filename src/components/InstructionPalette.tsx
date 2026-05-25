import { useDraggable } from '@dnd-kit/core'
import type { InstructionType } from '../types/simulator'
import { CATEGORY_ORDER, INSTRUCTION_MAP, instructionsByCategory } from '../logic/instructions'

interface Props {
  hasTarget: boolean
  onPick: (instr: InstructionType) => void
}

const byCat = instructionsByCategory()

function Tile({
  type,
  disabled,
  onPick,
}: {
  type: InstructionType
  disabled: boolean
  onPick: (t: InstructionType) => void
}) {
  const m = INSTRUCTION_MAP[type]
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `instr:${type}`,
    data: { type, role: m.role },
    disabled,
  })
  return (
    <button
      ref={setNodeRef}
      className="instr-tile"
      disabled={disabled}
      style={isDragging ? { opacity: 0.4 } : undefined}
      onClick={() => onPick(type)}
      title={`${m.description} — drag onto a rung, or click to add`}
      {...listeners}
      {...attributes}
    >
      <span className="instr-glyph">{m.symbol}</span>
      <span className="instr-name">{m.label}</span>
    </button>
  )
}

export default function InstructionPalette({ hasTarget, onPick }: Props) {
  return (
    <aside className="panel panel-left">
      <div className="panel-header">Instructions</div>

      <div className="palette-hint">
        Drag an instruction onto a rung (a blue line shows where it lands), or click to add.
      </div>

      {CATEGORY_ORDER.map((cat) => (
        <div className="palette-group" key={cat}>
          <div className="palette-group-title">{cat}</div>
          {byCat[cat].map((m) => (
            <Tile key={m.type} type={m.type} disabled={!hasTarget} onPick={onPick} />
          ))}
        </div>
      ))}
    </aside>
  )
}
