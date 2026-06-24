import { useEffect, useRef, useState } from 'react'

/**
 * Multi-format download dropdown.
 *
 *   <DownloadMenu label="Download transcript"
 *                 formats={[
 *                   {id:'txt',  label:'Text (.txt)',     icon:'📄'},
 *                   {id:'md',   label:'Markdown (.md)',  icon:'📝'},
 *                   {id:'json', label:'JSON (.json)',    icon:'🧾'},
 *                   {id:'pdf',  label:'PDF (.pdf)',      icon:'📕'},
 *                   {id:'docx', label:'Word (.docx)',    icon:'📘'},
 *                 ]}
 *                 onSelect={(id) => ...} />
 */
export default function DownloadMenu({
  label = 'Download',
  icon = '⬇️',
  size = 'md',
  formats = DEFAULT_FORMATS,
  onSelect,
  align = 'right',
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const onClick = (e) => { if (!ref.current?.contains(e.target)) setOpen(false) }
    const onKey   = (e) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('click', onClick)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('click', onClick)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="dl-menu" ref={ref}>
      <button className={`btn ${size === 'sm' ? 'btn-sm' : ''} dl-trigger`}
              onClick={() => setOpen(o => !o)}>
        <span>{icon}</span>
        <span>{label}</span>
        <span className={`dl-arrow ${open ? 'open' : ''}`}>▾</span>
      </button>
      {open && (
        <div className={`dl-dropdown dl-align-${align}`}>
          {formats.map(f => (
            <button key={f.id} className="dl-item"
                    onClick={() => { setOpen(false); onSelect?.(f.id) }}>
              <span className="dl-item-icon">{f.icon}</span>
              <span>{f.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export const DEFAULT_FORMATS = [
  { id: 'txt',  label: 'Text (.txt)',     icon: '📄' },
  { id: 'md',   label: 'Markdown (.md)',  icon: '📝' },
  { id: 'json', label: 'JSON (.json)',    icon: '🧾' },
  { id: 'pdf',  label: 'PDF (.pdf)',      icon: '📕' },
  { id: 'docx', label: 'Word (.docx)',    icon: '📘' },
]
