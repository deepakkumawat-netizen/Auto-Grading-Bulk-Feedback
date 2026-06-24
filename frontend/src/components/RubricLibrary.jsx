import { useEffect, useState } from 'react'

export default function RubricLibrary({ scope, currentRubric, onLoad }) {
  const [list, setList] = useState([])
  const [name, setName] = useState('')
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

  const refresh = () =>
    fetch('/api/rubric').then(r => r.json()).then(d => setList(d.rubrics || [])).catch(() => {})

  useEffect(() => { refresh() }, [])

  const save = async () => {
    if (!name.trim()) { alert('Name your rubric first.'); return }
    if (!currentRubric?.trim()) { alert('Rubric text is empty.'); return }
    setBusy(true)
    try {
      await fetch('/api/rubric', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, grade: scope.grade, subject: scope.subject,
          chapter: scope.chapter || '', rubric: currentRubric,
        }),
      })
      setName('')
      await refresh()
    } finally { setBusy(false) }
  }

  const load = async (id) => {
    const r = await fetch(`/api/rubric/${id}`).then(r => r.json())
    onLoad?.(r.rubric)
  }

  const remove = async (id) => {
    if (!confirm('Delete this rubric?')) return
    await fetch(`/api/rubric/${id}`, { method: 'DELETE' })
    refresh()
  }

  return (
    <div className="rubric-lib">
      <div className="rubric-lib-head">
        <button onClick={() => setOpen(o => !o)} className="small">
          {open ? '▼' : '▶'} 📚 Saved rubrics ({list.length})
        </button>
      </div>

      {open && (
        <div className="rubric-lib-body">
          <div className="row">
            <input className="lib-name" placeholder="Name this rubric…"
                   value={name} onChange={e => setName(e.target.value)} disabled={busy}/>
            <button onClick={save} disabled={busy}>💾 Save current</button>
          </div>

          {list.length === 0 && <div className="muted">No saved rubrics yet.</div>}

          <ul className="lib-list">
            {list.map(r => (
              <li key={r.id}>
                <div>
                  <b>{r.name}</b>
                  <span className="muted"> · Grade {r.grade} · {r.subject}
                    {r.chapter ? ` · ${r.chapter}` : ''}</span>
                </div>
                <div className="lib-actions">
                  <button className="small" onClick={() => load(r.id)}>↩ Load</button>
                  <button className="small danger" onClick={() => remove(r.id)}>🗑</button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
