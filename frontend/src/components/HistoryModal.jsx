import { useEffect, useMemo, useState } from 'react'
import EditorModal from './EditorModal.jsx'

/**
 * AI-Tutor-style history modal.
 *
 *   <HistoryModal isOpen={open} onClose={...} onLoad={(payload)=>...} accent="#a855f7" />
 *
 * Triggered by parent. Opens centered modal with filter bar + card list.
 * Click a card → opens viewer modal with full payload preview + download dropdown.
 */
export default function HistoryModal({ isOpen, onClose, onLoad,
                                       accent = '#a855f7', accentDark = '#6366f1' }) {
  const [items, setItems]   = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState(null)
  const [dateFrom, setFrom] = useState('')
  const [dateTo, setTo]     = useState('')
  const [search, setSearch] = useState('')
  const [active, setActive] = useState(null)  // selected history payload to view

  const reload = async () => {
    setLoading(true); setError(null)
    try {
      const r = await fetch('/api/history')
      const d = await r.json()
      setItems(d.items || [])
    } catch (e) { setError(`Connection error: ${e.message}`) }
    setLoading(false)
  }

  useEffect(() => { if (isOpen) reload() }, [isOpen])

  const filtered = useMemo(() => {
    const fromTs = dateFrom ? Date.parse(dateFrom) / 1000 : null
    const toTs   = dateTo   ? Date.parse(dateTo)   / 1000 + 86400 : null
    const q = search.trim().toLowerCase()
    return items.filter(it => {
      if (fromTs && it.created < fromTs) return false
      if (toTs   && it.created > toTs)   return false
      if (q && !((it.title || '').toLowerCase().includes(q)
              || (it.summary || '').toLowerCase().includes(q))) return false
      return true
    })
  }, [items, dateFrom, dateTo, search])

  const setPreset = (p) => {
    const today = new Date().toISOString().slice(0, 10)
    if (p === 'today') { setFrom(today); setTo(today) }
    else if (p === 'week')  { setFrom(daysAgo(7));  setTo(today) }
    else if (p === 'month') { setFrom(daysAgo(30)); setTo(today) }
    else                    { setFrom(''); setTo('') }
  }

  const openItem = async (id) => {
    const r = await fetch(`/api/history/${id}`)
    if (!r.ok) { alert('Failed to load entry'); return }
    setActive(await r.json())
  }

  const deleteItem = async (id, e) => {
    e?.stopPropagation()
    if (!confirm('Delete this history entry?')) return
    await fetch(`/api/history/${id}`, { method: 'DELETE' })
    reload()
  }

  const clearAll = async () => {
    if (!confirm('Clear ALL history? This cannot be undone.')) return
    await fetch('/api/history', { method: 'DELETE' })
    reload()
  }

  if (!isOpen) return null

  return (
    <>
      <div className="hm-backdrop" onClick={onClose}>
        <div className="hm-modal" onClick={e => e.stopPropagation()}>

          {/* Header */}
          <div className="hm-header"
               style={{ background: `linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%)` }}>
            <div>
              <div className="hm-title">📋 Grading History</div>
              <div className="hm-sub">
                {loading ? 'Loading…' : `${filtered.length} session${filtered.length === 1 ? '' : 's'}`}
              </div>
            </div>
            <div className="hm-head-actions">
              {items.length > 0 && (
                <button className="hm-clear-btn" onClick={clearAll}>Clear all</button>
              )}
              <button className="hm-close" onClick={onClose}>✕</button>
            </div>
          </div>

          {/* Filter bar */}
          <div className="hm-filters">
            <div className="hm-presets">
              <button onClick={() => setPreset('today')} className="hm-preset">Today</button>
              <button onClick={() => setPreset('week')}  className="hm-preset">7d</button>
              <button onClick={() => setPreset('month')} className="hm-preset">30d</button>
              <button onClick={() => setPreset('all')}   className="hm-preset">All</button>
            </div>
            <div className="hm-dates">
              <input type="date" value={dateFrom} onChange={e => setFrom(e.target.value)} className="hm-date" />
              <span>→</span>
              <input type="date" value={dateTo} onChange={e => setTo(e.target.value)} className="hm-date" />
            </div>
            <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                   placeholder="🔎 Search title…" className="hm-search" />
            {(dateFrom || dateTo || search) && (
              <button onClick={() => { setFrom(''); setTo(''); setSearch('') }}
                      className="hm-preset hm-clear">Clear</button>
            )}
          </div>

          {/* List */}
          <div className="hm-list">
            {error && <div className="hm-error">⚠️ {error}</div>}
            {loading ? (
              <div className="hm-empty">Loading history…</div>
            ) : filtered.length === 0 ? (
              <div className="hm-empty">
                <div className="hm-empty-icon">🗂️</div>
                <div className="hm-empty-title">No history yet</div>
                <div className="hm-empty-sub">Grade some sheets and your sessions will appear here.</div>
              </div>
            ) : (
              <div className="hm-cards">
                {filtered.map(it => (
                  <div key={it.id} className="hm-card" onClick={() => openItem(it.id)}>
                    <div className="hm-card-icon">📚</div>
                    <div className="hm-card-body">
                      <div className="hm-card-title">{it.title}</div>
                      <div className="hm-card-sub">{it.summary}</div>
                      <div className="hm-card-date">{formatDateTime(it.created)}</div>
                    </div>
                    <button className="hm-card-del" onClick={(e) => deleteItem(it.id, e)}
                            title="Delete">🗑</button>
                    <div className="hm-card-open" style={{ color: accent }}>Open →</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {active && (
        <HistoryViewer entry={active} accent={accent} accentDark={accentDark}
                       onClose={() => setActive(null)}
                       onLoad={() => { onLoad?.(active); onClose() }} />
      )}
    </>
  )
}


// ─── Viewer modal — shown when a card is clicked ───────────────────────────
function HistoryViewer({ entry, accent, accentDark, onClose, onLoad }) {
  const [fmtOpen, setFmtOpen] = useState(false)
  const [editOpen, setEditOpen] = useState(false)
  const payload = entry.payload || {}
  const results = payload.results || []
  const items = results.filter(r => r.ok).map(r => ({
    text: r.extracted_text || r.transcript || '',
    meta: {
      title:       r.file || r.student_name || 'Transcript',
      student:     r.student_name,
      file:        r.file,
      grade:       r.detected_scope?.grade,
      subject:     r.detected_scope?.subject,
      chapter:     r.detected_scope?.chapter,
      marks:       r.marks_awarded,
      marks_total: r.marks_total,
    },
  }))

  const download = async (fmt) => {
    setFmtOpen(false)
    if (fmt === 'txt' || fmt === 'md' || fmt === 'json') {
      const text = fmt === 'json'
        ? JSON.stringify(payload, null, 2)
        : items.map((it, i) => formatPlain(it, i, fmt)).join('\n\n')
      saveBlob(text, `history_${entry.id}.${fmt}`,
        fmt === 'json' ? 'application/json' : 'text/plain;charset=utf-8')
      return
    }
    // pdf / docx via backend
    const resp = await fetch(`/api/transcript/${fmt}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items, filename: `history_${entry.id}.${fmt}` }),
    })
    if (!resp.ok) { alert(`Export failed: HTTP ${resp.status}`); return }
    saveBlob(await resp.blob(), `history_${entry.id}.${fmt}`)
  }

  return (
    <div className="hm-backdrop hm-viewer-backdrop" onClick={onClose} style={{ zIndex: 1600 }}>
      <div className="hm-modal hm-viewer" onClick={e => e.stopPropagation()}>
        <div className="hm-header"
             style={{ background: `linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%)` }}>
          <div>
            <div className="hm-title">💬 {entry.title}</div>
            <div className="hm-sub">{entry.summary} · {formatDateTime(entry.created)}</div>
          </div>
          <div className="hm-head-actions">
            <div className="hm-dl-wrap">
              <button className="hm-dl-btn" onClick={() => setFmtOpen(o => !o)}>
                ⬇ Download ▾
              </button>
              {fmtOpen && (
                <div className="hm-dl-menu" onClick={e => e.stopPropagation()}>
                  {[
                    { id: 'txt',  label: 'Text (.txt)',     icon: '📄' },
                    { id: 'md',   label: 'Markdown (.md)',  icon: '📝' },
                    { id: 'json', label: 'JSON (.json)',    icon: '🧾' },
                    { id: 'pdf',  label: 'PDF (.pdf)',      icon: '📕' },
                    { id: 'docx', label: 'Word (.docx)',    icon: '📘' },
                  ].map(f => (
                    <button key={f.id} className="hm-dl-item" onClick={() => download(f.id)}>
                      <span>{f.icon}</span><span>{f.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button className="hm-load-btn" onClick={() => setEditOpen(true)}>📝 Open in editor</button>
            <button className="hm-close" onClick={onClose}>✕</button>
          </div>
        </div>

        <div className="hm-viewer-body">
          <div className="hm-stats">
            <span className="hm-stat-chip">
              <b>{payload.graded ?? results.length}</b>/<b>{payload.count ?? results.length}</b> graded
            </span>
            {payload.class_analytics?.average_percentage !== undefined && (
              <span className="hm-stat-chip">Avg <b>{payload.class_analytics.average_percentage}%</b></span>
            )}
          </div>
          <div className="hm-results-list">
            {results.map((r, i) => (
              <div key={i} className={`hm-res ${r.ok ? '' : 'hm-res-err'}`}>
                <div className="hm-res-num">{i + 1}</div>
                <div className="hm-res-body">
                  <div className="hm-res-title">
                    {r.file || `Result ${i + 1}`}
                    {r.student_name && <span className="hm-res-stu"> · {r.student_name}</span>}
                  </div>
                  {r.detected_scope && (
                    <div className="hm-res-sub">
                      G{r.detected_scope.grade} · {r.detected_scope.subject}
                      {r.detected_scope.chapter ? ` · ${r.detected_scope.chapter}` : ''}
                    </div>
                  )}
                  {r.ok && (typeof r.marks_awarded === 'number') && (
                    <div className="hm-res-marks">{r.marks_awarded}/{r.marks_total}</div>
                  )}
                  {!r.ok && <div className="hm-res-err-txt">⚠ {r.error}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <EditorModal isOpen={editOpen} onClose={() => setEditOpen(false)}
                   title={entry.title || 'Document'}
                   items={items}
                   accent={accent} accentDark={accentDark} />
    </div>
  )
}


function formatDateTime(unix) {
  if (!unix) return ''
  const d = new Date(unix * 1000)
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

function daysAgo(n) {
  const d = new Date(); d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

function formatPlain(item, i, fmt) {
  const m = item.meta || {}
  const sep = fmt === 'md' ? '---' : '═══════════════════════════════════════════'
  const h2  = fmt === 'md' ? `## ${m.title || `Transcript ${i + 1}`}` : `${m.title || `Transcript ${i + 1}`}`
  const meta = [
    m.student   && `Student: ${m.student}`,
    m.file      && `File: ${m.file}`,
    m.grade     && `Grade: ${m.grade}`,
    m.subject   && `Subject: ${m.subject}`,
    m.chapter   && `Chapter: ${m.chapter}`,
    (m.marks !== undefined) && `Marks: ${m.marks}/${m.marks_total}`,
  ].filter(Boolean).join(' · ')
  return `${sep}\n${h2}\n${meta}\n${sep}\n\n${item.text || ''}`
}

function saveBlob(content, filename, mime) {
  const blob = (content instanceof Blob)
    ? content : new Blob([content], { type: mime || 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
