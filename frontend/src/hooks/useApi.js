import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Generic fetch hook. Returns { data, loading, error, run, reset }.
 * `run(args)` performs the call. AbortController is wired up to cancel
 * on unmount or re-run.
 *
 *   const { data, loading, error, run } = useApi(async (signal, { id }) => {
 *     const r = await fetch(`/api/x/${id}`, { signal })
 *     if (!r.ok) throw new Error(`HTTP ${r.status}`)
 *     return r.json()
 *   })
 *   <button onClick={() => run({ id: 1 })}>...</button>
 */
export function useApi(fn) {
  const [data, setData]       = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState(null)
  const abortRef = useRef(null)
  const fnRef    = useRef(fn)
  useEffect(() => { fnRef.current = fn }, [fn])

  const reset = useCallback(() => {
    setData(null); setError(null); setLoading(false)
  }, [])

  const run = useCallback(async (...args) => {
    abortRef.current?.abort()
    const ctrl = new AbortController()
    abortRef.current = ctrl
    setLoading(true); setError(null)
    try {
      const result = await fnRef.current(ctrl.signal, ...args)
      setData(result); setLoading(false)
      return result
    } catch (e) {
      if (e.name === 'AbortError') return
      setError(e); setLoading(false)
      throw e
    }
  }, [])

  useEffect(() => () => abortRef.current?.abort(), [])

  return { data, loading, error, run, reset, setData }
}
