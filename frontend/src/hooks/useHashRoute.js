import { useCallback, useEffect, useState } from 'react'

/**
 * Hash-based router. Returns [route, navigate].
 * Subscribes to hashchange. Navigate replaces location.hash.
 */
export function useHashRoute(initial = '#/') {
  const [route, setRoute] = useState(() => window.location.hash || initial)

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash || initial)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [initial])

  const navigate = useCallback((h) => {
    window.location.hash = h
  }, [])

  return [route, navigate]
}
