import type { MouseEvent } from 'react'
import type {
  CounterValue,
  Element,
  TimerValue,
  Variable,
} from '../types/simulator'
import { INSTRUCTION_MAP } from '../logic/instructions'
import ElementSymbol from './ElementSymbol'

interface Props {
  element: Element
  variables: Variable[]
  selected: boolean
  onSelect: (el: Element) => void
  onContextMenu: (el: Element, x: number, y: number) => void
}

function srcLabel(raw: string | number | undefined, variables: Variable[]): string {
  if (raw === undefined || raw === '') return '?'
  if (typeof raw === 'number') return String(raw)
  const v = variables.find((x) => x.id === raw)
  if (v) return v.name
  return String(raw)
}

export default function ElementView({
  element,
  variables,
  selected,
  onSelect,
  onContextMenu,
}: Props) {
  const meta = INSTRUCTION_MAP[element.type]
  const v = element.varId ? variables.find((x) => x.id === element.varId) : undefined

  let label = ''
  let unset = false
  let sub: string | null = null
  let progress: number | null = null

  switch (meta.category) {
    case 'Contacts':
    case 'Coils': {
      label = v ? v.name : '??'
      unset = !v
      break
    }
    case 'Timers': {
      label = v ? v.name : '??'
      unset = !v
      const t = v?.value as TimerValue | undefined
      const PT = Number(element.params.PT ?? t?.PT ?? 0)
      const ET = t?.ET ?? 0
      sub = `${ET}/${PT}ms`
      progress = PT > 0 ? Math.min(1, ET / PT) : 0
      break
    }
    case 'Counters': {
      if (element.type === 'RES') {
        label = v ? v.name : '??'
        unset = !v
        break
      }
      label = v ? v.name : '??'
      unset = !v
      const c = v?.value as CounterValue | undefined
      const PV = Number(element.params.PV ?? c?.PV ?? 0)
      const ACC = c?.ACC ?? 0
      sub = `${ACC}/${PV}`
      break
    }
    case 'Math': {
      label = v ? v.name : '??'
      unset = !v
      if (element.type === 'MOV') {
        sub = `= ${srcLabel(element.params.src ?? element.params.srcA, variables)}`
      } else {
        const op =
          element.type === 'ADD'
            ? '+'
            : element.type === 'SUB'
              ? '-'
              : element.type === 'MUL'
                ? '×'
                : '÷'
        sub = `= ${srcLabel(element.params.srcA, variables)} ${op} ${srcLabel(element.params.srcB, variables)}`
      }
      break
    }
    case 'Compare': {
      const op =
        element.type === 'EQU'
          ? '='
          : element.type === 'NEQ'
            ? '≠'
            : element.type === 'GRT'
              ? '>'
              : element.type === 'LES'
                ? '<'
                : element.type === 'GEQ'
                  ? '≥'
                  : '≤'
      label = `${srcLabel(element.params.srcA, variables)} ${op} ${srcLabel(element.params.srcB, variables)}`
      unset = element.params.srcA === undefined || element.params.srcB === undefined
      break
    }
  }

  return (
    <div
      className={`element${selected ? ' selected' : ''}`}
      onClick={(e: MouseEvent) => {
        e.stopPropagation()
        onSelect(element)
      }}
      onContextMenu={(e: MouseEvent) => {
        e.preventDefault()
        e.stopPropagation()
        onContextMenu(element, e.clientX, e.clientY)
      }}
      title={meta.label}
    >
      <span className={`element-label${unset ? ' unset' : ''}`}>{label}</span>
      <ElementSymbol type={element.type} live={element.live} />
      {(sub || progress !== null) && (
        <div className="element-below">
          {sub && <span className="element-sub">{sub}</span>}
          {progress !== null && (
            <div className="block-progress">
              <span style={{ width: `${progress * 100}%` }} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
