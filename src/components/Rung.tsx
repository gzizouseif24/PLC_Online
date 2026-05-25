import type { CSSProperties, ReactNode } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { GitBranch, Trash2, CornerLeftUp } from 'lucide-react'
import type { Element, Rung, Variable } from '../types/simulator'
import { COL, ROW } from '../logic/compileRung'
import ElementView from './Element'
import { HWire, VWire } from './Wire'

const RAIL_X = 18
const MAIN_Y = 52
const SYM_HALF = 30
const OUT_GAP = 28

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
  const idxOf = (nodeId: string) => Math.max(0, rung.mainNodes.indexOf(nodeId))

  // --- layout pass: stretch main columns so closed branches fit & rejoin cleanly
  const colPos: number[] = []
  for (let i = 0; i <= K; i++) colPos[i] = i
  for (const b of rung.branches) {
    if (!b.closeNodeId) continue
    const s = idxOf(b.startNodeId)
    const e = idxOf(b.closeNodeId)
    if (e <= s) continue
    const need = b.contacts.length
    const have = colPos[e] - colPos[s]
    if (have < need) {
      const shift = need - have
      for (let j = e; j <= K; j++) colPos[j] += shift
    }
  }
  const X = (cell: number) => RAIL_X + cell * COL
  const nodeX = (i: number) => X(colPos[i])
  const mainCenter = (i: number) => X((colPos[i] + colPos[i + 1]) / 2)

  const mainNodeHot = (i: number): boolean => {
    if (i === 0) return true
    const nid = rung.mainNodes[i]
    if (rung.main[i - 1]?.live) return true
    if (rung.main[i]?.live) return true
    return rung.branches.some((b) => b.live && (b.startNodeId === nid || b.closeNodeId === nid))
  }

  const outCount = rung.outputs.length
  const outX = (oi: number) => nodeX(K) + OUT_GAP + oi * COL + COL / 2
  const rightRailX = nodeX(K) + OUT_GAP + Math.max(1, outCount) * COL

  let maxX = rightRailX
  rung.branches.forEach((b) => {
    maxX = Math.max(maxX, X(colPos[idxOf(b.startNodeId)] + b.contacts.length))
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
            const cx = mainCenter(i)
            return (
              <div key={c.id}>
                <HWire x1={nodeX(i)} x2={cx - SYM_HALF} y={MAIN_Y} live={mainNodeHot(i)} />
                <HWire x1={cx + SYM_HALF} x2={nodeX(i + 1)} y={MAIN_Y} live={c.live} />
                {contact(c, cx, MAIN_Y)}
              </div>
            )
          })}

          {/* main end → outputs → right rail */}
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
            const sIdx = idxOf(b.startNodeId)
            const sCell = colPos[sIdx]
            const sx = X(sCell)
            const startHot = mainNodeHot(sIdx)
            const N = b.contacts.length
            const legEndX = X(sCell + N)
            const lastLive = N > 0 && b.contacts[N - 1].live

            return (
              <div key={b.id}>
                <VWire x={sx} y1={MAIN_Y} y2={by} live={startHot} />
                {b.contacts.map((c, ci) => {
                  const cx = X(sCell + ci + 0.5)
                  const leftHot = ci === 0 ? startHot : b.contacts[ci - 1].live
                  return (
                    <div key={c.id}>
                      <HWire x1={X(sCell + ci)} x2={cx - SYM_HALF} y={by} live={leftHot} />
                      <HWire x1={cx + SYM_HALF} x2={X(sCell + ci + 1)} y={by} live={c.live} />
                      {contact(c, cx, by)}
                    </div>
                  )
                })}
                {b.closeNodeId ? (
                  <>
                    <HWire x1={legEndX} x2={nodeX(idxOf(b.closeNodeId))} y={by} live={lastLive} />
                    <VWire x={nodeX(idxOf(b.closeNodeId))} y1={by} y2={MAIN_Y} live={lastLive} />
                  </>
                ) : (
                  <button
                    className="branch-arrow"
                    style={{ left: legEndX - 10, top: by - 11 }}
                    title="Close this branch back onto the main line"
                    onClick={(e) => {
                      e.stopPropagation()
                      const closeIdx = Math.max(sIdx + 1, Math.min(sIdx + N, K))
                      onCloseBranch(rung.id, b.id, rung.mainNodes[closeIdx])
                    }}
                  >
                    <CornerLeftUp size={14} />
                  </button>
                )}
                {/* extend-branch drop slot at the leg end */}
                <Drop
                  id={`legend:${rung.id}:${b.id}`}
                  className="dropzone legslot"
                  style={{ left: legEndX - 18, top: by - 22, width: 44, height: 44 }}
                >
                  <div className="ind hind" />
                </Drop>
              </div>
            )
          })}

          {/* ---- main-line drop targets ---- */}
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
          {rung.main.map((c, i) => (
            <Drop
              key={`par${c.id}`}
              id={`par:${rung.id}:${c.id}`}
              className="dropzone parslot"
              style={{ left: nodeX(i), top: MAIN_Y + 16, width: nodeX(i + 1) - nodeX(i), height: 40 }}
            >
              <div className="ind hind" />
            </Drop>
          ))}
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
