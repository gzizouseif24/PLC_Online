interface Props {
  running: boolean
  scanCount: number
  lastCycleMs: number
  activeOutputs: number
}

export default function StatusBar({
  running,
  scanCount,
  lastCycleMs,
  activeOutputs,
}: Props) {
  return (
    <footer className="statusbar">
      <span className={`status-dot${running ? ' running' : ''}`}>
        <span className="dot" />
        {running ? 'RUNNING' : 'STOPPED'}
      </span>
      <span>
        Scan #{scanCount} | Cycle time: {lastCycleMs}ms
      </span>
      <span>
        Active outputs: {activeOutputs}
      </span>
    </footer>
  )
}
