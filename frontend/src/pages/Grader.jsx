import { Fragment, useEffect, useMemo, useState } from 'react'
import ClassAnalytics from '../components/ClassAnalytics.jsx'
import RubricLibrary from '../components/RubricLibrary.jsx'
import HistoryModal from '../components/HistoryModal.jsx'
import Button from '../ui/Button.jsx'
import Card from '../ui/Card.jsx'
import FileDropzone from '../ui/FileDropzone.jsx'
import Skeleton from '../ui/Skeleton.jsx'
import ThemeToggle from '../ui/ThemeToggle.jsx'
import { useApi } from '../hooks/useApi.js'
import { useToast } from '../hooks/useToast.js'

const RUBRIC_PLACEHOLDER = `Type the question marking scheme — e.g.

Q1 (2 marks): What to look for...
Q2 (3 marks): Required working / key concept...
Q3 (5 marks): Expected explanation + example...`

export default function Grader({ onHome }) {
  const { push } = useToast()
  const [rubric, setRubric] = useState('')
  const [files, setFiles]   = useState([])
  const [verify, setVerify] = useState(true)
  const [health, setHealth] = useState(null)

  // Rubric source: 'manual' (default) or 'paper' (upload a question paper, AI generates rubric)
  const [rubricMode, setRubricMode] = useState('manual')
  const [paperFiles, setPaperFiles] = useState([])
  const [paperBusy, setPaperBusy]   = useState(false)
  const [paperMeta, setPaperMeta]   = useState(null)   // {questions_found, total_marks, extracted_text}

  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(setHealth).catch(() => setHealth({}))
  }, [])

  // Question paper → AI rubric
  const onPaperUpload = async (incoming) => {
    setPaperFiles(incoming)
    const f = incoming[0]
    if (!f) return
    setPaperBusy(true); setPaperMeta(null)
    try {
      const fd = new FormData()
      fd.append('paper', f, f.name)
      const r = await fetch('/api/rubric/from-paper', { method: 'POST', body: fd })
      if (!r.ok) {
        const txt = await r.text()
        throw new Error(`HTTP ${r.status}: ${txt.slice(0, 200)}`)
      }
      const data = await r.json()
      setRubric(data.rubric || '')
      setPaperMeta({
        questions_found: data.questions_found,
        total_marks:     data.total_marks,
      })
      push({
        kind:  'success',
        title: '✨ Rubric generated from question paper',
        body:  `${data.questions_found} question${data.questions_found === 1 ? '' : 's'} found · total ${data.total_marks} marks`,
      })
    } catch (e) {
      push({ kind: 'error', title: 'Rubric generation failed', body: String(e.message || e) })
    } finally {
      setPaperBusy(false)
    }
  }

  // Accept all supported file types (PDFs, images, txt)
  const handleFilesChange = (incoming) => {
    setFiles(incoming)
  }

  const bulk = useApi(async (signal) => {
    const fd = new FormData()
    fd.append('rubric', rubric)
    fd.append('verify', String(verify))
    files.forEach(f => fd.append('files', f, f.name))
    const r = await fetch('/api/grade/bulk', { method: 'POST', body: fd, signal })
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`)
    return r.json()
  })

  // Keyboard shortcut: Cmd/Ctrl + Enter submits
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !bulk.loading) submit()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files, rubric, verify, bulk.loading])

  const submit = async () => {
    if (!files.length) { push({ kind: 'error', title: 'Add answer sheets', body: 'Drop a few files first.' }); return }
    if (!rubric.trim()) { push({ kind: 'error', title: 'Rubric missing', body: 'Type or load a rubric.' }); return }
    try {
      const r = await bulk.run()
      const flagged = r?.results?.filter(x => x.needs_review).length || 0
      push({
        kind: 'success',
        title: `Graded ${r.graded}/${r.count}`,
        body: flagged > 0 ? `Verifier flagged ${flagged} for review` : `Class average: ${r.class_analytics.average_percentage}%`,
      })
    } catch (e) {
      push({ kind: 'error', title: 'Grading failed', body: String(e.message || e) })
    }
  }

  const downloadCSV = async () => {
    const r = await fetch('/api/export/csv', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ results: bulk.data?.results }),
    })
    triggerDownload(await r.blob(), 'grades.csv')
    push({ kind: 'success', title: 'CSV downloaded' })
  }

  const downloadZip = async () => {
    const r = await fetch('/api/feedback/zip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ results: bulk.data?.results, meta: {} }),
    })
    triggerDownload(await r.blob(), 'feedback.zip')
    push({ kind: 'success', title: 'Feedback ZIP downloaded' })
  }

  const downloadAllTranscripts = () => {
    if (!bulk.data?.results) return
    const out = bulk.data.results
      .filter(r => r.ok && (r.extracted_text || '').trim())
      .map((r, i) => {
        const header = `═══════════════════════════════════════════\n` +
                       `Sheet ${i + 1}: ${r.file || 'untitled'}\n` +
                       `Student: ${r.student_name || '(unknown)'}\n` +
                       `Detected: G${r.detected_scope?.grade} · ${r.detected_scope?.subject || ''}` +
                       (r.detected_scope?.chapter ? ` · ${r.detected_scope.chapter}` : '') + `\n` +
                       `Marks: ${r.marks_awarded}/${r.marks_total} (${r.percentage}%)\n` +
                       `═══════════════════════════════════════════\n\n`
        return header + r.extracted_text + '\n\n'
      })
      .join('')
    const blob = new Blob([out], { type: 'text/plain;charset=utf-8' })
    triggerDownload(blob, 'all_transcripts.txt')
    push({ kind: 'success', title: 'All transcripts downloaded' })
  }

  const downloadOneTranscript = (result) => {
    const name = (result.student_name || result.file || 'student').replace(/[^a-z0-9 _-]/gi, '')
    const blob = new Blob([result.extracted_text || ''], { type: 'text/plain;charset=utf-8' })
    triggerDownload(blob, `${name}_transcript.txt`)
  }

  const downloadOnePdf = async (result) => {
    const r = await fetch('/api/feedback/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        result,
        meta: {
          grade:   result.grade_used,
          subject: result.subject_used,
          chapter: result.chapter_used,
          file:    result.file,
        },
      }),
    })
    const name = (result.student_name || result.file || 'student').replace(/[^a-z0-9 _-]/gi, '')
    triggerDownload(await r.blob(), `${name}.pdf`)
  }

  const [openRow, setOpenRow] = useState(null)
  const toggleRow = (i) => setOpenRow(prev => (prev === i ? null : i))

  // History modal
  const [histOpen, setHistOpen] = useState(false)
  const [histCount, setHistCount] = useState(0)
  useEffect(() => {
    fetch('/api/history').then(r => r.json()).then(d => setHistCount((d.items || []).length)).catch(() => {})
  }, [bulk.data])

  const loadFromHistory = (entry) => {
    bulk.setData(entry.payload)
    setHistOpen(false)
    push({ kind: 'success', title: 'Loaded from history', body: entry.title })
  }

  const missingGroq = health && !health.groq_configured
  const report = bulk.data
  const canSubmit = !bulk.loading && files.length > 0 && rubric.trim()
  const flaggedCount = useMemo(
    () => report?.results?.filter(r => r.needs_review).length || 0,
    [report]
  )

  return (
    <div className="app-wrap">
      <header className="app-bar">
        <button className="brand" onClick={onHome}>
          <span className="brand-mark">📝</span>
          <span className="brand-name">Auto-Grading &amp; Bulk Feedback</span>
        </button>
        <div className="app-bar-right">
          <span className="muted">AI grades against a rubric · Powered By Codevidhya</span>
          <button className="hist-btn" onClick={() => setHistOpen(true)}>
            <span>🗂</span><span>History</span>
            {histCount > 0 && <span className="hist-btn-badge">{histCount}</span>}
          </button>
          <ThemeToggle size="sm" />
          <Button variant="ghost" size="sm" onClick={onHome}>← Home</Button>
        </div>
      </header>

      {missingGroq && (
        <div className="banner">⚠️ GROQ_API_KEY missing — edit backend/.env and restart.</div>
      )}

      <main className="page">
        <div className="auto-detect-info">
          <span className="ad-icon">🪄</span>
          <div>
            <b>No scope selection needed.</b> AI auto-detects each student's
            grade, subject and chapter from the answer itself.
          </div>
        </div>

        <Card>
          <Card.Header
            eyebrow="Step 1"
            title="Rubric"
            hint={rubricMode === 'manual'
              ? 'Type the question marking scheme yourself'
              : 'Upload the question paper — AI reads it and writes the rubric for you'} />
          <Card.Body>
            <div className="rubric-tabs" role="tablist">
              <button role="tab" aria-selected={rubricMode === 'manual'}
                      className={`rubric-tab ${rubricMode === 'manual' ? 'active' : ''}`}
                      onClick={() => setRubricMode('manual')}>
                ✍️ Type manually
              </button>
              <button role="tab" aria-selected={rubricMode === 'paper'}
                      className={`rubric-tab ${rubricMode === 'paper' ? 'active' : ''}`}
                      onClick={() => setRubricMode('paper')}>
                📑 Upload question paper
              </button>
            </div>

            {rubricMode === 'paper' && (
              <>
                <FileDropzone value={paperFiles} onChange={onPaperUpload}
                              accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                              label={paperBusy ? '⏳ Reading question paper…' : 'Drop question paper here (PDF / JPG / PNG / TXT)'}
                              hint="AI will extract each question, detect marks, and write a marking rubric for you." />
                {paperMeta && (
                  <div className="paper-meta-pill">
                    ✨ Generated: <b>{paperMeta.questions_found} questions</b> ·
                    total <b>{paperMeta.total_marks} marks</b> · review and edit below if needed
                  </div>
                )}
              </>
            )}

            <textarea className="rubric" rows={8} value={rubric}
                      placeholder={rubricMode === 'paper'
                        ? 'Generated rubric will appear here. You can edit it freely before grading.'
                        : RUBRIC_PLACEHOLDER}
                      onChange={e => setRubric(e.target.value)}
                      disabled={bulk.loading || paperBusy}/>
            <RubricLibrary scope={{ grade: 0, subject: '', chapter: '' }} currentRubric={rubric} onLoad={setRubric} />
          </Card.Body>
        </Card>

        <Card>
          <Card.Header eyebrow="Step 2" title="Answer sheets"
                       hint="Upload photos, scans or PDFs of answer sheets" />
          <Card.Body>
            <FileDropzone value={files} onChange={handleFilesChange} multiple
                          accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                          label="Drop answer sheets here (PDF / JPG / PNG / TXT)"
                          hint="Multi-page PDFs are read with pypdf; image scans are OCR'd via Gemini Vision." />
            <label className="verify-toggle">
              <input type="checkbox" checked={verify}
                     onChange={e => setVerify(e.target.checked)} disabled={bulk.loading}/>
              <div>
                <div className="vt-title">🔍 Run Verifier Agent</div>
                <div className="vt-sub">A second AI reviews every grade — flags over-generous marks.</div>
              </div>
            </label>
          </Card.Body>
        </Card>

        <div className="actions">
          <Button variant="primary" size="lg" loading={bulk.loading} disabled={!canSubmit}
                  onClick={submit} icon="🚀">
            {bulk.loading ? 'Grading…' : 'Grade all'}
          </Button>
          <span className="kbd-hint">⌘/Ctrl + ⏎ to submit</span>
          {report && <Button onClick={downloadCSV} icon="⬇️">CSV</Button>}
          {report && <Button onClick={downloadZip} icon="📦">Feedback PDFs (ZIP)</Button>}
          {report && <Button onClick={downloadAllTranscripts} icon="📥">All transcripts (.txt)</Button>}
        </div>

        {bulk.loading && (
          <Card>
            <Card.Header eyebrow="Working" title="AI is grading…"
                         hint={`${files.length} answer${files.length > 1 ? 's' : ''} in flight`} />
            <Card.Body>
              <Skeleton h={20} count={4} />
            </Card.Body>
          </Card>
        )}

        {report && (
          <section className="results">
            <ClassAnalytics results={report.results} analytics={report.class_analytics} />

            <h3 className="results-h">📋 Student results
              {flaggedCount > 0 && <span className="results-flag">  ·  {flaggedCount} flagged by verifier</span>}
            </h3>
            <div className="table-wrap">
              <table className="table table-expand">
                <thead>
                  <tr>
                    <th aria-label="expand" style={{width: 28}}></th>
                    <th>File</th><th>Student</th><th>Detected</th><th>Marks</th><th>%</th>
                    <th>Verifier</th><th>Top mistake</th><th>PDF</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((r, i) => {
                    const isOpen = openRow === i
                    const ds = r.detected_scope
                    return (
                      <Fragment key={i}>
                        <tr className={`${r.ok ? (r.needs_review ? 'row-flag' : '') : 'row-err'} ${isOpen ? 'row-open' : ''} row-clickable`}
                            onClick={() => r.ok && toggleRow(i)}>
                          <td>
                            {r.ok && (
                              <span className={`row-caret ${isOpen ? 'open' : ''}`} aria-hidden="true">▸</span>
                            )}
                          </td>
                          <td>{r.file}</td>
                          <td>{r.student_name || ''}</td>
                          <td>
                            {ds ? (
                              <span className="detected-pill" title={ds.reason || ''}>
                                G{ds.grade} · {ds.subject}{ds.chapter ? ` · ${ds.chapter}` : ''}
                              </span>
                            ) : ''}
                          </td>
                          <td>{r.ok ? `${r.marks_awarded}/${r.marks_total}` : '—'}</td>
                          <td>{r.ok ? `${r.percentage}%` : ''}</td>
                          <td>{r.verifier
                                ? (r.verifier.agrees ? `✓ ${r.verifier.confidence ?? ''}%` : `⚠ suggests ${r.verifier.suggested_marks}`)
                                : ''}</td>
                          <td>
                            {r.ok ? (
                              <>
                                {r.pre_flight?.warnings?.length > 0 && (
                                  <span title={r.pre_flight.warnings.map(w => w.message).join('\n')}
                                        style={{marginRight:6}}>⚠</span>
                                )}
                                {r.mistakes?.[0]?.type || ''}
                              </>
                            ) : (
                              <span className="err-text">{r.error}</span>
                            )}
                          </td>
                          <td onClick={e => e.stopPropagation()}>
                            {r.ok && <Button size="sm" onClick={() => downloadOnePdf(r)}>📄</Button>}
                          </td>
                        </tr>
                        {isOpen && r.ok && (
                          <tr className="row-feedback">
                            <td colSpan={9}>
                              <FeedbackPanel result={r}
                                             onDownloadTranscript={() => downloadOneTranscript(r)} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>

      <HistoryModal isOpen={histOpen} onClose={() => setHistOpen(false)}
                    onLoad={loadFromHistory}
                    accent="#a855f7" accentDark="#6366f1" />
    </div>
  )
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}

function FeedbackPanel({ result, onDownloadTranscript }) {
  const v   = result.verifier
  const mc  = result.math_check
  const sp  = result.study_plan
  const tx  = result.extracted_text
  const pf  = result.pre_flight
  return (
    <div className="feedback-panel stagger-in">
      {pf && (pf.warnings?.length > 0 || pf.info?.length > 0) && (
        <div className="fp-block fp-preflight">
          <div className="fp-label">🛡 Pre-flight checks</div>
          {pf.warnings?.map((w, i) => (
            <div className="pf-row pf-warn" key={`w${i}`}>⚠ <b>{w.code}</b>: {w.message}</div>
          ))}
          {pf.info?.map((it, i) => (
            <div className="pf-row pf-info" key={`i${i}`}>ℹ <b>{it.code}</b>: {it.message}</div>
          ))}
        </div>
      )}
      {result.suggestion && (
        <div className="fp-block fp-suggestion">
          <div className="fp-label">💡 Suggestion for the student</div>
          <p>{result.suggestion}</p>
        </div>
      )}

      {/* 📚 Personalised study plan */}
      {sp?.length > 0 && (
        <div className="fp-block fp-studyplan">
          <div className="fp-label">📚 Personalised study plan</div>
          <ol className="fp-plan">
            {sp.map((step, i) => <li key={i}>{step}</li>)}
          </ol>
        </div>
      )}

      {/* 🔧 Math verifier — only show if errors were caught */}
      {mc?.errors?.length > 0 && (
        <div className="fp-block fp-mathcheck">
          <div className="fp-label">🔧 Math verifier caught calculation error(s)</div>
          <ul>
            {mc.errors.slice(0, 5).map((err, i) => (
              <li key={i}>
                <code>{err.expression}</code> — student claimed <b>{err.claimed}</b>,
                correct answer is <b>{err.correct}</b>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="fp-grid">
        {result.strengths?.length > 0 && (
          <div className="fp-block fp-strengths">
            <div className="fp-label">✓ Strengths</div>
            <ul>{result.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul>
          </div>
        )}

        {result.mistakes?.length > 0 && (
          <div className="fp-block fp-mistakes">
            <div className="fp-label">✗ Areas to improve</div>
            <ul>
              {result.mistakes.map((m, i) => (
                <li key={i}><b>{m.type}:</b> {m.description}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {result.per_question?.length > 0 && (
        <div className="fp-block">
          <div className="fp-label">📋 Per-question feedback</div>
          <table className="fp-table">
            <thead>
              <tr><th>Question</th><th>Marks</th><th>Feedback</th></tr>
            </thead>
            <tbody>
              {result.per_question.map((q, i) => (
                <tr key={i}>
                  <td className="fp-q">{q.q}</td>
                  <td className="fp-m">{q.marks_awarded}/{q.marks_total}</td>
                  <td className="fp-f">{q.feedback}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {v && !v.agrees && v.comment && (
        <div className="fp-block fp-verifier">
          <div className="fp-label">🔍 Verifier note · suggests {v.suggested_marks}/{result.marks_total}</div>
          <p>{v.comment}</p>
        </div>
      )}

      {/* 📝 Extracted transcript — what the AI actually read from the sheet */}
      {tx && (
        <details className="fp-transcript">
          <summary>📝 Extracted transcript ({tx.length} chars) — what AI read from the sheet</summary>
          <div className="transcript-toolbar">
            <button className="btn btn-sm" onClick={onDownloadTranscript}>⬇️ Download .txt</button>
            <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(tx)}>📋 Copy</button>
          </div>
          <pre className="transcript">{tx}</pre>
        </details>
      )}
    </div>
  )
}
