import { Plus } from 'lucide-react'
import type { Element, Rung, Variable } from '../types/simulator'
import RungView from './Rung'

interface Props {
  rungs: Rung[]
  variables: Variable[]
  selectedRungId: string | null
  selectedElementId: string | null
  onSelectRung: (rungId: string) => void
  onSelectElement: (el: Element, rungId: string) => void
  onContextMenu: (el: Element, rungId: string, region: 'logic' | 'output', x: number, y: number) => void
  onAddBranch: (rungId: string, elementId: string) => void
  onDeleteRung: (rungId: string) => void
  onAddRung: () => void
  onClearSelection: () => void
}

export default function LadderCanvas({
  rungs,
  variables,
  selectedRungId,
  selectedElementId,
  onSelectRung,
  onSelectElement,
  onContextMenu,
  onAddBranch,
  onDeleteRung,
  onAddRung,
  onClearSelection,
}: Props) {
  return (
    <div
      className="canvas"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClearSelection()
      }}
    >
      {rungs.length === 0 && (
        <div className="canvas-empty">No rungs yet — click "Add rung" to start.</div>
      )}

      {rungs.map((rung) => (
        <RungView
          key={rung.id}
          rung={rung}
          variables={variables}
          selected={selectedRungId === rung.id}
          selectedElementId={selectedElementId}
          onSelectRung={onSelectRung}
          onSelectElement={onSelectElement}
          onContextMenu={onContextMenu}
          onAddBranch={onAddBranch}
          onDeleteRung={onDeleteRung}
        />
      ))}

      <button className="add-rung-btn" onClick={onAddRung}>
        <Plus size={16} /> Add rung
      </button>
    </div>
  )
}
