import { GitBranch, Trash2, CornerLeftUp } from 'lucide-react'
import type { Element, Rung, Variable } from '../types/simulator'
import { COL, ROW } from '../logic/compileRung'
import ElementView from './Element'
import { HWire, VWire } from './Wire'

const RAIL_X = 18
const MAIN_Y = 52
const SYM_HALF = 30 // half-width reserved for a contact symbol
const OUT_GAP = 40

interface Props {
  rung: Rung
  variables: Variable[]
  selected: boolean
  selectedElementId: string | null
  closing: { branchId: string } | null // a branch on THIS rung is awaiting a close target
  onSelectRung: (rungId: string) => void
  onSelectElement: (el: Element, rungId: string) => void
  onContextMenu: (el: Element, rungId: string, x: number, y: number) => void
  onAddBranch: (rungId: string, startNodeId: string) => void
  onStartClose: (rungId: string, branchId: string) => void
  onCloseAtNode: (rungId: string, nodeId: string) => void
  onDeleteRung: (rungId: string) => void
}

export default function RungView(props: Props) {
  const {
    rung,
    variables,
    selected,
    selectedElementId,
    closing,
    onSelectRung,
    onSelectElement,
    onContextMenu,
    onAddBranch,
    onStartClose,
    onCloseAtNode,
    onDeleteRung,
  } = props

  const K = rung.main.length
  const nodeX = (i: number) => RAIL_X + i * COL
  const cellCenter = (i: number) => nodeX(i) + COL / 2
  const colOf = (nodeId: string) => Math.max(0, rung.mainNodes.indexOf(nodeId))

  // main node hotness (for wire color)
  const mainNodeHot = (i: number): boolean => {
    if (i === 0) return true
    const nid = rung.mainNodes[i]
    if (rung.main[i - 1]?.live) return true
    if (rung.main[i]?.live) return true
    return rung.branches.some(
      (b) => b.live && (b.startNodeId === nid || b.closeNodeId === nid),
    )
  }

  // geometry / size
  let maxX = nodeX(K)
  rung.branches.forEach((b) => {
    const endX = nodeX(colOf(b.startNodeId)) + b.contacts.length * COL
    maxX = Math.max(maxX, endX)
  })
  const outputsStartX = nodeX(K) + OUT_GAP
  const width = Math.max(maxX, outputsStartX + rung.outputs.length * (COL + 10)) + RAIL_X
  const height = MAIN_Y + rung.branches.length * ROW + 60

  const mainCtl = selectedElementId
    ? rung.main.find((c) => c.id === selectedElementId)
    : undefined
  const canBranch = !!mainCtl

  const contact = (el: Element, x: number, y: number) => (
    <div
      key={el.id}
      style={{ position: 'absolute', left: x, top: y, transform: 'translate(-50%, -50%)' }}
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
          <div className="railbar" style={{ left: nodeX(K) - 2.5, top: 16, height: height - 32 }} />

          {/* main line contacts + connecting wires */}
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
          {K === 0 && <HWire x1={nodeX(0)} x2={nodeX(0)} y={MAIN_Y} live={false} />}

          {/* clickable main nodes (targets when closing a branch) */}
          {closing &&
            rung.mainNodes.map((nid, i) => (
              <button
                key={nid}
                className="node-dot"
                style={{ left: nodeX(i) - 7, top: MAIN_Y - 7 }}
                title="Close branch here"
                onClick={(e) => {
                  e.stopPropagation()
                  onCloseAtNode(rung.id, nid)
                }}
              />
            ))}

          {/* branches */}
          {rung.branches.map((b, bi) => {
            const by = MAIN_Y + (bi + 1) * ROW
            const sCol = colOf(b.startNodeId)
            const sx = nodeX(sCol)
            const startHot = mainNodeHot(sCol)
            const endX = b.closeNodeId
              ? nodeX(colOf(b.closeNodeId))
              : sx + b.contacts.length * COL
            const lastLive = b.contacts.length > 0 && b.contacts[b.contacts.length - 1].live

            return (
              <div key={b.id}>
                {/* start riser down from main */}
                <VWire x={sx} y1={MAIN_Y} y2={by} live={startHot} />
                {/* leg contacts */}
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
                    {/* horizontal run to the close column, then riser up to main */}
                    <HWire x1={sx + b.contacts.length * COL} x2={endX} y={by} live={lastLive} />
                    <VWire x={endX} y1={by} y2={MAIN_Y} live={lastLive} />
                  </>
                ) : (
                  /* OPEN end — arrow handle the user clicks, then picks a main node */
                  <button
                    className={`branch-arrow${closing?.branchId === b.id ? ' active' : ''}`}
                    style={{ left: sx + b.contacts.length * COL - 9, top: by - 9 }}
                    title="Close this branch — then click a node on the main line"
                    onClick={(e) => {
                      e.stopPropagation()
                      onStartClose(rung.id, b.id)
                    }}
                  >
                    <CornerLeftUp size={14} />
                  </button>
                )}
              </div>
            )
          })}

          {/* outputs to the right of the right rail */}
          <HWire x1={nodeX(K)} x2={outputsStartX} y={MAIN_Y} live={rung.power} />
          {rung.outputs.map((o, oi) => (
            <div key={o.id}>
              {oi > 0 && (
                <HWire
                  x1={outputsStartX + oi * (COL + 10) - 10}
                  x2={outputsStartX + oi * (COL + 10)}
                  y={MAIN_Y}
                  live={rung.power}
                />
              )}
              {contact(o, outputsStartX + oi * (COL + 10) + COL / 2, MAIN_Y)}
            </div>
          ))}
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
          {closing && (
            <span className="closing-hint">Click a node on the main line to close the branch…</span>
          )}
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
