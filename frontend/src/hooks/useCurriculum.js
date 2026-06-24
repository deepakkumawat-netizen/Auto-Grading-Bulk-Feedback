import { useEffect, useState } from 'react'

/** Fetches /api/curriculum/{grade} once per grade. Cached in-memory. */
const cache = new Map()

export function useCurriculum(grade) {
  const [data, setData] = useState(() => cache.get(grade) || null)
  const [loading, setLoading] = useState(!cache.has(grade))

  useEffect(() => {
    if (cache.has(grade)) { setData(cache.get(grade)); setLoading(false); return }
    let cancelled = false
    setLoading(true)
    fetch(`/api/curriculum/${grade}`)
      .then(r => r.json())
      .then(d => { if (cancelled) return; cache.set(grade, d); setData(d); setLoading(false) })
      .catch(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [grade])

  const subjects = data ? Object.keys(data.subjects || {}) : []
  return { data, subjects, loading, chaptersFor: (s) => data?.subjects?.[s] || [] }
}
