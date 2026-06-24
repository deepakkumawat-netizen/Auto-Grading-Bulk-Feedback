import { useEffect } from 'react'
import { createPortal } from 'react-dom'

/** Portal-based modal. ESC closes. Backdrop click closes. */
export default function Modal({ open, onClose, title, children, footer, size = 'md' }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [open, onClose])

  if (!open) return null
  return createPortal(
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal>
      <div className={`modal-card modal-${size}`} onClick={(e) => e.stopPropagation()}>
        {title && (
          <header className="modal-head">
            <h3>{title}</h3>
            <button className="modal-x" onClick={onClose} aria-label="Close">×</button>
          </header>
        )}
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>,
    document.body
  )
}
