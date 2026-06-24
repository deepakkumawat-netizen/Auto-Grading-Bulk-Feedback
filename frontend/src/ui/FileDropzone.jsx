import { useDropzone } from '../hooks/useDropzone.js'

/**
 * Drag-and-drop file zone with file list + remove buttons.
 * `value` is an array of File objects. `onChange(newFiles)` receives the new list.
 */
export default function FileDropzone({
  value = [], onChange, multiple = false, accept = '',
  label = 'Drop files here or click to choose', hint = '',
}) {
  const handleDrop = (files) => {
    onChange?.(multiple ? [...value, ...files] : files.slice(0, 1))
  }
  const { getRootProps, getInputProps, isDragging } = useDropzone({
    onDrop: handleDrop, multiple, accept,
  })

  const remove = (i) => onChange?.(value.filter((_, idx) => idx !== i))

  return (
    <div className="dropzone-wrap">
      <div {...getRootProps()} className={`dropzone ${isDragging ? 'is-dragging' : ''}`}>
        <input {...getInputProps()} />
        <div className="dropzone-icon">📥</div>
        <div className="dropzone-label">{label}</div>
        {hint && <div className="dropzone-hint">{hint}</div>}
        {accept && <div className="dropzone-accept">Accepted: {accept}</div>}
      </div>

      {value.length > 0 && (
        <ul className="file-list">
          {value.map((f, i) => (
            <li key={`${f.name}-${i}`} className="file-item">
              <span className="file-icon">📄</span>
              <span className="file-name">{f.name}</span>
              <span className="file-size">{prettyBytes(f.size)}</span>
              <button className="file-remove" onClick={(e) => { e.stopPropagation(); remove(i) }}
                      aria-label={`Remove ${f.name}`}>×</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function prettyBytes(n) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}
