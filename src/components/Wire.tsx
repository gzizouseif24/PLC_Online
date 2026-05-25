export function HWire({ x1, x2, y, live }: { x1: number; x2: number; y: number; live: boolean }) {
  return (
    <div
      className={`seg${live ? ' live' : ''}`}
      style={{ left: Math.min(x1, x2), top: y - 1.5, width: Math.abs(x2 - x1), height: 3 }}
    />
  )
}

export function VWire({ x, y1, y2, live }: { x: number; y1: number; y2: number; live: boolean }) {
  return (
    <div
      className={`seg${live ? ' live' : ''}`}
      style={{ left: x - 1.5, top: Math.min(y1, y2), width: 3, height: Math.abs(y2 - y1) }}
    />
  )
}
