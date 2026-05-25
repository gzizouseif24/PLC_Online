import type { CSSProperties, ReactNode } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { GitBranch, Trash2, CornerLeftUp } from 'lucide-react'
import type { Element, Rung, Variable } from '../types/simulator'
import { COL, ROW } from '../logic/compileRung'
import ElementView from './Element'
import { HWire, VWire } from './Wire'

/** A drop target that highlights when an instruction is dragged over it. */
function Drop({
  id,
  className,
  style,
  children,
}: {
  id: string
  className: string
  style: CSSProperties
  children?: ReactNode
}) {
  const { setNodeRef, isOver } = useDroppable({ id })
  return (
    <div ref={setNodeRef} className={`${className}${isOver ? ' over' : ''}`} style={style}>
      {children}
    </div>
  )
}

const RAIL_X = 18
const MAIN_Y = 52
const SYM_HALF = 30
const OUT_GAP = 28

interface Props {
  rung: Rung
  variables: Variable[]
  selected: boolean
  selectedElementId: string | null
  onSelectRung: (rungId: string) => void
  onSelectElement: (el: Element, rungId: string) => void
  onContextMenu: (el: Element, rungId: string, x: number, y: number) => void
  onAddBranch: (rungId: string, startNodeId: string) => void
  onCloseBranch: (rungId: string, branchId: string, closeNodeId: string) => void
  onDeleteRung: (rungId: string) => void
}

export default function RungView(props: Props) {
  const {
    rung,
    variables,
    selected,
    selectedElementId,
    onSelectRung,
    onSelectElement,
    onContextMenu,
    onAddBranch,
    onCloseBranch,
    onDeleteRung,
  } = props

  const K = rung.main.length
  const nodeX = (i: number) => RAIL_X + i * COL
  const cellCenter = (i: number) => nodeX(i) + COL / 2
  const colOf = (nodeId: string) => Math.max(0, rung.mainNodes.indexOf(nodeId))

  const mainNodeHot = (i: number): boolean => {
    if (i === 0) return true
    const nid = rung.mainNodes[i]
    if (rung.main[i - 1]?.live) return true
    if (rung.main[i]?.live) return true
    return rung.branches.some((b) => b.live && (b.startNodeId === nid || b.closeNodeId === nid))
  }

  // outputs sit between the main-line end and the right rail
  const outCount = rung.outputs.length
  const outX = (oi: number) => nodeX(K) + OUT_GAP + oi * COL + COL / 2
  const rightRailX = nodeX(K) + OUT_GAP + Math.max(1, outCount) * COL

  let maxX = rightRailX
  rung.branches.forEach((b) => {
    maxX = Math.max(maxX, nodeX(colOf(b.startNodeId)) + b.contacts.length * COL)
  })
  const width = maxX + RAIL_X
  const height = MAIN_Y + rung.branches.length * ROW + 60

  const mainCtl = selectedElementId ? rung.main.find((c) => c.id === selectedElementId) : undefined
  const canBranch = !!mainCtl

  const contact = (el: Element, x: number, y: number) => (
    <div
      key={el.id}
      style={{ position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)', zIndex: 3 }}
    >
      <ElementView
        element={el}
        variables={variables}
        selected={selectedElementId === el.id}
        onSelect={(e) => onSelectElement(e, rung.id)}
        onContextMenu={(e, cx, cy) => onContextMenu(e, rung.id, cx, cy)}
      />
    </div>
  )

  return (
    <div
      className={`rung${selected ? ' selected' : ''}${rung.power ? ' powered' : ''}`}
      onClick={() => onSelectRung(rung.id)}
    >
      <div className="rung-gutter" onClick={(e) => { e.stopPropagation(); onSelectRung(rung.id) }}>
        {rung.number}
      </div>

      <div className="rung-body">
        {rung.comment && <div className="rung-comment">{rung.comment}</div>}

        <div className="rung-canvas" style={{ width, height }}>
          {/* rails */}
          <div className="railbar live" style={{ left: nodeX(0) - 2.5, top: 16, height: height - 32 }} />
          <div className="railbar live" style={{ left: rightRailX - 2.5, top: 16, height: height - 32 }} />

          {/* main line */}
          {rung.main.map((c, i) => {
            const cx = cellCenter(i)
            return (
              <div key={c.id}>
                <HWire x1={nodeX(i)} x2={cx - SYM_HALF} y={MAIN_Y} live={mainNodeHot(i)} />
                <HWire x1={cx + SYM_HALF} x2={nodeX(i + 1)} y={MAIN_Y} live={c.live} />
                {contact(c, cx, MAIN_Y)}
              </div>
            )
          })}

          {/* main-end → outputs → right rail */}
          {(() => {
            const segs: ReactNode[] = []
            let cursor = nodeX(K)
            rung.outputs.forEach((_o, oi) => {
              const ox = outX(oi)
              segs.push(<HWire key={`ow${oi}`} x1={cursor} x2={ox - SYM_HALF} y={MAIN_Y} live={rung.power} />)
              cursor = ox + SYM_HALF
            })
            segs.push(<HWire key="oend" x1={cursor} x2={rightRailX} y={MAIN_Y} live={rung.power} />)
            return segs
          })()}
          {rung.outputs.map((o, oi) => contact(o, outX(oi), MAIN_Y))}

          {/* branches */}
          {rung.branches.map((b, bi) => {
            const by = MAIN_Y + (bi + 1) * ROW
            const sCol = colOf(b.startNodeId)
            const sx = nodeX(sCol)
            const startHot = mainNodeHot(sCol)
            const lastLive = b.contacts.length > 0 && b.contacts[b.contacts.length - 1].live
            const endCol = Math.min(K, sCol + b.contacts.length)

            return (
              <div key={b.id}>
                <VWire x={sx} y1={MAIN_Y} y2={by} live={startHot} />
                {b.contacts.map((c, ci) => {
                  const cx = sx + COL / 2 + ci * COL
                  const leftHot = ci === 0 ? startHot : b.contacts[ci - 1].live
                  return (
                    <div key={c.id}>
                      <HWire x1={sx + ci * COL} x2={cx - SYM_HALF} y={by} live={leftHot} />
                      <HWire x1={cx + SYM_HALF} x2={sx + (ci + 1) * COL} y={by} live={c.live} />
                      {contact(c, cx, by)}
                    </div>
                  )
                })}
                {b.closeNodeId ? (
                  <>
                    <HWire x1={sx + b.contacts.length * COL} x2={nodeX(colOf(b.closeNodeId))} y={by} live={lastLive} />
                    <VWire x={nodeX(colOf(b.closeNodeId))} y1={by} y2={MAIN_Y} live={lastLive} />
                  </>
                ) : (
                  <button
                    className="branch-arrow"
                    style={{ left: sx + b.contacts.length * COL - 10, top: by - 11 }}
                    title="Close this branch back onto the main line"
                    onClick={(e) => {
                      e.stopPropagation()
                      onCloseBranch(rung.id, b.id, rung.mainNodes[endCol])
                    }}
                  >
                    <CornerLeftUp size={14} />
                  </button>
                )}
              </div>
            )
          })}

          {/* ---- drop targets ---- */}
          {/* main-line insertion slots */}
          {Array.from({ length: K + 1 }).map((_, g) => (
            <Drop
              key={`ms${g}`}
              id={`main:${rung.id}:${g}`}
              className="dropzone mainslot"
              style={{ left: nodeX(g) - 14, top: MAIN_Y - 26, width: 28, height: 52 }}
            >
              <div className="ind vind" />
            </Drop>
          ))}
          {/* parallel-around-contact slots (below each main contact) */}
          {rung.main.map((c, i) => (
            <Drop
              key={`par${c.id}`}
              id={`par:${rung.id}:${c.id}`}
              className="dropzone parslot"
              style={{ left: cellCenter(i) - COL / 2, top: MAIN_Y + 16, width: COL, height: 40 }}
            >
              <div className="ind hind" />
            </Drop>
          ))}
          {/* extend-branch slots (end of each branch leg) */}
          {rung.branches.map((b) => {
            const sCol = colOf(b.startNodeId)
            const by = MAIN_Y + (rung.branches.indexOf(b) + 1) * ROW
            const x = nodeX(sCol) + b.contacts.length * COL
            return (
              <Drop
                key={`legend${b.id}`}
                id={`legend:${rung.id}:${b.id}`}
                className="dropzone legslot"
                style={{ left: x - 18, top: by - 22, width: 44, height: 44 }}
              >
                <div className="ind hind" />
              </Drop>
            )
          })}
          {/* output slot */}
          <Drop
            id={`out:${rung.id}`}
            className="dropzone outslot"
            style={{ left: nodeX(K) + 4, top: MAIN_Y - 24, width: rightRailX - nodeX(K) - 4, height: 48 }}
          >
            <div className="ind hind" />
          </Drop>
        </div>

        <div className="rung-tools">
          <button
            className="mini-btn"
            disabled={!canBranch}
            style={!canBranch ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
            title="Add an open parallel branch starting before the selected contact"
            onClick={(e) => {
              e.stopPropagation()
              if (mainCtl) {
                const ix = rung.main.findIndex((c) => c.id === mainCtl.id)
                onAddBranch(rung.id, rung.mainNodes[ix])
              }
            }}
          >
            <GitBranch size={13} /> Add branch
          </button>
          <button
            className="mini-btn danger"
            onClick={(e) => { e.stopPropagation(); onDeleteRung(rung.id) }}
          >
            <Trash2 size={13} /> Delete rung
          </button>
        </div>
      </div>
    </div>
  )
}
