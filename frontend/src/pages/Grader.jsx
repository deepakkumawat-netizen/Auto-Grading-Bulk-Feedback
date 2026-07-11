import { Fragment, useEffect, useMemo, useState } from 'react'
import ExamConfigPanel from '../components/ExamConfigPanel.jsx'
import ClassAnalytics from '../components/ClassAnalytics.jsx'
import AgentPanel from '../components/AgentPanel.jsx'
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
  const [rubric, setRubric]         = useState('')
  const [files, setFiles]           = useState([])
  const [verify, setVerify]               = useState(false)
  const [studyPlan, setStudyPlan]         = useState(false)
  const [totalMarks, setTotalMarks]       = useState('')
  const [examConfig, setExamConfig]       = useState({})
  const [health, setHealth] = useState(null)
  const [progress, setProgress] = useState(null)


  // Rubric source: 'manual' (default) or 'paper' (upload a question paper, AI generates rubric)
  const [rubricMode, setRubricMode] = useState('manual')
  const [paperFiles, setPaperFiles] = useState([])
  const [solutionFiles, setSolutionFiles] = useState([])
  const [paperBusy, setPaperBusy]   = useState(false)
  const [paperMeta, setPaperMeta]   = useState(null)   // {questions_found, total_marks, extracted_text}

  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(setHealth).catch(() => setHealth({}))
  }, [])

  // Question paper + solution key → AI rubric
  const onGenerateRubric = async () => {
    const paperFile = paperFiles[0]
    const solutionFile = solutionFiles[0]
    if (!paperFile || !solutionFile) {
      push({ kind: 'error', title: 'Files missing', body: 'Please select both a question paper and a solution/answer key.' })
      return
    }
    setPaperBusy(true)
    setPaperMeta(null)
    setRubric('')
    try {
      const fd = new FormData()
      fd.append('paper', paperFile, paperFile.name)
      fd.append('solution', solutionFile, solutionFile.name)
      const r = await fetch('/api/rubric/from-paper', { method: 'POST', body: fd })
      if (!r.ok) {
        const txt = await r.text()
        let errMsg = `HTTP ${r.status}: ${txt.slice(0, 200)}`
        try {
          const parsed = JSON.parse(txt)
          if (typeof parsed.detail === 'string') {
            errMsg = parsed.detail
          } else if (Array.isArray(parsed.detail)) {
            const missingSolution = parsed.detail.some(x => x.loc && x.loc.includes('solution'))
            if (missingSolution) {
              errMsg = 'Please upload the Answer Key / Solution Key alongside the Question Paper to generate the grading rubric.'
            } else {
              errMsg = parsed.detail.map(x => `${x.loc.join('.')}: ${x.msg}`).join(', ')
            }
          }
        } catch (e) {}
        throw new Error(errMsg)
      }
      const data = await r.json()

      // ── CRITICAL FIX ──────────────────────────────────────────────────────
      // If the backend returned an empty rubric, block grading immediately.
      // Without this check the AI grades with NO rubric and invents its own
      // marks/responses completely disconnected from the question paper.
      if (!data.rubric || !data.rubric.trim()) {
        throw new Error(
          'The question paper was read but no questions/marks could be extracted. ' +
          'Make sure the file contains a real CBSE question paper with question numbers and marks.'
        )
      }
      // ──────────────────────────────────────────────────────────────────────

      setRubric(data.rubric)
      setPaperMeta({
        questions_found: data.questions_found,
        total_marks:     data.total_marks,
        paper_board:     data.paper_board,
      })
      // Auto-fill ExamConfigPanel with board/total_marks read from the question paper header
      if (data.paper_board || data.total_marks) {
        setExamConfig(prev => ({
          ...prev,
          ...(data.paper_board   ? { board: data.paper_board }      : {}),
          ...(data.total_marks   ? { paper_total: data.total_marks } : {}),
        }))
      }
      if (data.total_marks) setTotalMarks(String(data.total_marks))
      const metaHints = [
        data.paper_board   || null,
      ].filter(Boolean).join(' · ')
      push({
        kind:  'success',
        title: '✨ Rubric generated successfully',
        body:  `${data.questions_found} question${data.questions_found === 1 ? '' : 's'} found · ${data.total_marks} marks${metaHints ? ` · ${metaHints}` : ''}`,
      })
    } catch (e) {
      // Clear rubric on failure so grading is blocked (canSubmit requires rubric)
      setRubric('')
      setPaperMeta(null)
      push({ kind: 'error', title: 'Rubric generation failed', body: String(e.message || e) })
    } finally {
      setPaperBusy(false)
    }
  }

  // Accept all supported file types (PDFs, images, txt)
  const handleFilesChange = (incoming) => {
    setFiles(incoming)
  }

  const bulk = useApi(async (signal, sessionId) => {
    const fd = new FormData()
    fd.append('rubric', rubric)
    fd.append('verify', String(verify))
    fd.append('ncert_check', 'false')
    fd.append('study_plan', String(studyPlan))
    fd.append('total_marks', String(parseInt(totalMarks) || 0))
    fd.append('grade_override', String(examConfig.grade || 0))
    fd.append('subject_override', String(examConfig.subject || ''))
    if (sessionId) {
      fd.append('session_id', sessionId)
    }
    if (examConfig && Object.keys(examConfig).length) {
      fd.append('exam_config', JSON.stringify(examConfig))
    }
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
    
    const sessId = Math.random().toString(36).substring(7) + Date.now().toString(36)
    setProgress({ total: files.length, completed: 0, failed: 0, files: Object.fromEntries(files.map(f => [f.name, 'queued'])) })
    
    const intervalId = setInterval(async () => {
      try {
        const pr = await fetch(`/api/grade/progress/${sessId}`)
        if (pr.ok) {
          const pData = await pr.json()
          setProgress(pData)
        }
      } catch (e) {
        console.warn('Failed to fetch progress:', e)
      }
    }, 1000)

    try {
      const r = await bulk.run(sessId)
      clearInterval(intervalId)
      setProgress(null)
      const flagged = r?.results?.filter(x => x.needs_review).length || 0
      push({
        kind: 'success',
        title: `Graded ${r.graded}/${r.count}`,
        body: flagged > 0 ? `Verifier flagged ${flagged} for review` : `Class average: ${r.class_analytics.average_percentage}%`,
      })
    } catch (e) {
      clearInterval(intervalId)
      setProgress(null)
      push({ kind: 'error', title: 'Grading failed', body: String(e.message || e) })
    }
  }

  const downloadCSV = async () => {
    try {
      const r = await fetch('/api/export/csv', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: bulk.data?.results }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      triggerDownload(await r.blob(), 'grades.csv')
      push({ kind: 'success', title: 'CSV downloaded' })
    } catch (e) {
      push({ kind: 'error', title: 'CSV download failed', body: String(e.message || e) })
    }
  }

  const downloadZip = async () => {
    try {
      const r = await fetch('/api/feedback/zip', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results: bulk.data?.results, meta: {} }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      triggerDownload(await r.blob(), 'feedback.zip')
      push({ kind: 'success', title: 'Feedback ZIP downloaded' })
    } catch (e) {
      push({ kind: 'error', title: 'ZIP download failed', body: String(e.message || e) })
    }
  }

  const downloadAllTranscripts = () => {
    try {
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
      const blob = new Blob([out], { type: 'text/markdown;charset=utf-8' })
      triggerDownload(blob, 'all_transcripts.md')
      push({ kind: 'success', title: 'All transcripts downloaded' })
    } catch (e) {
      push({ kind: 'error', title: 'Transcripts download failed', body: String(e.message || e) })
    }
  }

  const downloadOneTranscript = (result) => {
    try {
      const name = (result.student_name || result.file || 'student').replace(/[^a-z0-9 _-]/gi, '')
      const blob = new Blob([result.extracted_text || ''], { type: 'text/markdown;charset=utf-8' })
      triggerDownload(blob, `${name}_transcript.md`)
    } catch (e) {
      push({ kind: 'error', title: 'Transcript download failed', body: String(e.message || e) })
    }
  }

  const downloadOnePdf = async (result) => {
    try {
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
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const name = (result.student_name || result.file || 'student').replace(/[^a-z0-9 _-]/gi, '')
      triggerDownload(await r.blob(), `${name}.pdf`)
      push({ kind: 'success', title: 'Feedback PDF downloaded', body: `${name}.pdf` })
    } catch (e) {
      push({ kind: 'error', title: 'PDF download failed', body: String(e.message || e) })
    }
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
  const canSubmit = !bulk.loading && files.length > 0 && rubric.trim() && examConfig.grade && examConfig.subject
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
          <span className="ad-icon">📌</span>
          <div>
            <b>Class/Grade &amp; Subject selection:</b> Please manually select the Class/Grade and Subject below. Grade-appropriate strictness and feedback will be applied.
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
                <div className="fp-grid" style={{ marginBottom: '12px' }}>
                  <FileDropzone value={paperFiles} onChange={setPaperFiles}
                                accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                                label="1. Question paper (Required)"
                                hint="AI will extract each question and detect marks." />
                  <FileDropzone value={solutionFiles} onChange={setSolutionFiles}
                                accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                                label="2. Solution Key / Answer Key (Required)"
                                hint="Provide key to ground correct steps & formulas." />
                </div>
                
                <div style={{ marginBottom: '14px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <Button variant="primary" onClick={onGenerateRubric} disabled={paperBusy || !paperFiles.length || !solutionFiles.length} loading={paperBusy}>
                    ✨ Generate Rubric from Paper & Solution Key
                  </Button>
                  {(paperFiles.length > 0 || solutionFiles.length > 0) && (
                    <Button variant="ghost" onClick={() => { setPaperFiles([]); setSolutionFiles([]); setRubric(''); setPaperMeta(null); }}>
                      Clear files
                    </Button>
                  )}
                </div>

                {paperBusy && (
                  <div className="paper-meta-pill" style={{background:'#fef9c3',color:'#92400e',borderColor:'#fde68a', marginBottom: '12px'}}>
                    ⏳ Extracting questions from paper — rubric will appear below…
                  </div>
                )}
                {paperMeta && rubric.trim() && (
                  <div className="paper-meta-pill" style={{marginBottom: '12px'}}>
                    ✅ Rubric locked from question paper — <b>{paperMeta.questions_found} questions</b> ·
                    total <b>{paperMeta.total_marks} marks</b>
                    {paperMeta.paper_grade   && <> · <b>Grade {paperMeta.paper_grade}</b></>}
                    {paperMeta.paper_subject && <> · <b>{paperMeta.paper_subject}</b></>}
                    {paperMeta.paper_board   && <> · <b>{paperMeta.paper_board}</b></>}
                    {' '}· auto-filled below ↓
                  </div>
                )}
                {!paperBusy && paperFiles.length > 0 && !rubric.trim() && (
                  <div className="paper-meta-pill" style={{background:'#fee2e2',color:'#991b1b',borderColor:'#fca5a5', marginBottom: '12px'}}>
                    ⚠️ Rubric not generated yet — click 'Generate Rubric' above.
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
                <div className="vt-title">🔍 Verifier Agent <span className="vt-speed">+3s/sheet</span></div>
                <div className="vt-sub">A second AI reviews every grade — flags over-generous marks.</div>
              </div>
            </label>
            <label className="verify-toggle">
              <input type="checkbox" checked={studyPlan}
                     onChange={e => setStudyPlan(e.target.checked)} disabled={bulk.loading}/>
              <div>
                <div className="vt-title">📖 Study Plan <span className="vt-speed">+3s/sheet</span></div>
                <div className="vt-sub">Generates a personalised next-steps plan for struggling students.</div>
              </div>
            </label>
            <div className="total-marks-row">
              <label className="total-marks-label">
                📋 Total marks of this paper
                <span className="total-marks-hint">(optional — fixes wrong totals like 81 instead of 80)</span>
              </label>
              <input
                type="number"
                className="total-marks-input"
                placeholder="e.g. 80"
                min="1" max="500"
                value={totalMarks}
                onChange={e => setTotalMarks(e.target.value)}
                disabled={bulk.loading}
              />
            </div>
            <ExamConfigPanel value={examConfig} onChange={setExamConfig} />
            <div className="override-row">
              <div className="override-field">
                <label className="override-label">
                   Class / Grade *
                </label>
                <select className="total-marks-input" value={examConfig.grade || ""}
                        onChange={e => setExamConfig(prev => ({ ...prev, grade: parseInt(e.target.value) || "" }))}
                        disabled={bulk.loading}>
                  <option value="">Select Grade</option>
                  {[1,2,3,4,5,6,7,8,9,10,11,12].map(g => (
                    <option key={g} value={g}>Grade {g}</option>
                  ))}
                </select>
              </div>
              <div className="override-field">
                <label className="override-label">
                   Subject *
                </label>
                <select className="total-marks-input" value={examConfig.subject || ""}
                        onChange={e => setExamConfig(prev => ({ ...prev, subject: e.target.value }))}
                        disabled={bulk.loading}>
                  <option value="">Select Subject</option>
                  {['English','Mathematics','Science','Physics','Chemistry','Biology',
                    'Social Science','Hindi','Sanskrit','Computer Science'].map(s => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
              </div>
            </div>
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
          {report && <Button onClick={downloadAllTranscripts} icon="📥">All transcripts (.md)</Button>}
        </div>

        {bulk.loading && progress && (
          <Card>
            <style>{`
              @keyframes gradingPulse {
                0% { transform: scale(0.9); opacity: 0.6; }
                50% { transform: scale(1.15); opacity: 1; }
                100% { transform: scale(0.9); opacity: 0.6; }
              }
            `}</style>
            <Card.Header 
              eyebrow="Processing" 
              title="Grading answer sheets..." 
              hint={`${progress.completed} of ${progress.total} completed`} 
            />
            <Card.Body>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '14px', fontWeight: 600 }}>
                <span>Overall Progress</span>
                <span>{Math.round(((progress.completed + progress.failed) / progress.total) * 100)}%</span>
              </div>
              <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.1)', borderRadius: '9999px', overflow: 'hidden', marginTop: '8px', marginBottom: '24px' }}>
                <div style={{ 
                  width: `${((progress.completed + progress.failed) / progress.total) * 100}%`, 
                  height: '100%', 
                  background: 'linear-gradient(90deg, #2563eb, #10b981)', 
                  borderRadius: '9999px',
                  transition: 'width 0.4s cubic-bezier(0.4, 0, 0.2, 1)' 
                }} />
              </div>
              
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '12px' }}>
                {Object.entries(progress.files || {}).map(([filename, status]) => {
                  let statusColor = 'rgba(255,255,255,0.3)';
                  let statusLabel = 'Queued';
                  let isPulsing = false;
                  
                  if (status === 'grading') {
                    statusColor = '#eab308';
                    statusLabel = 'Grading...';
                    isPulsing = true;
                  } else if (status === 'completed') {
                    statusColor = '#10b981';
                    statusLabel = 'Completed';
                  } else if (status === 'failed') {
                    statusColor = '#ef4444';
                    statusLabel = 'Failed';
                  } else if (status === 'skipped') {
                    statusColor = '#3b82f6';
                    statusLabel = 'Skipped';
                  }

                  return (
                    <div key={filename} style={{
                      padding: '12px',
                      borderRadius: '8px',
                      background: 'rgba(255,255,255,0.03)',
                      border: '1px solid rgba(255,255,255,0.06)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '12px',
                      transition: 'all 0.3s ease'
                    }}>
                      <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        {isPulsing && (
                          <span style={{
                            position: 'absolute',
                            width: '18px',
                            height: '18px',
                            borderRadius: '50%',
                            backgroundColor: 'rgba(234, 179, 8, 0.2)',
                            animation: 'gradingPulse 1.5s infinite ease-in-out'
                          }} />
                        )}
                        <span style={{
                          position: 'relative',
                          width: '8px',
                          height: '8px',
                          borderRadius: '50%',
                          backgroundColor: statusColor,
                          boxShadow: isPulsing ? `0 0 8px ${statusColor}` : 'none'
                        }} />
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: '13px',
                          fontWeight: 500,
                          textOverflow: 'ellipsis',
                          overflow: 'hidden',
                          whiteSpace: 'nowrap'
                        }} title={filename}>{filename}</div>
                        <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.5)', marginTop: '2px' }}>{statusLabel}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card.Body>
          </Card>
        )}

        {report && (
          <section className="results">
            <ClassAnalytics results={report.results} analytics={report.class_analytics} />
            <AgentPanel results={report.results} rubric={rubric} classStats={report.class_analytics} />

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
                          <td>
                            {r.ok ? `${r.marks_awarded}/${r.marks_total}` : '—'}
                            {r.grade_tier && <span className="grade-tier-badge" style={{marginLeft:6}}>{r.grade_tier}</span>}
                          </td>
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
  a.style.display = 'none'
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  // Defer revocation so browser has time to initiate the download stream
  setTimeout(() => URL.revokeObjectURL(url), 100)
}

const FORMAT_ICON = {
  text: '📝', diagram: '🖼', table: '📋', math: '🔢',
  bullets: '📌', hinglish: '🗣', mixed: '🔄',
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

      {(result.answer_formats_used?.length > 0 || result.detected_language) && (
        <div className="fp-block fp-formats">
          <div className="fp-label">🎨 Answer formats detected</div>
          <div className="format-badges">
            {result.detected_language && (
              <span className="fmt-badge fmt-language" title="Language detected in this answer sheet">
                🌐 {result.detected_language}
              </span>
            )}
            {result.answer_formats_used?.map(f => (
              <span key={f} className={`fmt-badge fmt-${f}`}>
                {FORMAT_ICON[f] || '📝'} {f}
              </span>
            ))}
          </div>
        </div>
      )}

      {result.per_question?.length > 0 && (
        <div className="fp-block">
          <div className="fp-label">📋 Per-question feedback</div>
          <table className="fp-table">
            <thead>
              <tr><th>Question</th><th>Format</th><th>Marks</th><th>Feedback</th></tr>
            </thead>
            <tbody>
              {result.per_question.map((q, i) => (
                <tr key={i}>
                  <td className="fp-q">{q.q}</td>
                  <td className="fp-fmt">
                    {q.format && <span className={`fmt-badge-sm fmt-${q.format}`}>{FORMAT_ICON[q.format] || '📝'}</span>}
                  </td>
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
