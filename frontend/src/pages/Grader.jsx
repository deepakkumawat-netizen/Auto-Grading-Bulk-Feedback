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
  const [appMode, setAppMode]       = useState('exam') // 'exam' or 'homework'
  const [rubric, setRubric]         = useState('')
  const [files, setFiles]           = useState([])
  const [verify, setVerify]               = useState(false)
  const [studyPlan, setStudyPlan]         = useState(false)
  const [handwritingAudit, setHandwritingAudit] = useState(false)
  const [totalMarks, setTotalMarks]       = useState('')
  const [examConfig, setExamConfig]       = useState({ grade: 10, subject: 'Science' })
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
      const sessId = Math.random().toString(36).substring(7) + Date.now().toString(36)
      fd.append('session_id', sessId)

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
      const initData = await r.json()

      let data = initData
      if (initData.status === 'started') {
        const targetSessId = initData.session_id || sessId
        while (true) {
          await new Promise(resolve => setTimeout(resolve, 2000))
          const pr = await fetch(`/api/rubric/progress/${targetSessId}`)
          if (!pr.ok) throw new Error(`Rubric progress check failed: HTTP ${pr.status}`)
          const pData = await pr.json()

          if (pData.status === 'completed') {
            const resR = await fetch(`/api/rubric/results/${targetSessId}`)
            if (!resR.ok) throw new Error(`Failed to fetch rubric results: HTTP ${resR.status}`)
            data = await resR.json()
            break
          } else if (pData.status === 'failed') {
            throw new Error(pData.error || 'Rubric generation failed in background.')
          }
        }
      }

      if (!data.rubric || !data.rubric.trim()) {
        throw new Error(
          'The question paper was read but no questions/marks could be extracted. ' +
          'Make sure the file contains a real CBSE question paper with question numbers and marks.'
        )
      }

      setRubric(data.rubric)
      setPaperMeta({
        questions_found: data.questions_found,
        total_marks:     data.total_marks,
        paper_board:     data.paper_board,
      })
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
    fd.append('handwriting_audit', String(handwritingAudit))
    if (sessionId) {
      fd.append('session_id', sessionId)
    }
    if (examConfig && Object.keys(examConfig).length) {
      fd.append('exam_config', JSON.stringify(examConfig))
    }
    files.forEach(f => fd.append('files', f, f.name))
    const r = await fetch('/api/grade/bulk', { method: 'POST', body: fd, signal })
    if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`)
    const initData = await r.json()

    if (initData.status === 'started') {
      const sessId = initData.session_id || sessionId
      while (true) {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        await new Promise(resolve => setTimeout(resolve, 2000))
        
        const pr = await fetch(`/api/grade/progress/${sessId}`, { signal })
        if (!pr.ok) throw new Error(`Progress check failed: HTTP ${pr.status}`)
        const pData = await pr.json()
        
        const isDone = pData.completed + pData.failed === pData.total
        if (isDone && pData.total > 0) {
          const resR = await fetch(`/api/grade/results/${sessId}`, { signal })
          if (!resR.ok) throw new Error(`Failed to fetch final results: HTTP ${resR.status}`)
          return resR.json()
        }
      }
    }
    return initData
  })

  // Keyboard shortcut: Cmd/Ctrl + Enter submits
  useEffect(() => {
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !bulk.loading) submit()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files, rubric, verify, handwritingAudit, bulk.loading])

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
          <span className="brand-name">Auto Grade</span>
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
        <div className="rubric-tabs" role="tablist" style={{ marginBottom: '24px' }}>
          <button role="tab" aria-selected={appMode === 'exam'}
                  className={`rubric-tab ${appMode === 'exam' ? 'active' : ''}`}
                  onClick={() => {
                    setAppMode('exam');
                    setRubric('');
                    setHandwritingAudit(false);
                  }}>
            📝 Exam Grading Mode
          </button>
          <button role="tab" aria-selected={appMode === 'homework'}
                  className={`rubric-tab ${appMode === 'homework' ? 'active' : ''}`}
                  onClick={() => {
                    setAppMode('homework');
                    setRubric('Homework completion and quality audit. Evaluate spelling, grammar, handwriting quality, and overall effort.');
                    setHandwritingAudit(true);
                  }}>
            ✍️ Homework Checking Mode (Audit Only)
          </button>
        </div>

        <div className="auto-detect-info">
          <span className="ad-icon">📌</span>
          <div>
            <b>Class/Grade &amp; Subject selection:</b> Please manually select the Class/Grade and Subject below. Grade-appropriate strictness and feedback will be applied.
          </div>
        </div>

        {appMode === 'exam' && (
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
        )}

        <Card>
          <Card.Header eyebrow={appMode === 'exam' ? "Step 2" : "Step 1"} title={appMode === 'exam' ? "Answer sheets" : "Homework sheets"}
                       hint={appMode === 'exam' ? "Upload photos, scans or PDFs of answer sheets" : "Upload photos, scans or PDFs of homework sheets"} />
          <Card.Body>
            <FileDropzone value={files} onChange={handleFilesChange} multiple
                          accept=".pdf,.png,.jpg,.jpeg,.webp,.txt"
                          label={appMode === 'exam' ? "Drop answer sheets here (PDF / JPG / PNG / TXT)" : "Drop homework sheets here (PDF / JPG / PNG / TXT)"}
                          hint="Multi-page PDFs are read with pypdf; image scans are OCR'd via Gemini Vision." />
            {appMode === 'exam' && (
              <label className="verify-toggle">
                <input type="checkbox" checked={verify}
                       onChange={e => setVerify(e.target.checked)} disabled={bulk.loading}/>
                <div>
                  <div className="vt-title">🔍 Verifier Agent <span className="vt-speed">+3s/sheet</span></div>
                  <div className="vt-sub">A second AI reviews every grade — flags over-generous marks.</div>
                </div>
              </label>
            )}
            <label className="verify-toggle">
              <input type="checkbox" checked={handwritingAudit}
                     onChange={e => setHandwritingAudit(e.target.checked)} disabled={bulk.loading || appMode === 'homework'}/>
              <div>
                <div className="vt-title">✍️ Handwriting &amp; Quality Audit <span className="vt-speed">Vision AI</span></div>
                <div className="vt-sub">Checks handwriting clarity, grammar/spelling errors, effort score, and visual checklist.</div>
              </div>
            </label>
            {appMode === 'exam' && (
              <label className="verify-toggle">
                <input type="checkbox" checked={studyPlan}
                       onChange={e => setStudyPlan(e.target.checked)} disabled={bulk.loading}/>
                <div>
                  <div className="vt-title">📖 Study Plan <span className="vt-speed">+3s/sheet</span></div>
                  <div className="vt-sub">Generates a personalised next-steps plan for struggling students.</div>
                </div>
              </label>
            )}
            {appMode === 'exam' && (
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
            )}
            <ExamConfigPanel value={examConfig} onChange={setExamConfig} appMode={appMode} />
            <div className="override-row">
              <div className="override-field">
                <label className="override-label">
                   Class / Grade *
                </label>
                <select className="total-marks-input" value={examConfig.grade || ""}
                        onChange={e => setExamConfig(prev => ({ ...prev, grade: parseInt(e.target.value) || "" }))}
                        disabled={bulk.loading}>
                  <option value="">Select Grade</option>
                  {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(g => (
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
                    <th>File</th><th>Student</th><th>Detected</th>
                    {appMode === 'exam' ? (
                      <>
                        <th>Marks</th><th>%</th>
                      </>
                    ) : (
                      <>
                        <th>Status</th><th>Effort</th>
                      </>
                    )}
                    {handwritingAudit && <th>Clarity</th>}
                    {appMode === 'exam' && <th>Verifier</th>}
                    <th>Top mistake</th><th>PDF</th>
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
                          {appMode === 'exam' ? (
                            <>
                              <td>
                                {r.ok ? `${r.marks_awarded}/${r.marks_total}` : '—'}
                                {r.grade_tier && <span className="grade-tier-badge" style={{marginLeft:6}}>{r.grade_tier}</span>}
                              </td>
                              <td>{r.ok ? `${r.percentage}%` : ''}</td>
                            </>
                          ) : (
                            <>
                              <td>
                                {r.ok ? (
                                  <span className={`detected-pill`}
                                        style={{
                                          background: r.homework_completeness?.status === 'complete' ? 'rgba(29, 158, 117, 0.15)' : r.homework_completeness?.status === 'partial' ? 'rgba(245, 158, 11, 0.15)' : 'rgba(226, 75, 74, 0.15)',
                                          color: r.homework_completeness?.status === 'complete' ? '#1d9e75' : r.homework_completeness?.status === 'partial' ? '#f59e0b' : '#e24b4a',
                                          border: '1px solid currentColor',
                                          textTransform: 'capitalize'
                                        }}>
                                    {r.homework_completeness?.status || 'Unknown'}
                                  </span>
                                ) : '—'}
                              </td>
                              <td>{r.ok ? `${r.effort_score ?? 0}%` : '—'}</td>
                            </>
                          )}

                          {handwritingAudit && (
                            <td>
                              {r.ok ? (
                                r.is_typed ? (
                                  <span className="rc-typed" style={{ fontSize: '11px', background: 'rgba(255,255,255,0.06)', padding: '2px 6px', borderRadius: '4px', whiteSpace: 'nowrap' }}>⌨️ Typed</span>
                                ) : (
                                  <span className="rc-stars" style={{ color: '#eab308', whiteSpace: 'nowrap' }}>
                                    {'★'.repeat(r.handwriting_clarity ?? 0)}
                                    <span style={{ opacity: 0.25 }}>
                                      {'★'.repeat(5 - (r.handwriting_clarity ?? 0))}
                                    </span>
                                  </span>
                                )
                              ) : '—'}
                            </td>
                          )}

                          {appMode === 'exam' && (
                            <td>{r.verifier
                                  ? (r.verifier.agrees ? `✓ ${r.verifier.confidence ?? ''}%` : `⚠ suggests ${r.verifier.suggested_marks}`)
                                  : ''}</td>
                          )}
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
                            <td colSpan={appMode === 'exam' ? (handwritingAudit ? 10 : 9) : 9}>
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

  const stars = result.handwriting_clarity ?? 0
  const isTyped = result.is_typed
  const effortScore = result.effort_score
  const categoryScores = result.category_scores
  const handwritingAnalysis = result.handwriting_analysis
  const grammarSpelling = result.grammar_spelling
  const visualElements = result.visual_elements
  const homeworkCompleteness = result.homework_completeness
  const steps = result.steps
  const firstMistake = result.first_mistake
  const cleanedTranscript = result.cleaned_transcript

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

      {/* 💪 Effort meter */}
      {typeof effortScore === 'number' && (
        <div className="effort-row" style={{
          display: 'flex', alignItems: 'center', gap: '12px',
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
          padding: '12px', borderRadius: '8px', marginBottom: '14px'
        }}>
          <div className="effort-label" style={{ fontWeight: 600, fontSize: '13px' }}>💪 Effort score</div>
          <div className="effort-bar-bg" style={{ flex: 1, height: '8px', background: 'rgba(255,255,255,0.1)', borderRadius: '9999px', overflow: 'hidden' }}>
            <div className="effort-bar-fill"
                 style={{
                   height: '100%',
                   background: 'linear-gradient(90deg, #3b82f6, #8b5cf6)',
                   width: `${Math.max(0, Math.min(100, effortScore))}%`
                 }} />
          </div>
          <div className="effort-pct" style={{ fontWeight: 700, fontSize: '13px' }}>{effortScore}%</div>
        </div>
      )}

      {result.suggestion && (
        <div className="fp-block fp-suggestion">
          <div className="fp-label">💡 Suggestion for the student</div>
          <p>{result.suggestion}</p>
        </div>
      )}

      {/* 📊 Teacher Diagnostic Dashboard */}
      {(categoryScores || handwritingAnalysis || grammarSpelling || visualElements || homeworkCompleteness) && (
        <div className="srd-dashboard-card" style={{ marginBottom: '16px' }}>
          <h4 className="srd-dashboard-title">📊 Teacher Diagnostic Dashboard</h4>
          
          {/* Category-wise Scores */}
          {categoryScores && (
            <div className="srd-category-scores">
              <div className="srd-score-tile">
                <div className="tile-icon">✍️</div>
                <div className="tile-label">Handwriting</div>
                <div className="tile-score">{categoryScores.handwriting_quality}%</div>
                <div className="tile-progress-bg">
                  <div className="tile-progress-fill hw" style={{ width: `${categoryScores.handwriting_quality}%` }} />
                </div>
              </div>
              <div className="srd-score-tile">
                <div className="tile-icon">🗣️</div>
                <div className="tile-label">Grammar &amp; Spell</div>
                <div className="tile-score">{categoryScores.grammar_and_spelling}%</div>
                <div className="tile-progress-bg">
                  <div className="tile-progress-fill gs" style={{ width: `${categoryScores.grammar_and_spelling}%` }} />
                </div>
              </div>
              <div className="srd-score-tile">
                <div className="tile-icon">🔢</div>
                <div className="tile-label">Math &amp; Eq</div>
                <div className="tile-score">{categoryScores.math_and_equations}%</div>
                <div className="tile-progress-bg">
                  <div className="tile-progress-fill math" style={{ width: `${categoryScores.math_and_equations}%` }} />
                </div>
              </div>
              <div className="srd-score-tile">
                <div className="tile-icon">🖼️</div>
                <div className="tile-label">Visuals/Diagrams</div>
                <div className="tile-score">{categoryScores.diagrams_and_visuals}%</div>
                <div className="tile-progress-bg">
                  <div className="tile-progress-fill vis" style={{ width: `${categoryScores.diagrams_and_visuals}%` }} />
                </div>
              </div>
              <div className="srd-score-tile">
                <div className="tile-icon">✅</div>
                <div className="tile-label">Completeness</div>
                <div className="tile-score">{categoryScores.completeness}%</div>
                <div className="tile-progress-bg">
                  <div className="tile-progress-fill comp" style={{ width: `${categoryScores.completeness}%` }} />
                </div>
              </div>
            </div>
          )}

          {/* Handwriting Analysis */}
          {!isTyped && handwritingAnalysis && (
            <div className="srd-section">
              <h5 className="srd-sec-title">✍️ Handwriting Analysis Details</h5>
              <div className="srd-hw-details">
                <div className="srd-hw-metric">
                  <strong>Clarity Rating:</strong> {handwritingAnalysis.clarity_score}/5
                </div>
                <div className="srd-hw-metric">
                  <strong>Baseline Alignment:</strong> <span className={`srd-status-${handwritingAnalysis.alignment}`}>{handwritingAnalysis.alignment}</span>
                </div>
                <div className="srd-hw-metric">
                  <strong>Letter Spacing:</strong> <span className={`srd-status-${handwritingAnalysis.spacing}`}>{handwritingAnalysis.spacing}</span>
                </div>
              </div>
              {handwritingAnalysis.readability_comment && (
                <p className="srd-hw-comment"><i>"{handwritingAnalysis.readability_comment}"</i></p>
              )}
            </div>
          )}

          {isTyped && (
            <div className="srd-section">
              <h5 className="srd-sec-title">✍️ Handwriting Analysis Details</h5>
              <div className="srd-no-errors">⌨️ This student sheet is typed/printed. Handwriting clarity evaluation was skipped.</div>
            </div>
          )}

          {/* Grammar & Spelling Check */}
          {grammarSpelling && (
            <div className="srd-section">
              <h5 className="srd-sec-title">🗣️ Grammar &amp; Spelling Auditor</h5>
              <div className="srd-gs-scores">
                <span><strong>Grammar Score:</strong> {grammarSpelling.grammar_score}/100</span>
                <span style={{ marginLeft: 20 }}><strong>Spelling Score:</strong> {grammarSpelling.spelling_score}/100</span>
              </div>
              {grammarSpelling.errors && grammarSpelling.errors.length > 0 ? (
                <div className="srd-table-wrapper">
                  <table className="srd-error-table">
                    <thead>
                      <tr>
                        <th>Original</th>
                        <th>Correction</th>
                        <th>Type</th>
                        <th>Explanation</th>
                      </tr>
                    </thead>
                    <tbody>
                      {grammarSpelling.errors.map((err, errIdx) => (
                        <tr key={errIdx}>
                          <td className="srd-err-orig">{err.original}</td>
                          <td className="srd-err-corr">{err.correction}</td>
                          <td><span className={`srd-err-type ${err.type}`}>{err.type}</span></td>
                          <td className="srd-err-expl">{err.explanation}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="srd-no-errors">🎉 No grammar or spelling errors detected!</div>
              )}
            </div>
          )}

          {/* Visual Elements Checklist */}
          {visualElements && (
            <div className="srd-section">
              <h5 className="srd-sec-title">🖼️ Visual &amp; Drawing Elements Evaluation</h5>
              <div className="srd-visual-grid">
                {Object.entries(visualElements).map(([elName, elData]) => {
                  if (!elData) return null;
                  return (
                    <div key={elName} className={`srd-visual-card ${elData.detected ? 'detected' : 'not-detected'}`}>
                      <div className="srd-visual-card-header">
                        <span className="srd-visual-icon">
                          {elName === 'graphs' && '📊'}
                          {elName === 'diagrams' && '🖼️'}
                          {elName === 'sketches' && '✏️'}
                          {elName === 'arrows' && '➡️'}
                          {elName === 'maps' && '🗺️'}
                          {elName === 'dots' && '⚫'}
                          {elName === 'tables' && '📋'}
                          {elName === 'shapes' && '⬡'}
                        </span>
                        <span className="srd-visual-name">{elName.charAt(0).toUpperCase() + elName.slice(1)}</span>
                        <span className={`srd-visual-badge ${elData.detected ? 'detected' : 'not-detected'}`}>
                          {elData.detected ? `Detected (${elData.correctness_score}%)` : 'Not Detected'}
                        </span>
                      </div>
                      {elData.detected && elData.comment && (
                        <div className="srd-visual-comment">{elData.comment}</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Homework Completeness */}
          {homeworkCompleteness && (
            <div className="srd-section" style={{ borderBottom: 'none' }}>
              <h5 className="srd-sec-title">✅ Homework Completeness Audit</h5>
              <div className="srd-completeness-row">
                <span className={`srd-complete-badge status-${homeworkCompleteness.status}`}>
                  {homeworkCompleteness.status.toUpperCase()} ({homeworkCompleteness.score}%)
                </span>
                {homeworkCompleteness.missing_parts_comment && (
                  <span className="srd-complete-comment" style={{ marginLeft: 12 }}>{homeworkCompleteness.missing_parts_comment}</span>
                )}
              </div>
            </div>
          )}
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

      {/* Root mistake card */}
      {firstMistake && (
        <div className="first-mistake-card" style={{
          background: 'rgba(239, 68, 68, 0.05)',
          border: '1px solid rgba(239, 68, 68, 0.2)',
          padding: '16px',
          borderRadius: '8px',
          marginBottom: '16px'
        }}>
          <div className="fm-head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
            <span className="fm-pill" style={{ background: '#ef4444', color: '#fff', fontSize: '11px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px' }}>🎯 ROOT MISTAKE</span>
            <span className="fm-step" style={{ fontWeight: 600, fontSize: '13px' }}>Step {firstMistake.step_index}</span>
          </div>
          <div className="fm-explain" style={{ fontSize: '12px', color: 'rgba(255,255,255,0.6)', marginBottom: '8px' }}>
            This is the <b>first step where the student went wrong</b>. Every later step is affected.
          </div>
          <div className="fm-why" style={{ fontSize: '13px', marginBottom: '4px' }}><b>Why it's wrong:</b> {firstMistake.why}</div>
          <div className="fm-fix" style={{ fontSize: '13px' }}><b>How to fix it:</b> {firstMistake.correction}</div>
        </div>
      )}

      {/* Step by step list */}
      {steps && steps.length > 0 && (
        <div className="fp-block">
          <div className="fp-label">📋 Step-by-Step Logic Validation</div>
          <div className="steps-list" style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
            {steps.map((s, i) => {
              let color = 'rgba(255,255,255,0.3)';
              let icon = '·';
              if (s.verdict === 'correct') { color = '#10b981'; icon = '✓'; }
              else if (s.verdict === 'wrong') { color = '#ef4444'; icon = '✗'; }
              else if (s.verdict === 'partial') { color = '#eab308'; icon = '⚠'; }
              
              return (
                <div key={i} style={{
                  display: 'flex', gap: '12px', padding: '8px 12px',
                  borderRadius: '6px', background: 'rgba(255,255,255,0.02)',
                  borderLeft: `3px solid ${color}`
                }}>
                  <div style={{ fontWeight: 700, color }}>{icon}</div>
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 500 }}>{s.text}</div>
                    {s.comment && <div style={{ fontSize: '11px', color: 'rgba(255,255,255,0.5)', marginTop: '2px' }}>{s.comment}</div>}
                  </div>
                </div>
              );
            })}
          </div>
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

      {/* Polished transcript details */}
      {cleanedTranscript && (
        <details className="fp-transcript" style={{ marginBottom: 12 }}>
          <summary style={{ cursor: 'pointer', fontWeight: 600 }}>✨ Cleaned &amp; Readable Transcript (Recommended)</summary>
          <div className="transcript-toolbar" style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
            <button className="btn btn-sm" onClick={() => {
              const name = (result.student_name || result.file || 'student').replace(/[^a-z0-9 _-]/gi, '')
              const blob = new Blob([cleanedTranscript], { type: 'text/markdown;charset=utf-8' })
              triggerDownload(blob, `${name}_cleaned.md`)
            }}>⬇️ Download .md</button>
            <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(cleanedTranscript)}>📋 Copy</button>
          </div>
          <pre className="transcript" style={{ whiteSpace: 'pre-wrap' }}>{cleanedTranscript}</pre>
        </details>
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
