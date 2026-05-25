import { GitBranch, Trash2 } from 'lucide-react'
import type { Element, LadderNode, Rung, Variable } from '../types/simulator'
import NodeSeries from './LadderNodeView'
import ElementView from './Element'

interface Props {
  rung: Rung
  variables: Variable[]
  selected: boolean
  selectedElementId: string | null
  onSelectRung: (rungId: string) => void
  onSelectElement: (el: Element, rungId: string) => void
  onContextMenu: (el: Element, rungId: string, region: 'logic' | 'output', x: number, y: number) => void
  onAddBranch: (rungId: string, elementId: string) => void
  onDeleteRung: (rungId: string) => void
}

function logicHas(nodes: LadderNode[], elId: string): boolean {
  return nodes.some((n) =>
    n.kind === 'element'
      ? n.element.id === elId
      : n.branches.some((b) => logicHas(b, elId)),
  )
}

export default function RungView({
  rung,
  variables,
  selected,
  selectedElementId,
  onSelectRung,
  onSelectElement,
  onContextMenu,
  onAddBranch,
  onDeleteRung,
}: Props) {
  const canBranch = selectedElementId !== null && logicHas(rung.logic, selectedElementId)

  return (
    <div
      className={`rung${selected ? ' selected' : ''}${rung.power ? ' powered' : ''}`}
      onClick={() => onSelectRung(rung.id)}
    >
      <div
        className="rung-gutter"
        onClick={(e) => {
          e.stopPropagation()
          onSelectRung(rung.id)
        }}
        title="Select rung"
      >
        {rung.number}
      </div>

      <div className="rung-body">
        {rung.comment && <div className="rung-comment">{rung.comment}</div>}

        <div className="rung-line">
          <div className="rail rail-left live" />

          <div className="logic-flow">
            <NodeSeries
              nodes={rung.logic}
              variables={variables}
              incomingLive
              selectedId={selectedElementId}
              onSelect={(el) => onSelectElement(el, rung.id)}
              onContextMenu={(el, x, y) => onContextMenu(el, rung.id, 'logic', x, y)}
            />
          </div>

          <div className="output-flow">
            {rung.outputs.map((o) => (
              <span key={o.id} style={{ display: 'flex', alignItems: 'center' }}>
                <div className={`wire${rung.power ? ' live' : ''}`} />
                <ElementView
                  element={o}
                  variables={variables}
                  selected={selectedElementId === o.id}
                  onSelect={(el) => onSelectElement(el, rung.id)}
                  onContextMenu={(el, x, y) => onContextMenu(el, rung.id, 'output', x, y)}
                />
              </span>
            ))}
            <div className={`wire${rung.power ? ' live' : ''}`} />
          </div>

          <div className="rail rail-right" />
        </div>

        <div className="rung-tools">
          <button
            className="mini-btn"
            disabled={!canBranch}
            style={!canBranch ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
            onClick={(e) => {
              e.stopPropagation()
              if (canBranch && selectedElementId) onAddBranch(rung.id, selectedElementId)
            }}
            title="Add a parallel OR branch around the selected contact"
          >
            <GitBranch size={13} /> OR branch
          </button>
          <button
            className="mini-btn danger"
            onClick={(e) => {
              e.stopPropagation()
              onDeleteRung(rung.id)
            }}
          >
            <Trash2 size={13} /> Delete rung
          </button>
        </div>
      </div>
    </div>
  )
}
