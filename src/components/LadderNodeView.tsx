import type { ReactNode } from 'react'
import type { Element, LadderNode, Variable } from '../types/simulator'
import ElementView from './Element'

interface SeriesProps {
  nodes: LadderNode[]
  variables: Variable[]
  incomingLive: boolean
  selectedId: string | null
  onSelect: (el: Element) => void
  onContextMenu: (el: Element, x: number, y: number) => void
}

function seriesLiveOut(nodes: LadderNode[]): boolean {
  if (nodes.length === 0) return true
  return nodeLiveOut(nodes[nodes.length - 1])
}

function nodeLiveOut(node: LadderNode): boolean {
  if (node.kind === 'element') return node.element.live
  return node.branches.some((b) => seriesLiveOut(b))
}

function NodeView({
  node,
  variables,
  incomingLive,
  selectedId,
  onSelect,
  onContextMenu,
}: {
  node: LadderNode
  variables: Variable[]
  incomingLive: boolean
  selectedId: string | null
  onSelect: (el: Element) => void
  onContextMenu: (el: Element, x: number, y: number) => void
}) {
  if (node.kind === 'element') {
    return (
      <ElementView
        element={node.element}
        variables={variables}
        selected={selectedId === node.element.id}
        onSelect={onSelect}
        onContextMenu={onContextMenu}
      />
    )
  }

  const live = nodeLiveOut(node)
  const n = node.branches.length
  const GAP = 60 // vertical distance between branch centers
  const riserH = (n - 1) * GAP
  const center = riserH + 20 // axis (matches single-element center)

  return (
    <div className="parallel" style={{ padding: `${riserH}px 0` }}>
      {node.branches.map((branch, i) => (
        <div
          key={i}
          className={`branch-row${i === 0 ? ' main' : ' sub'}`}
          style={i === 0 ? undefined : { top: riserH + i * GAP }}
        >
          <NodeSeries
            nodes={branch}
            variables={variables}
            incomingLive={incomingLive}
            selectedId={selectedId}
            onSelect={onSelect}
            onContextMenu={onContextMenu}
          />
        </div>
      ))}
      {riserH > 0 && (
        <>
          <span className={`riser left${incomingLive ? ' live' : ''}`} style={{ top: center, height: riserH }} />
          <span className={`riser right${live ? ' live' : ''}`} style={{ top: center, height: riserH }} />
        </>
      )}
    </div>
  )
}

export default function NodeSeries({
  nodes,
  variables,
  incomingLive,
  selectedId,
  onSelect,
  onContextMenu,
}: SeriesProps) {
  if (nodes.length === 0) {
    return <div className={`wire grow${incomingLive ? ' live' : ''}`} />
  }

  const parts: ReactNode[] = []
  let prevLive = incomingLive

  nodes.forEach((node) => {
    parts.push(<div key={`w-${node.id}`} className={`wire${prevLive ? ' live' : ''}`} />)
    parts.push(
      <NodeView
        key={node.id}
        node={node}
        variables={variables}
        incomingLive={prevLive}
        selectedId={selectedId}
        onSelect={onSelect}
        onContextMenu={onContextMenu}
      />,
    )
    prevLive = nodeLiveOut(node)
  })

  parts.push(<div key="w-tail" className={`wire grow${prevLive ? ' live' : ''}`} />)
  return <>{parts}</>
}
