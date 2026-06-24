// Class analytics with built-in explanations so teachers understand at a glance.
const BUCKETS = [
  { label: '0-39',   min: 0,  max: 39,  color: '#ef4444', name: 'Failing',     hint: 'major help needed' },
  { label: '40-59',  min: 40, max: 59,  color: '#f59e0b', name: 'Below avg',   hint: 'concept gaps' },
  { label: '60-74',  min: 60, max: 74,  color: '#eab308', name: 'Average',     hint: 'okay, room to grow' },
  { label: '75-89',  min: 75, max: 89,  color: '#10b981', name: 'Good',        hint: 'solid grasp' },
  { label: '90-100', min: 90, max: 100, color: '#059669', name: 'Excellent',   hint: 'mastered' },
]

const MISTAKE_MEANING = {
  conceptual:    "Didn't understand the idea",
  calculation:   'Arithmetic / number error',
  step_skipped:  'Left out a working step',
  wrong_formula: 'Used the wrong formula',
  spelling:      'Spelling mistakes',
  language:      'Grammar / wording issues',
  blank:         'No attempt',
  other:         'Other mistake',
}

function describeMistake(type) {
  const key = (type || '').toLowerCase()
  return MISTAKE_MEANING[key] || 'Other mistake'
}

export default function ClassAnalytics({ results, analytics }) {
  if (!results?.length) return null
  const graded = results.filter(r => r.ok)
  if (!graded.length) return null

  const buckets = BUCKETS.map(b => ({
    ...b,
    count: graded.filter(r => (r.percentage ?? 0) >= b.min && (r.percentage ?? 0) <= b.max).length,
  }))
  const maxCount = Math.max(...buckets.map(b => b.count), 1)
  const total = graded.length

  // Find the dominant bucket and dominant mistake for a one-line takeaway
  const topBucket   = buckets.slice().sort((a, b) => b.count - a.count)[0]
  const topMistake  = analytics?.top_mistakes?.[0]
  const avgPercent  = analytics?.average_percentage ?? 0

  return (
    <div className="card analytics-card">

      {/* ─── Class distribution ─── */}
      <h3>📊 Class distribution
        <span className="chart-sub">how many students scored in each range</span>
      </h3>
      <p className="chart-help">
        Each bar = number of students in that score range. The taller the bar, the more students
        fell in that bucket. If the red/orange bars dominate, the class needs re-teaching.
      </p>

      <div className="bars">
        {buckets.map(b => (
          <div className="bar-col" key={b.label}>
            <div className="bar-wrap">
              <div className="bar"
                   style={{ height: `${(b.count / maxCount) * 100}%`, background: b.color }}
                   title={`${b.count} student(s) — ${b.name} (${b.hint})`}>
                {b.count > 0 && <span className="bar-label">{b.count}</span>}
              </div>
            </div>
            <div className="bar-tick">{b.label}%</div>
            <div className="bar-name" style={{ color: b.color }}>{b.name}</div>
          </div>
        ))}
      </div>

      <div className="bucket-legend">
        {buckets.map(b => (
          <span className="bl-item" key={b.label}>
            <span className="bl-dot" style={{ background: b.color }} />
            <b>{b.label}%</b> {b.name} <span className="bl-hint">— {b.hint}</span>
          </span>
        ))}
      </div>

      {/* ─── Most common mistakes ─── */}
      {analytics?.top_mistakes?.length > 0 && (
        <>
          <h3 style={{ marginTop: 22 }}>🎯 Most common mistakes
            <span className="chart-sub">what kind of error came up most</span>
          </h3>
          <p className="chart-help">
            Every wrong answer is tagged with a category. The bar shows how many students made that
            type of mistake. Use this to decide what to focus on in the next class.
          </p>

          <div className="mistake-bars">
            {analytics.top_mistakes.map(m => {
              const totalCount = analytics.top_mistakes.reduce((s, x) => s + x.count, 0)
              const pct = totalCount ? Math.round((m.count / totalCount) * 100) : 0
              return (
                <div className="mistake-row" key={m.type}>
                  <div className="mistake-label">
                    <div className="ml-name">{m.type}</div>
                    <div className="ml-mean">{describeMistake(m.type)}</div>
                  </div>
                  <div className="mistake-bar-bg">
                    <div className="mistake-bar-fill" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="mistake-count">{m.count}</div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* ─── Plain-English takeaway ─── */}
      <div className="takeaway">
        <span className="ta-icon">💡</span>
        <div>
          <b>In short:</b> Class average is <b>{avgPercent}%</b>. Most students fell in the{' '}
          <b style={{ color: topBucket.color }}>{topBucket.label}%</b> range
          ({topBucket.name.toLowerCase()}). {topMistake
            ? <>The biggest issue was <b>{topMistake.type}</b> ({describeMistake(topMistake.type)}) — affected {topMistake.count} student(s).</>
            : 'No major mistake pattern detected.'}
        </div>
      </div>

      {/* ─── 🧩 Common Misconceptions ─── */}
      {analytics?.misconceptions?.length > 0 && (
        <>
          <h3 style={{ marginTop: 22 }}>🧩 Common misconceptions
            <span className="chart-sub">patterns across multiple students</span>
          </h3>
          <p className="chart-help">
            The AI looked at every student's mistakes and grouped them into shared
            misunderstandings. Use this to decide what to re-teach.
          </p>
          <div className="misc-list">
            {analytics.misconceptions.map((m, i) => (
              <div className="misc-card" key={i}>
                <div className="misc-head">
                  <span className="misc-label">{m.label}</span>
                  <span className="misc-count">{m.count} student{m.count > 1 ? 's' : ''}</span>
                </div>
                <p className="misc-desc">{m.description}</p>
                {m.students?.length > 0 && (
                  <div className="misc-students">
                    {m.students.slice(0, 8).map((s, j) => <span key={j} className="misc-stu">{s}</span>)}
                    {m.students.length > 8 && <span className="misc-stu misc-more">+{m.students.length - 8} more</span>}
                  </div>
                )}
                {m.remedy && (
                  <div className="misc-remedy">
                    <b>💡 Teach next:</b> {m.remedy}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {graded.some(r => r.needs_review) && (
        <div className="needs-review-banner">
          ⚠️ {graded.filter(r => r.needs_review).length} answer(s) flagged by the verifier — review before exporting.
        </div>
      )}
    </div>
  )
}
