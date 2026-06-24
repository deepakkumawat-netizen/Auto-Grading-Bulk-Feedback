import { useEffect } from 'react'
import Field from '../ui/Field.jsx'
import { useCurriculum } from '../hooks/useCurriculum.js'

const GRADES = Array.from({ length: 12 }, (_, i) => i + 1)

export default function GradeSubjectChapter({ value, onChange, disabled }) {
  const { grade = 6, subject = '', chapter = '' } = value || {}
  const { data, subjects, loading, chaptersFor } = useCurriculum(grade)

  useEffect(() => {
    if (!data) return
    const firstSubj = subjects.includes(subject) ? subject : (subjects[0] || '')
    const chList = chaptersFor(firstSubj)
    const firstChap = chList.find(c => c.title === chapter)?.title || chList[0]?.title || ''
    if (firstSubj !== subject || firstChap !== chapter) {
      onChange?.({ grade, subject: firstSubj, chapter: firstChap })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  const chapters = chaptersFor(subject)

  return (
    <div className="gsc">
      <Field label="Grade">
        {({ id }) => (
          <select id={id} value={grade}
                  onChange={e => onChange?.({ grade: Number(e.target.value), subject: '', chapter: '' })}
                  disabled={disabled}>
            {GRADES.map(g => <option key={g} value={g}>Grade {g}</option>)}
          </select>
        )}
      </Field>
      <Field label="Subject">
        {({ id }) => (
          <select id={id} value={subject}
                  onChange={e => {
                    const s = e.target.value
                    const first = chaptersFor(s)[0]?.title || ''
                    onChange?.({ grade, subject: s, chapter: first })
                  }}
                  disabled={disabled || loading || !subjects.length}>
            {subjects.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
      </Field>
      <Field label="Chapter" wide>
        {({ id }) => (
          <select id={id} value={chapter}
                  onChange={e => onChange?.({ grade, subject, chapter: e.target.value })}
                  disabled={disabled || !chapters.length}>
            {chapters.map((c, i) => (
              <option key={`${c.ch}-${i}`} value={c.title}>{c.ch} — {c.title}</option>
            ))}
          </select>
        )}
      </Field>
    </div>
  )
}
