import { useEffect, useMemo, useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import type { Element, InstructionType } from './types/simulator'
import { useSimulator } from './hooks/useSimulator'
import { useScanCycle } from './hooks/useScanCycle'
import { makeElement } from './logic/factory'
import Toolbar from './components/Toolbar'
import InstructionPalette from './components/InstructionPalette'
import LadderCanvas from './components/LadderCanvas'
import VariablePanel from './components/VariablePanel'
import StatusBar from './components/StatusBar'
import ElementConfigPopover from './components/ElementConfigPopover'

interface ConfigState {
  element: Element
  anchor: { x: number; y: number }
}
interface CtxState {
  element: Element
  x: number
  y: number
}

export default function App() {
  const [state, dispatch] = useSimulator()
  useScanCycle(state, dispatch)

  const [selectedRungId, setSelectedRungId] = useState<string | null>(
    () => state.rungs[0]?.id ?? null,
  )
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null)
  const [branchTarget, setBranchTarget] = useState<{ rungId: string; elementId: string } | null>(null)
  const [config, setConfig] = useState<ConfigState | null>(null)
  const [ctx, setCtx] = useState<CtxState | null>(null)

  const effectiveRungId = useMemo(() => {
    if (selectedRungId && state.rungs.some((r) => r.id === selectedRungId)) return selectedRungId
    return state.rungs[0]?.id ?? null
  }, [selectedRungId, state.rungs])

  const activeOutputs = useMemo(
    () => state.rungs.reduce((n, r) => n + r.outputs.filter((o) => o.live).length, 0),
    [state.rungs],
  )

  const openConfig = (element: Element, anchor: { x: number; y: number }) =>
    setConfig({ element, anchor })

  const handlePick = (instr: InstructionType) => {
    const el = makeElement(instr)
    const centerAnchor = { x: window.innerWidth / 2 - 140, y: 110 }

    if (branchTarget) {
      dispatch({
        type: 'ADD_PARALLEL',
        rungId: branchTarget.rungId,
        targetElementId: branchTarget.elementId,
        element: el,
      })
      setBranchTarget(null)
      setSelectedElementId(el.id)
      openConfig(el, centerAnchor)
      return
    }

    if (!effectiveRungId) return
    dispatch({
      type: 'ADD_INSTRUCTION',
      rungId: effectiveRungId,
      element: el,
      afterElementId: selectedElementId,
    })
    setSelectedRungId(effectiveRungId)
    setSelectedElementId(el.id)
    openConfig(el, centerAnchor)
  }

  // keyboard: Delete removes selected element, else selected rung
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete') return
      const tag = (document.activeElement?.tagName ?? '').toLowerCase()
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return
      if (config) return
      if (selectedElementId) {
        dispatch({ type: 'DELETE_ELEMENT', elementId: selectedElementId })
        setSelectedElementId(null)
      } else if (effectiveRungId) {
        dispatch({ type: 'DELETE_RUNG', rungId: effectiveRungId })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selectedElementId, effectiveRungId, config, dispatch])

  // close context menu on any outside click
  useEffect(() => {
    if (!ctx) return
    const close = () => setCtx(null)
    window.addEventListener('click', close)
    return () => window.removeEventListener('click', close)
  }, [ctx])

  return (
    <div className="app">
      <Toolbar
        running={state.running}
        scanInterval={state.scanInterval}
        scanCount={state.scanCount}
        onToggleRun={() => dispatch({ type: 'SET_RUNNING', running: !state.running })}
        onSetInterval={(ms) => dispatch({ type: 'SET_INTERVAL', interval: ms })}
      />

      <div className="app-main">
        <InstructionPalette
          branchMode={branchTarget !== null}
          hasTarget={effectiveRungId !== null}
          onPick={handlePick}
        />

        <LadderCanvas
          rungs={state.rungs}
          variables={state.variables}
          selectedRungId={effectiveRungId}
          selectedElementId={selectedElementId}
          onSelectRung={(id) => {
            setSelectedRungId(id)
            setSelectedElementId(null)
            setBranchTarget(null)
          }}
          onSelectElement={(el, rungId) => {
            setSelectedRungId(rungId)
            setSelectedElementId(el.id)
            setBranchTarget(null)
          }}
          onContextMenu={(el, _rungId, _region, x, y) => {
            setSelectedElementId(el.id)
            setCtx({ element: el, x, y })
          }}
          onAddBranch={(rungId, elementId) => setBranchTarget({ rungId, elementId })}
          onDeleteRung={(rungId) => {
            dispatch({ type: 'DELETE_RUNG', rungId })
            if (selectedRungId === rungId) setSelectedRungId(null)
          }}
          onAddRung={() => dispatch({ type: 'ADD_RUNG' })}
          onClearSelection={() => {
            setSelectedElementId(null)
            setBranchTarget(null)
          }}
        />

        <VariablePanel
          variables={state.variables}
          onAdd={(v) => dispatch({ type: 'ADD_VARIABLE', variable: v })}
          onRename={(id, name) => dispatch({ type: 'RENAME_VARIABLE', id, name })}
          onSetValue={(id, value) => dispatch({ type: 'SET_VARIABLE_VALUE', id, value })}
          onDelete={(id) => dispatch({ type: 'DELETE_VARIABLE', id })}
        />
      </div>

      <StatusBar
        running={state.running}
        scanCount={state.scanCount}
        lastCycleMs={state.lastCycleMs}
        activeOutputs={activeOutputs}
      />

      {config && (
        <ElementConfigPopover
          element={config.element}
          variables={state.variables}
          anchor={config.anchor}
          onAddVariable={(v) => dispatch({ type: 'ADD_VARIABLE', variable: v })}
          onSetVariableValue={(id, value) => dispatch({ type: 'SET_VARIABLE_VALUE', id, value })}
          onConfigure={(elementId, varId, params) =>
            dispatch({ type: 'CONFIGURE_ELEMENT', elementId, varId, params })
          }
          onClose={() => setConfig(null)}
        />
      )}

      {ctx && (
        <div
          className="ctx-menu"
          style={{
            top: Math.min(ctx.y, window.innerHeight - 100),
            left: Math.min(ctx.x, window.innerWidth - 160),
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            className="ctx-item"
            onClick={() => {
              openConfig(ctx.element, { x: ctx.x, y: ctx.y })
              setCtx(null)
            }}
          >
            <Pencil size={13} /> Edit properties
          </button>
          <button
            className="ctx-item danger"
            onClick={() => {
              dispatch({ type: 'DELETE_ELEMENT', elementId: ctx.element.id })
              if (selectedElementId === ctx.element.id) setSelectedElementId(null)
              setCtx(null)
            }}
          >
            <Trash2 size={13} /> Delete
          </button>
        </div>
      )}
    </div>
  )
}
