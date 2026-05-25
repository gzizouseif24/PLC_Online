import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import type {
  CounterValue,
  TimerValue,
  Variable,
  VarType,
  VarValue,
} from '../types/simulator'
import { makeVariable } from '../logic/factory'

interface Props {
  variables: Variable[]
  onAdd: (v: Variable) => void
  onRename: (id: string, name: string) => void
  onSetValue: (id: string, value: VarValue) => void
  onDelete: (id: string) => void
}

const TYPES: VarType[] = ['BOOL', 'INT', 'TIMER', 'COUNTER']

export default function VariablePanel({
  variables,
  onAdd,
  onRename,
  onSetValue,
  onDelete,
}: Props) {
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [newType, setNewType] = useState<VarType>('BOOL')

  const submitAdd = () => {
    const name = newName.trim() || `Var_${variables.length + 1}`
    onAdd(makeVariable(name, newType))
    setNewName('')
    setNewType('BOOL')
    setAdding(false)
  }

  return (
    <aside className="panel panel-right">
      <div className="panel-header">
        Variables
        <button className="add-var-btn" onClick={() => setAdding((a) => !a)}>
          <Plus size={13} /> Add
        </button>
      </div>

      {adding && (
        <div className="var-row" style={{ background: 'var(--surface-2)' }}>
          <input
            className="num-input"
            style={{ width: '100%' }}
            placeholder="name"
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitAdd()}
          />
          <div className="var-row-bottom">
            <select
              className="num-input"
              style={{ width: 110 }}
              value={newType}
              onChange={(e) => setNewType(e.target.value as VarType)}
            >
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button className="btn primary" style={{ background: 'var(--accent)', color: '#06101f', borderColor: 'var(--accent)' }} onClick={submitAdd}>
              Create
            </button>
          </div>
        </div>
      )}

      {variables.map((v) => (
        <VarRow
          key={v.id}
          variable={v}
          onRename={onRename}
          onSetValue={onSetValue}
          onDelete={onDelete}
        />
      ))}

      {variables.length === 0 && !adding && (
        <div className="palette-hint">No variables yet.</div>
      )}
    </aside>
  )
}

function VarRow({
  variable,
  onRename,
  onSetValue,
  onDelete,
}: {
  variable: Variable
  onRename: (id: string, name: string) => void
  onSetValue: (id: string, value: VarValue) => void
  onDelete: (id: string) => void
}) {
  const v = variable

  return (
    <div className="var-row">
      <div className="var-row-top">
        <input
          className="var-name"
          value={v.name}
          onChange={(e) => onRename(v.id, e.target.value)}
        />
        <span className={`type-badge ${v.type}`}>{v.type}</span>
        <button className="var-del" onClick={() => onDelete(v.id)} title="Delete variable">
          <X size={14} />
        </button>
      </div>

      <div className="var-row-bottom">
        {v.type === 'BOOL' && (
          <>
            <span className="var-value">{v.value ? 'TRUE' : 'FALSE'}</span>
            <button
              className={`toggle${v.value ? ' on' : ''}`}
              onClick={() => onSetValue(v.id, !(v.value as boolean))}
              title="Toggle"
            />
          </>
        )}

        {v.type === 'INT' && (
          <>
            <span className="var-value">value</span>
            <input
              className="num-input"
              type="number"
              value={Number(v.value)}
              onChange={(e) => onSetValue(v.id, Number(e.target.value))}
            />
          </>
        )}

        {v.type === 'TIMER' && <TimerRow v={v} onSetValue={onSetValue} />}
        {v.type === 'COUNTER' && <CounterRow v={v} onSetValue={onSetValue} />}
      </div>
    </div>
  )
}

function TimerRow({ v, onSetValue }: { v: Variable; onSetValue: (id: string, value: VarValue) => void }) {
  const t = v.value as TimerValue
  return (
    <>
      <span className="var-value">
        <span style={{ color: t.DN ? 'var(--live)' : 'var(--text-faint)' }}>●</span> {t.ET}/
        <input
          className="num-input"
          style={{ width: 64 }}
          type="number"
          value={t.PT}
          onChange={(e) => onSetValue(v.id, { ...t, PT: Number(e.target.value) })}
        />
        ms
      </span>
    </>
  )
}

function CounterRow({ v, onSetValue }: { v: Variable; onSetValue: (id: string, value: VarValue) => void }) {
  const c = v.value as CounterValue
  return (
    <>
      <span className="var-value">
        <span style={{ color: c.Q ? 'var(--live)' : 'var(--text-faint)' }}>●</span> {c.ACC}/
        <input
          className="num-input"
          style={{ width: 56 }}
          type="number"
          value={c.PV}
          onChange={(e) => onSetValue(v.id, { ...c, PV: Number(e.target.value) })}
        />
      </span>
    </>
  )
}
