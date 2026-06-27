import { useState, useEffect } from "react";

const BOARDS   = ["CBSE","ICSE","Maharashtra State Board","UP Board","Karnataka Board","Rajasthan Board","Tamil Nadu Board","Custom"];
const SUBJECTS = ["Mathematics","Science","Physics","Chemistry","Biology","English","Hindi","Social Science","History","Geography","Civics","Economics","Computer Science","Sanskrit","Other"];
const TYPES    = ["Unit test","Class test","Mid-term","Final / Board exam","Practice paper","Assignment"];
const Q_TYPES  = ["Short answer","Long answer","Numerical","MCQ","Fill in the blank","Diagram","Essay","Definition","True / False"];
const EVAL_ORDERS = [
  "Formula → Working → Answer",
  "Concept → Steps → Result",
  "Introduction → Body → Conclusion",
  "Definition → Explanation → Example",
  "Question → Hypothesis → Experiment → Conclusion",
  "Custom (from instructions above)",
];

const DEFAULT_RULES = {
  step_marks: true, partial_credit: true, forgive_calc: false,
  diagram_marks: false, grammar_check: false, carry_forward: true,
};
const DEFAULT_FEEDBACK = {
  language: "English", tone: "Auto (based on grade)", length: "Auto (based on grade)",
  ncert_ref: true, show_concepts: true, revision_tips: true,
};

export default function ExamConfigPanel({ value, onChange, apiBase = "/api" }) {
  const [open, setOpen]       = useState(false);
  const [saved, setSaved]     = useState([]);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving]   = useState(false);
  const [qCounter, setQCounter] = useState(1);

  const cfg = value || {};
  const questions = cfg.questions || [];
  const rules     = cfg.rules     || DEFAULT_RULES;
  const feedback  = cfg.feedback  || DEFAULT_FEEDBACK;

  const update = (patch) => onChange({ ...cfg, ...patch });
  const updateRules = (patch) => update({ rules: { ...rules, ...patch } });
  const updateFb    = (patch) => update({ feedback: { ...feedback, ...patch } });

  useEffect(() => {
    fetch(`${apiBase}/exam-config`).then(r => r.json()).then(setSaved).catch(() => {});
  }, [open]);

  const addQuestion = () => {
    const label = `Q${qCounter}`;
    setQCounter(c => c + 1);
    update({ questions: [...questions, { label, type: "Short answer", marks: 5, partial: "yes" }] });
  };
  const removeQuestion = (i) => {
    const qs = [...questions]; qs.splice(i, 1);
    update({ questions: qs });
  };
  const updateQ = (i, patch) => {
    const qs = questions.map((q, idx) => idx === i ? { ...q, ...patch } : q);
    update({ questions: qs });
  };

  const qSum = questions.reduce((a, q) => a + (parseInt(q.marks) || 0), 0);
  const paperTotal = parseInt(cfg.paper_total) || 100;
  const remaining = paperTotal - qSum;

  const handleSave = async () => {
    if (!saveName.trim()) return;
    setSaving(true);
    try {
      const r = await fetch(`${apiBase}/exam-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...cfg, name: saveName }),
      });
      const d = await r.json();
      setSaved(s => [{ id: d.id, name: saveName, board: cfg.board, grade: cfg.grade,
                       subject: cfg.subject }, ...s]);
      setSaveName("");
    } finally { setSaving(false); }
  };

  const handleLoad = async (id) => {
    const r = await fetch(`${apiBase}/exam-config/${id}`);
    const d = await r.json();
    onChange(d);
    setQCounter((d.questions || []).length + 1);
  };

  const handleDelete = async (id, e) => {
    e.stopPropagation();
    await fetch(`${apiBase}/exam-config/${id}`, { method: "DELETE" });
    setSaved(s => s.filter(c => c.id !== id));
  };

  return (
    <div className="exam-config-panel">
      <div className="ecp-header" onClick={() => setOpen(o => !o)}>
        <span className="ecp-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
          {cfg.grade && cfg.subject ? (
            <>
              Exam configuration
              <span className="ecp-badge">
                Grade {cfg.grade} · {cfg.subject} {cfg.board ? `· ${cfg.board}` : ""}
              </span>
            </>
          ) : (
            <>
              <span>⚙ Set board, grade, subject &amp; question marks</span>
              {!open && <span className="ecp-hint">click to configure</span>}
            </>
          )}
        </span>
        <span className="ecp-chevron" style={{ transform: open ? "rotate(180deg)" : "none" }}>▾</span>
      </div>

      {open && (
        <div className="ecp-body">

          {/* Saved configs */}
          {saved.length > 0 && (
            <div className="ecp-section">
              <div className="ecp-section-label">Load saved config</div>
              <div className="ecp-saved-list">
                {saved.map(s => (
                  <div key={s.id} className="ecp-saved-item" onClick={() => handleLoad(s.id)}>
                    <span className="ecp-saved-name">{s.name}</span>
                    <span className="ecp-saved-meta">Grade {s.grade} · {s.subject} · {s.board}</span>
                    <button className="ecp-del-btn" onClick={(e) => handleDelete(s.id, e)} title="Delete">×</button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Exam Setup */}
          <div className="ecp-section">
            <div className="ecp-section-label">Exam setup</div>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Board</span>
                <select value={cfg.board || "CBSE"} onChange={e => update({ board: e.target.value })}>
                  {BOARDS.map(b => <option key={b}>{b}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>Grade</span>
                <select value={cfg.grade || ""} onChange={e => update({ grade: parseInt(e.target.value) || "" })}>
                  <option value="">Select grade</option>
                  {Array.from({length:12},(_,i)=>i+1).map(g=><option key={g} value={g}>Grade {g}</option>)}
                </select>
              </label>
            </div>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Subject</span>
                <select value={cfg.subject || ""} onChange={e => update({ subject: e.target.value })}>
                  <option value="">Select subject</option>
                  {SUBJECTS.map(s => <option key={s}>{s}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>Chapter / topic (optional)</span>
                <input type="text" value={cfg.chapter || ""} onChange={e => update({ chapter: e.target.value })}
                  placeholder="e.g. Chemical reactions, Ch. 1-3" />
              </label>
            </div>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Exam type</span>
                <select value={cfg.exam_type || "Unit test"} onChange={e => update({ exam_type: e.target.value })}>
                  {TYPES.map(t => <option key={t}>{t}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>Total paper marks</span>
                <input type="number" min="1" max="500" value={cfg.paper_total || 100}
                  onChange={e => update({ paper_total: parseInt(e.target.value) || 100 })} />
              </label>
            </div>
          </div>

          {/* Question marks */}
          <div className="ecp-section">
            <div className="ecp-section-label">Question-wise marks</div>
            <table className="ecp-table">
              <thead>
                <tr>
                  <th style={{width:80}}>Question</th>
                  <th>Type</th>
                  <th style={{width:80}}>Marks</th>
                  <th style={{width:120}}>Partial credit</th>
                  <th style={{width:32}}></th>
                </tr>
              </thead>
              <tbody>
                {questions.map((q, i) => (
                  <tr key={i}>
                    <td>
                      <input type="text" value={q.label} style={{width:70}}
                        onChange={e => updateQ(i, {label: e.target.value})} />
                    </td>
                    <td>
                      <select value={q.type} onChange={e => updateQ(i, {type: e.target.value})}>
                        {Q_TYPES.map(t => <option key={t}>{t}</option>)}
                      </select>
                    </td>
                    <td>
                      <input type="number" min="0" max="100" value={q.marks} style={{width:64}}
                        onChange={e => updateQ(i, {marks: parseInt(e.target.value)||0})} />
                    </td>
                    <td>
                      <select value={q.partial} onChange={e => updateQ(i, {partial: e.target.value})}>
                        <option value="yes">Yes</option>
                        <option value="no">No</option>
                        <option value="half">Half only</option>
                      </select>
                    </td>
                    <td>
                      <button className="ecp-del-btn" onClick={() => removeQuestion(i)}>×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="ecp-add-btn" onClick={addQuestion}>+ Add question</button>
            <div className="ecp-marks-summary">
              <span>Configured: <strong>{qSum}</strong></span>
              <span style={{color: remaining < 0 ? "var(--danger,#e24b4a)" : remaining === 0 ? "var(--success,#1d9e75)" : "inherit"}}>
                Remaining: <strong>{remaining}</strong>
              </span>
            </div>
          </div>

          {/* Instructions */}
          <div className="ecp-section">
            <div className="ecp-section-label">Grading instructions</div>
            <label className="ecp-field">
              <span>How should answers be checked? (your own words)</span>
              <textarea value={cfg.instructions || ""} rows={3}
                onChange={e => update({ instructions: e.target.value })}
                placeholder="e.g. Give full marks if concept is right even if calculation is wrong. Check formula first, then substitution, then final answer." />
            </label>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Evaluation order</span>
                <select value={cfg.eval_order || EVAL_ORDERS[0]}
                  onChange={e => update({ eval_order: e.target.value })}>
                  {EVAL_ORDERS.map(o => <option key={o}>{o}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>Strictness level</span>
                <select value={cfg.strictness || "moderate"} onChange={e => update({ strictness: e.target.value })}>
                  <option value="lenient">Lenient — concept understood = marks</option>
                  <option value="moderate">Moderate — key terms required</option>
                  <option value="strict">Strict — exact steps required</option>
                  <option value="board">Board exam — full precision required</option>
                </select>
              </label>
            </div>
          </div>

          {/* Rules */}
          <div className="ecp-section">
            <div className="ecp-section-label">Grading rules</div>
            {[
              ["step_marks",    "Step-by-step marking", "Award marks per step, not just final answer"],
              ["partial_credit","Partial credit allowed","Give marks for partially correct answers"],
              ["forgive_calc",  "Forgive calculation errors","Full marks if method is correct, calculation wrong"],
              ["diagram_marks", "Diagram / labeling marks","Award marks separately for diagrams"],
              ["grammar_check", "Grammar check","Deduct for poor grammar (language subjects only)"],
              ["carry_forward", "Carry-forward error protection","Don't penalise downstream steps for one root error"],
            ].map(([key, label, desc]) => (
              <div key={key} className="ecp-toggle-row">
                <div>
                  <div className="ecp-toggle-label">{label}</div>
                  <div className="ecp-toggle-desc">{desc}</div>
                </div>
                <label className="ecp-toggle">
                  <input type="checkbox" checked={!!rules[key]}
                    onChange={e => updateRules({[key]: e.target.checked})} />
                  <span className="ecp-slider" />
                </label>
              </div>
            ))}
          </div>

          {/* Feedback */}
          <div className="ecp-section">
            <div className="ecp-section-label">Feedback style</div>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Feedback language</span>
                <select value={feedback.language || "English"} onChange={e => updateFb({language: e.target.value})}>
                  {["English","Hindi","Hinglish (Hindi + English)","Marathi","Telugu","Tamil","Bengali","Gujarati","Kannada","Punjabi"].map(l=><option key={l}>{l}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>Tone</span>
                <select value={feedback.tone || "Auto (based on grade)"} onChange={e => updateFb({tone: e.target.value})}>
                  {["Auto (based on grade)","Encouraging","Neutral / factual","Exam-focused","Very simple (primary classes)"].map(t=><option key={t}>{t}</option>)}
                </select>
              </label>
            </div>
            <div className="ecp-grid-2">
              <label className="ecp-field">
                <span>Length</span>
                <select value={feedback.length || "Auto (based on grade)"} onChange={e => updateFb({length: e.target.value})}>
                  {["Auto (based on grade)","Short — 1-2 sentences","Medium — 3-4 sentences","Detailed — full paragraph"].map(l=><option key={l}>{l}</option>)}
                </select>
              </label>
              <label className="ecp-field">
                <span>NCERT chapter reference</span>
                <select value={feedback.ncert_ref ? "yes" : "no"} onChange={e => updateFb({ncert_ref: e.target.value === "yes"})}>
                  <option value="yes">Auto (Grade 6+ only)</option>
                  <option value="always">Always include</option>
                  <option value="no">Never include</option>
                </select>
              </label>
            </div>
            <div className="ecp-toggle-row">
              <div>
                <div className="ecp-toggle-label">Show missed concepts in feedback</div>
                <div className="ecp-toggle-desc">Tell student exactly which concept was missing</div>
              </div>
              <label className="ecp-toggle">
                <input type="checkbox" checked={!!feedback.show_concepts}
                  onChange={e => updateFb({show_concepts: e.target.checked})} />
                <span className="ecp-slider" />
              </label>
            </div>
            <div className="ecp-toggle-row">
              <div>
                <div className="ecp-toggle-label">Include revision tips</div>
                <div className="ecp-toggle-desc">Suggest what to revise based on mistakes</div>
              </div>
              <label className="ecp-toggle">
                <input type="checkbox" checked={!!feedback.revision_tips}
                  onChange={e => updateFb({revision_tips: e.target.checked})} />
                <span className="ecp-slider" />
              </label>
            </div>
          </div>

          {/* Save config */}
          <div className="ecp-save-row">
            <input type="text" value={saveName} onChange={e => setSaveName(e.target.value)}
              placeholder="Config name (e.g. Class 10 Science Unit Test)" />
            <button className="ecp-save-btn" onClick={handleSave} disabled={saving || !saveName.trim()}>
              {saving ? "Saving…" : "Save config"}
            </button>
          </div>

        </div>
      )}
    </div>
  );
}
