import { createContext, useCallback, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export const ToastCtx = createContext(null)

let _id = 0

/**
 * Toast notifications via React portal.
 *
 *   const { push } = useToast()
 *   push({ kind: 'success', title: 'Saved' })
 *   push({ kind: 'error',   title: 'Failed', body: err.message })
 */
export default function ToastProvider({ children }) {
  const [items, setItems] = useState([])
  const timers = useRef(new Map())

  const dismiss = useCallback((id) => {
    setItems((arr) => arr.filter((t) => t.id !== id))
    const t = timers.current.get(id)
    if (t) { clearTimeout(t); timers.current.delete(id) }
  }, [])

  const push = useCallback((toast) => {
    const id = ++_id
    const ttl = toast.ttl ?? 4500
    setItems((arr) => [...arr, { id, kind: 'info', ...toast }])
    if (ttl > 0) {
      timers.current.set(id, setTimeout(() => dismiss(id), ttl))
    }
    return id
  }, [dismiss])

  const value = useMemo(() => ({ push, dismiss, items }), [push, dismiss, items])

  return (
    <ToastCtx.Provider value={value}>
      {children}
      {createPortal(
        <div className="toast-stack" role="region" aria-label="Notifications">
          {items.map((t) => (
            <div key={t.id} className={`toast toast-${t.kind}`} role="status"
                 onClick={() => dismiss(t.id)}>
              <div className="toast-icon">
                {t.kind === 'success' ? '✓' : t.kind === 'error' ? '✗' : 'ℹ'}
              </div>
              <div className="toast-body">
                <div className="toast-title">{t.title}</div>
                {t.body && <div className="toast-detail">{t.body}</div>}
              </div>
            </div>
          ))}
        </div>,
        document.body
      )}
    </ToastCtx.Provider>
  )
}
