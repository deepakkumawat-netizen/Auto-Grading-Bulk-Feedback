import { useEffect, useRef, useState } from 'react'

const CHIPS = [
  'Which student needs the most attention?',
  'What topic should I reteach?',
  'List students scoring below 50%',
  'Summarise the class mistakes',
  'Who improved most from the class average?',
]

const DIFF_COLOR = { easy: '#22c55e', medium: '#f59e0b', hard: '#ef4444' }

export default function AgentPanel({ results, rubric, classStats }) {
  const [tab, setTab] = useState('chat')

  return (
    <div className="agent-panel">
      <div className="ap-header">
        <span className="ap-icon">✦</span>
        <span className="ap-title">AI Teaching Co-pilot</span>
        <span className="ap-sub">Powered by Gemini · acts on these {(results || []).filter(r=>r.ok).length} graded results</span>
      </div>
      <div className="ap-tabs">
        {[['chat','💬 Chat'], ['practice','📝 Practice Qs'], ['plan','🗓 Class Plan']].map(([k,label]) => (
          <button key={k} className={`ap-tab ${tab===k?'active':''}`} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'chat'     && <ChatTab     results={results} rubric={rubric} />}
      {tab === 'practice' && <PracticeTab results={results} rubric={rubric} classStats={classStats} />}
      {tab === 'plan'     && <PlanTab     results={results} rubric={rubric} />}
    </div>
  )
}

// ── Chat tab ──────────────────────────────────────────────────────────────────

function ChatTab({ results, rubric }) {
  const [messages, setMessages] = useState([
    { role: 'assistant', text: 'Ask me anything about these grading results. I can explain scores, identify patterns, suggest what to reteach, or generate practice questions on request.' }
  ])
  const [input, setInput]   = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async (text) => {
    const msg = (text || input).trim()
    if (!msg || loading) return
    setInput('')
    const history = messages
      .filter(m => m.role !== 'assistant' || messages.indexOf(m) > 0)
      .map(m => ({ role: m.role, content: m.text }))
    setMessages(prev => [...prev, { role: 'user', text: msg }])
    setLoading(true)
    try {
      const r = await fetch('/api/agent/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, results, rubric, history }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setMessages(prev => [...prev, { role: 'assistant', text: data.reply }])
    } catch (e) {
      setMessages(prev => [...prev, { role: 'assistant', text: `⚠ Error: ${e.message}`, err: true }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="ap-chat">
      <div className="ap-chips">
        {CHIPS.map(c => (
          <button key={c} className="ap-chip" onClick={() => send(c)} disabled={loading}>{c}</button>
        ))}
      </div>
      <div className="ap-messages">
        {messages.map((m, i) => (
          <div key={i} className={`ap-bubble ${m.role} ${m.err ? 'err' : ''}`}>
            {m.role === 'assistant' && <span className="ap-bubble-icon">✦</span>}
            <pre className="ap-bubble-text">{m.text}</pre>
          </div>
        ))}
        {loading && (
          <div className="ap-bubble assistant">
            <span className="ap-bubble-icon">✦</span>
            <span className="ap-typing"><span/><span/><span/></span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div className="ap-input-row">
        <input
          className="ap-input"
          placeholder="Ask about the results…"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
          disabled={loading}
        />
        <button className="ap-send" onClick={() => send()} disabled={loading || !input.trim()}>
          {loading ? '…' : '↑'}
        </button>
      </div>
    </div>
  )
}

// ── Practice Questions tab ────────────────────────────────────────────────────

function PracticeTab({ results, rubric, classStats }) {
  const graded = (results || []).filter(r => r.ok)
  const [studentIdx, setStudentIdx] = useState(0)
  const [count, setCount]           = useState(5)
  const [questions, setQuestions]   = useState([])
  const [loading, setLoading]       = useState(false)
  const [error, setError]           = useState('')

  const student = graded[studentIdx]

  const generate = async () => {
    if (!student) return
    setLoading(true); setError(''); setQuestions([])
    try {
      const scope = student.detected_scope || {}
      const r = await fetch('/api/agent/practice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          result:  student,
          grade:   scope.grade   || 8,
          subject: scope.subject || 'General',
          chapter: scope.chapter || '',
          count,
        }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setQuestions(data.questions || [])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  if (!graded.length) return <div className="ap-empty">No graded results yet.</div>

  return (
    <div className="ap-practice">
      <div className="ap-controls">
        <div className="ap-ctrl-group">
          <label className="ap-ctrl-label">Student</label>
          <select className="ap-select" value={studentIdx}
                  onChange={e => { setStudentIdx(+e.target.value); setQuestions([]) }}>
            {graded.map((r, i) => (
              <option key={i} value={i}>
                {r.student_name || r.file} · {r.marks_awarded}/{r.marks_total} ({r.percentage}%)
              </option>
            ))}
          </select>
        </div>
        <div className="ap-ctrl-group">
          <label className="ap-ctrl-label">Questions</label>
          <select className="ap-select ap-select-sm" value={count} onChange={e => setCount(+e.target.value)}>
            {[3,5,7,10].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <button className="ap-action-btn" onClick={generate} disabled={loading}>
          {loading ? 'Generating…' : '✦ Generate'}
        </button>
      </div>
      {student && (
        <div className="ap-student-summary">
          <span className="ap-ss-name">{student.student_name || student.file}</span>
          <span className="ap-ss-score">{student.marks_awarded}/{student.marks_total}</span>
          <span className="ap-ss-mistakes">{(student.mistakes || []).slice(0,2).map(m=>m.type).join(' · ')}</span>
        </div>
      )}
      {error && <div className="ap-error">{error}</div>}
      {questions.length > 0 && (
        <div className="ap-questions">
          {questions.map((q, i) => (
            <div key={i} className="ap-qcard">
              <div className="ap-qcard-head">
                <span className="ap-qnum">Q{q.number}</span>
                <span className="ap-qmeta">
                  <span className="ap-qdiff" style={{ color: DIFF_COLOR[q.difficulty] || '#94a3b8' }}>
                    {q.difficulty}
                  </span>
                  <span className="ap-qmarks">{q.marks} mark{q.marks !== 1 ? 's' : ''}</span>
                </span>
              </div>
              <div className="ap-qtext">{q.question}</div>
              <details className="ap-qanswer">
                <summary>Answer key</summary>
                <div className="ap-qanswer-body">{q.answer_key}</div>
                {q.ncert_ref && <div className="ap-qref">📚 {q.ncert_ref}</div>}
              </details>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Class Plan tab ────────────────────────────────────────────────────────────

const HEALTH_COLOR = { strong: '#22c55e', average: '#f59e0b', needs_help: '#ef4444' }
const HEALTH_LABEL = { strong: 'Class is strong', average: 'Needs attention', needs_help: 'Urgent intervention needed' }

function PlanTab({ results, rubric }) {
  const [plan, setPlan]     = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]   = useState('')

  const generate = async () => {
    setLoading(true); setError(''); setPlan(null)
    try {
      const r = await fetch('/api/agent/class-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ results, rubric }),
      })
      const data = await r.json()
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`)
      setPlan(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="ap-plan">
      {!plan && !loading && (
        <div className="ap-plan-cta">
          <p className="ap-plan-desc">
            Generate a focused 30-40 minute intervention lesson plan based on the class's weakest topics.
            Includes step-by-step activities, quick-check questions, and homework.
          </p>
          <button className="ap-action-btn ap-action-btn-lg" onClick={generate} disabled={loading}>
            ✦ Generate Class Plan
          </button>
        </div>
      )}
      {loading && (
        <div className="ap-plan-loading">
          <span className="ap-typing"><span/><span/><span/></span>
          <span>Analysing class mistakes and building lesson plan…</span>
        </div>
      )}
      {error && <div className="ap-error">{error}</div>}
      {plan && (
        <div className="ap-plan-body">
          <div className="ap-plan-top">
            <div className="ap-health-badge"
                 style={{ background: HEALTH_COLOR[plan.class_health] + '22',
                          borderColor: HEALTH_COLOR[plan.class_health],
                          color: HEALTH_COLOR[plan.class_health] }}>
              {HEALTH_LABEL[plan.class_health] || plan.class_health}
            </div>
            <p className="ap-plan-summary">{plan.summary}</p>
          </div>

          {plan.priority_topics?.length > 0 && (
            <div className="ap-section">
              <div className="ap-section-title">Priority topics to cover</div>
              <div className="ap-topic-pills">
                {plan.priority_topics.map((t, i) => <span key={i} className="ap-topic-pill">{t}</span>)}
              </div>
            </div>
          )}

          {plan.steps?.length > 0 && (
            <div className="ap-section">
              <div className="ap-section-title">Lesson steps</div>
              {plan.steps.map((s, i) => (
                <div key={i} className="ap-step">
                  <div className="ap-step-head">
                    <span className="ap-step-num">{s.step}</span>
                    <span className="ap-step-title">{s.title}</span>
                    <span className="ap-step-dur">{s.duration_mins} min</span>
                  </div>
                  <div className="ap-step-body">
                    <div><b>Teacher:</b> {s.what_teacher_does}</div>
                    <div><b>Students:</b> {s.what_students_do}</div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {plan.quick_check_questions?.length > 0 && (
            <div className="ap-section">
              <div className="ap-section-title">Quick check questions (end of class)</div>
              {plan.quick_check_questions.map((q, i) => (
                <details key={i} className="ap-check-q">
                  <summary>{i + 1}. {q.q}</summary>
                  <div className="ap-check-ans">{q.answer}</div>
                </details>
              ))}
            </div>
          )}

          {plan.students_needing_attention?.length > 0 && (
            <div className="ap-section">
              <div className="ap-section-title">Students needing extra attention</div>
              <div className="ap-attention-list">
                {plan.students_needing_attention.map((n, i) => (
                  <span key={i} className="ap-attention-name">{n}</span>
                ))}
              </div>
            </div>
          )}

          {plan.homework_suggestion && (
            <div className="ap-section">
              <div className="ap-section-title">Homework suggestion</div>
              <p className="ap-hw">{plan.homework_suggestion}</p>
            </div>
          )}

          <button className="ap-regen-btn" onClick={generate}>↺ Regenerate</button>
        </div>
      )}
    </div>
  )
}
