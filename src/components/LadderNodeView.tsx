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
  selectedId,
  onSelect,
  onContextMenu,
}: {
  node: LadderNode
  variables: Variable[]
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
  return (
    <div className="parallel">
      <div className={`parallel-branches${live ? ' live' : ''}`}>
        {node.branches.map((branch, i) => (
          <div className="branch-row" key={i}>
            <NodeSeries
              nodes={branch}
              variables={variables}
              incomingLive={false}
              selectedId={selectedId}
              onSelect={onSelect}
              onContextMenu={onContextMenu}
            />
          </div>
        ))}
      </div>
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
