import HeroIllustration from '../components/HeroIllustration.jsx'
import Button from '../ui/Button.jsx'
import Stagger from '../ui/Stagger.jsx'
import ThemeToggle from '../ui/ThemeToggle.jsx'

const FEATURES = [
  { icon: '⚡', title: 'Bulk grading in seconds',
    desc: 'Upload 30 sheets, get marks + per-student feedback in under a minute.' },
  { icon: '🧠', title: 'Verifier Agent',
    desc: 'A second AI critic double-checks every grade. Catches over-generous marks.' },
  { icon: '📚', title: 'NCERT-aligned',
    desc: 'Knows 769 chapters from Grade 1 to 12. Feedback cites the exact chapter.' },
  { icon: '📊', title: 'Class analytics',
    desc: 'See score distribution + the top mistakes your class is making.' },
  { icon: '📦', title: 'One-click exports',
    desc: 'CSV of marks + ZIP of personalised PDF feedback for every student.' },
  { icon: '💸', title: 'Free to run',
    desc: 'Powered by Groq Llama (free). Zero rupees per grading session.' },
]

const STEPS = [
  "Upload your class's answer sheets (PDF, photo or typed).",
  'Type your rubric or load a saved one.',
  'Click Grade — AI works in parallel.',
  'Download CSV + per-student PDF feedback.',
]

export default function Landing({ onStart }) {
  return (
    <div className="landing">
      <nav className="nav">
        <button className="brand" onClick={onStart}>
          <span className="brand-mark">📝</span>
          <span className="brand-name">Auto-Grading &amp; Bulk Feedback</span>
        </button>
        <div className="nav-right">
          <ThemeToggle />
          <Button variant="ghost" onClick={onStart}>Launch app →</Button>
        </div>
      </nav>

      <section className="hero">
        <div className="hero-text">
          <span className="badge">⚡ Powered By Codevidhya</span>
          <h1>Grade an entire class in <span className="grad">under a minute.</span></h1>
          <p className="lead">
            Upload a class's answer sheets; AI grades against a rubric and writes
            per-student feedback in one batch.
          </p>
          <div className="cta-row">
            <Button variant="primary" size="lg" onClick={onStart} icon="🚀">
              Start grading now
            </Button>
            <Button as="a" variant="ghost" href="#features">How it works ↓</Button>
          </div>
          <div className="trust-row">
            <span>✓ Grade 1 — Grade 12</span>
            <span>✓ All CBSE subjects</span>
            <span>✓ Free to use</span>
          </div>
        </div>
        <div className="hero-illustration">
          <HeroIllustration />
        </div>
      </section>

      <section className="features" id="features">
        <h2>Built for real classrooms.</h2>
        <Stagger className="feature-grid" gap={70}>
          {FEATURES.map(f => (
            <div className="feature-card" key={f.title}>
              <div className="feature-icon">{f.icon}</div>
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          ))}
        </Stagger>
      </section>

      <section className="how" id="how-it-works">
        <h2>How it works</h2>
        <Stagger as="ol" className="steps" gap={90}>
          {STEPS.map((s, i) => (
            <li key={i}>
              <span className="step-num">{i + 1}</span>
              <span>{s}</span>
            </li>
          ))}
        </Stagger>
      </section>

      <section className="final-cta">
        <h2>Get your evenings back.</h2>
        <p>Grade your next assignment in minutes instead of hours.</p>
        <Button variant="primary" size="lg" onClick={onStart} icon="🚀">
          Launch AutoGrader
        </Button>
      </section>

      <footer className="foot">
        Auto-Grading &amp; Bulk Feedback · <b>Powered By Codevidhya</b>
      </footer>
    </div>
  )
}
