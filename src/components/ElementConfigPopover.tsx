import { useCallback, useEffect, useState } from 'react'
import type {
  CounterValue,
  Element,
  TimerValue,
  Variable,
  VarType,
  VarValue,
} from '../types/simulator'
import { INSTRUCTION_MAP } from '../logic/instructions'
import { makeVariable } from '../logic/factory'

interface Props {
  element: Element
  variables: Variable[]
  anchor: { x: number; y: number }
  onAddVariable: (v: Variable) => void
  onSetVariableValue: (id: string, value: VarValue) => void
  onConfigure: (elementId: string, varId: string | null, params: Record<string, string | number>) => void
  onClose: () => void
}

/** Dropdown that selects an existing variable of a given type or creates a new one. */
function VarSelect({
  label,
  type,
  value,
  variables,
  onAddVariable,
  onChange,
}: {
  label: string
  type: VarType | VarType[]
  value: string | null
  variables: Variable[]
  onAddVariable: (v: Variable) => void
  onChange: (id: string) => void
}) {
  const types = Array.isArray(type) ? type : [type]
  const options = variables.filter((v) => types.includes(v.type))
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')

  const create = () => {
    const v = makeVariable(name.trim() || 'NewVar', types[0])
    onAddVariable(v)
    onChange(v.id)
    setCreating(false)
    setName('')
  }

  return (
    <div className="field">
      <label>{label}</label>
      {creating ? (
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            autoFocus
            placeholder={`new ${types[0]}`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && create()}
          />
          <button className="btn" onClick={create}>
            Add
          </button>
        </div>
      ) : (
        <select
          value={value ?? ''}
          onChange={(e) => {
            if (e.target.value === '__new__') setCreating(true)
            else onChange(e.target.value)
          }}
        >
          <option value="" disabled>
            select…
          </option>
          {options.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name} ({v.type})
            </option>
          ))}
          <option value="__new__">➕ Create new…</option>
        </select>
      )}
    </div>
  )
}

/** Source that can be an immediate integer or a variable reference. */
function SourceField({
  label,
  value,
  variables,
  onChange,
}: {
  label: string
  value: string | number | undefined
  variables: Variable[]
  onChange: (val: string | number) => void
}) {
  const isImmediate = typeof value === 'number' || value === undefined || value === ''
  const numeric = variables.filter((v) => ['INT', 'TIMER', 'COUNTER'].includes(v.type))

  return (
    <div className="field">
      <label>{label}</label>
      <div style={{ display: 'flex', gap: 6 }}>
        <select
          style={{ flex: 1 }}
          value={isImmediate ? '__imm__' : String(value)}
          onChange={(e) =>
            e.target.value === '__imm__' ? onChange(0) : onChange(e.target.value)
          }
        >
          <option value="__imm__">Immediate</option>
          {numeric.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
        {isImmediate && (
          <input
            style={{ width: 80 }}
            type="number"
            value={typeof value === 'number' ? value : 0}
            onChange={(e) => onChange(Number(e.target.value))}
          />
        )}
      </div>
    </div>
  )
}

export default function ElementConfigPopover({
  element,
  variables,
  anchor,
  onAddVariable,
  onSetVariableValue,
  onConfigure,
  onClose,
}: Props) {
  const meta = INSTRUCTION_MAP[element.type]
  const seedPt = () => {
    const v = variables.find((x) => x.id === element.varId)
    return v?.type === 'TIMER' ? (v.value as TimerValue).PT : 1000
  }
  const seedPv = () => {
    const v = variables.find((x) => x.id === element.varId)
    return v?.type === 'COUNTER' ? (v.value as CounterValue).PV : 5
  }

  const [varId, setVarId] = useState<string | null>(element.varId)
  const [params, setParams] = useState<Record<string, string | number>>({ ...element.params })
  const [pt, setPt] = useState<number>(seedPt)
  const [pv, setPv] = useState<number>(seedPv)

  // when the user picks a different variable, refresh PT/PV from it
  const handleVarChange = (id: string) => {
    setVarId(id)
    const v = variables.find((x) => x.id === id)
    if (v?.type === 'TIMER') setPt((v.value as TimerValue).PT)
    if (v?.type === 'COUNTER') setPv((v.value as CounterValue).PV)
  }

  const setParam = (k: string, v: string | number) => setParams((p) => ({ ...p, [k]: v }))

  const apply = useCallback(() => {
    if (meta.category === 'Timers' && varId) {
      const v = variables.find((x) => x.id === varId)
      if (v?.type === 'TIMER') onSetVariableValue(varId, { ...(v.value as TimerValue), PT: pt })
    }
    if (meta.category === 'Counters' && element.type !== 'RES' && varId) {
      const v = variables.find((x) => x.id === varId)
      if (v?.type === 'COUNTER') onSetVariableValue(varId, { ...(v.value as CounterValue), PV: pv })
    }
    onConfigure(element.id, meta.category === 'Compare' ? null : varId, params)
    onClose()
  }, [meta.category, varId, variables, pt, pv, element.id, element.type, params, onConfigure, onSetVariableValue, onClose])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'Enter') apply()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [apply, onClose])

  const top = Math.min(anchor.y, window.innerHeight - 320)
  const left = Math.min(anchor.x, window.innerWidth - 300)

  return (
    <>
      <div className="popover-backdrop" onClick={onClose} />
      <div className="popover" style={{ top, left }} onClick={(e) => e.stopPropagation()}>
        <h4>
          <span className="glyph">{meta.symbol}</span>
          {meta.label}
        </h4>

        {/* contacts + coils: single BOOL var */}
        {(meta.category === 'Contacts' || meta.category === 'Coils') && (
          <VarSelect
            label="Variable"
            type="BOOL"
            value={varId}
            variables={variables}
            onAddVariable={onAddVariable}
            onChange={handleVarChange}
          />
        )}

        {/* timers */}
        {meta.category === 'Timers' && (
          <>
            <VarSelect
              label="Timer variable"
              type="TIMER"
              value={varId}
              variables={variables}
              onAddVariable={onAddVariable}
              onChange={handleVarChange}
            />
            <div className="field">
              <label>Preset time (ms)</label>
              <input type="number" value={pt} onChange={(e) => setPt(Number(e.target.value))} />
            </div>
          </>
        )}

        {/* counters */}
        {meta.category === 'Counters' && element.type !== 'RES' && (
          <>
            <VarSelect
              label="Counter variable"
              type="COUNTER"
              value={varId}
              variables={variables}
              onAddVariable={onAddVariable}
              onChange={handleVarChange}
            />
            <div className="field">
              <label>Preset value</label>
              <input type="number" value={pv} onChange={(e) => setPv(Number(e.target.value))} />
            </div>
          </>
        )}

        {element.type === 'RES' && (
          <VarSelect
            label="Reset target"
            type={['TIMER', 'COUNTER']}
            value={varId}
            variables={variables}
            onAddVariable={onAddVariable}
            onChange={handleVarChange}
          />
        )}

        {/* math */}
        {meta.category === 'Math' && (
          <>
            <VarSelect
              label="Destination"
              type="INT"
              value={varId}
              variables={variables}
              onAddVariable={onAddVariable}
              onChange={handleVarChange}
            />
            {element.type === 'MOV' ? (
              <SourceField
                label="Source"
                value={params.src ?? params.srcA}
                variables={variables}
                onChange={(v) => setParam('src', v)}
              />
            ) : (
              <>
                <SourceField
                  label="Source A"
                  value={params.srcA}
                  variables={variables}
                  onChange={(v) => setParam('srcA', v)}
                />
                <SourceField
                  label="Source B"
                  value={params.srcB}
                  variables={variables}
                  onChange={(v) => setParam('srcB', v)}
                />
              </>
            )}
          </>
        )}

        {/* compare */}
        {meta.category === 'Compare' && (
          <>
            <SourceField
              label="Source A"
              value={params.srcA}
              variables={variables}
              onChange={(v) => setParam('srcA', v)}
            />
            <SourceField
              label="Source B"
              value={params.srcB}
              variables={variables}
              onChange={(v) => setParam('srcB', v)}
            />
          </>
        )}

        <div className="popover-actions">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" onClick={apply}>
            Apply
          </button>
        </div>
      </div>
    </>
  )
}
