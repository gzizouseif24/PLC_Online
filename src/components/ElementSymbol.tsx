import type { InstructionType } from '../types/simulator'
import { INSTRUCTION_MAP } from '../logic/instructions'

interface Props {
  type: InstructionType
  live: boolean
}

const W = 54
const H = 40
const MID = H / 2

/** IEC-style ladder symbols rendered as SVG. Square, industrial — no rounded UI styling. */
export default function ElementSymbol({ type, live }: Props) {
  const color = live ? 'var(--live)' : 'var(--idle)'
  const meta = INSTRUCTION_MAP[type]
  const sw = 2.5

  const common = {
    stroke: color,
    strokeWidth: sw,
    fill: 'none',
    strokeLinecap: 'round' as const,
  }

  // ---- contacts ----------------------------------------------------------
  if (type === 'NO' || type === 'NC' || type === 'P_CONTACT' || type === 'N_CONTACT') {
    const lx = 16
    const rx = 38
    return (
      <svg className="element-svg" width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        <line x1={0} y1={MID} x2={lx} y2={MID} {...common} />
        <line x1={rx} y1={MID} x2={W} y2={MID} {...common} />
        <line x1={lx} y1={MID - 11} x2={lx} y2={MID + 11} {...common} />
        <line x1={rx} y1={MID - 11} x2={rx} y2={MID + 11} {...common} />
        {type === 'NC' && (
          <line x1={lx + 2} y1={MID + 12} x2={rx - 2} y2={MID - 12} {...common} />
        )}
        {(type === 'P_CONTACT' || type === 'N_CONTACT') && (
          <text
            x={W / 2}
            y={MID + 4}
            textAnchor="middle"
            fontSize="11"
            fontFamily="var(--mono)"
            fontWeight="700"
            fill={color}
          >
            {type === 'P_CONTACT' ? 'P' : 'N'}
          </text>
        )}
      </svg>
    )
  }

  // ---- coils -------------------------------------------------------------
  if (type === 'COIL' || type === 'COIL_NEG' || type === 'SET' || type === 'RST') {
    const lx = 16
    const rx = 38
    const letter = type === 'SET' ? 'S' : type === 'RST' ? 'R' : type === 'COIL_NEG' ? '/' : ''
    return (
      <svg className="element-svg" width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
        <line x1={0} y1={MID} x2={lx} y2={MID} {...common} />
        <line x1={rx} y1={MID} x2={W} y2={MID} {...common} />
        {/* left paren */}
        <path d={`M ${lx} ${MID - 12} Q ${lx - 8} ${MID} ${lx} ${MID + 12}`} {...common} />
        {/* right paren */}
        <path d={`M ${rx} ${MID - 12} Q ${rx + 8} ${MID} ${rx} ${MID + 12}`} {...common} />
        {letter && (
          <text
            x={W / 2}
            y={MID + 4}
            textAnchor="middle"
            fontSize="12"
            fontFamily="var(--mono)"
            fontWeight="700"
            fill={color}
          >
            {letter}
          </text>
        )}
      </svg>
    )
  }

  // ---- boxes (timers / counters / math / compare) ------------------------
  const bw = 64
  return (
    <svg className="element-svg" width={bw + 16} height={H} viewBox={`0 0 ${bw + 16} ${H}`}>
      <line x1={0} y1={MID} x2={8} y2={MID} {...common} />
      <line x1={bw + 8} y1={MID} x2={bw + 16} y2={MID} {...common} />
      <rect x={8} y={4} width={bw} height={H - 8} stroke={color} strokeWidth={sw} fill="var(--surface-2)" />
      <text
        x={(bw + 16) / 2}
        y={MID + 4}
        textAnchor="middle"
        fontSize="12"
        fontFamily="var(--mono)"
        fontWeight="700"
        fill={live ? 'var(--live)' : 'var(--text)'}
      >
        {meta.symbol.replace(/[[\]]/g, '')}
      </text>
    </svg>
  )
}
