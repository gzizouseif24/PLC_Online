import { Cpu, Play, Square, Gauge } from 'lucide-react'

interface Props {
  running: boolean
  scanInterval: number
  scanCount: number
  onToggleRun: () => void
  onSetInterval: (ms: number) => void
}

export default function Toolbar({
  running,
  scanInterval,
  scanCount,
  onToggleRun,
  onSetInterval,
}: Props) {
  return (
    <header className="toolbar">
      <div className="brand">
        <span className="brand-icon">
          <Cpu size={18} />
        </span>
        Ladder<span className="accent">Sim</span>
      </div>

      <div className="toolbar-center">
        <button
          className={`run-btn ${running ? 'running' : 'stopped'}`}
          onClick={onToggleRun}
        >
          {running ? <Square size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
          {running ? 'Stop' : 'Run'}
        </button>
      </div>

      <div className="toolbar-right">
        <div className="speed-control">
          <Gauge size={15} />
          <input
            type="range"
            min={50}
            max={1000}
            step={50}
            value={scanInterval}
            onChange={(e) => onSetInterval(Number(e.target.value))}
          />
          <span className="speed-val">{scanInterval}ms</span>
        </div>
        <div className="scan-pill">
          scan <b>#{scanCount}</b>
        </div>
      </div>
    </header>
  )
}
