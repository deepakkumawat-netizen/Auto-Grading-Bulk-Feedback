import { useEffect, useRef, useState } from 'react'

/**
 * MS Word-style WYSIWYG editor for transcripts.
 *
 *   <EditorModal isOpen={open} onClose={...}
 *                title="My Document"
 *                items={[{text, meta:{title,student,...}}, ...]}
 *                accent="#a855f7" accentDark="#6366f1" />
 *
 * Uses contentEditable + execCommand for the toolbar. Lets teachers:
 *  - format text (bold/italic/underline/lists/headings/colors)
 *  - download in TXT/MD/JSON/PDF/DOCX
 *  - print
 *  - copy all
 */
export default function EditorModal({
  isOpen, onClose, title = 'Document', items = [],
  accent = '#a855f7', accentDark = '#6366f1',
}) {
  const editorRef = useRef(null)
  const fileNameRef = useRef('document')
  const [dlOpen, setDlOpen] = useState(false)
  const [activeStyles, setActiveStyles] = useState({})

  // Build the initial HTML from items
  useEffect(() => {
    if (!isOpen || !editorRef.current) return
    const html = items.map((it, i) => itemToHtml(it, i)).join('<hr style="margin: 24px 0; border: none; border-top: 1px dashed #cbd5e1;">')
    editorRef.current.innerHTML = html || '<p>Empty document.</p>'
    // file name derived from first item or title
    const first = items[0]?.meta
    fileNameRef.current = (first?.student || first?.title || title || 'document')
      .replace(/[^a-z0-9_-]/gi, '_').slice(0, 60) || 'document'
  }, [isOpen, items, title])

  // Update toolbar button highlights based on selection
  const updateActive = () => {
    const cmds = ['bold', 'italic', 'underline', 'strikeThrough',
                  'insertOrderedList', 'insertUnorderedList',
                  'justifyLeft', 'justifyCenter', 'justifyRight']
    const next = {}
    for (const c of cmds) {
      try { next[c] = document.queryCommandState(c) } catch { next[c] = false }
    }
    setActiveStyles(next)
  }

  const exec = (cmd, value = null) => {
    document.execCommand(cmd, false, value)
    editorRef.current?.focus()
    updateActive()
  }

  const setBlock = (tag) => exec('formatBlock', tag)
  const setFontSize = (px) => {
    // execCommand fontSize takes 1-7; use inline style approach instead
    const sel = window.getSelection()
    if (!sel || sel.rangeCount === 0) return
    const range = sel.getRangeAt(0)
    if (range.collapsed) return
    const span = document.createElement('span')
    span.style.fontSize = `${px}px`
    span.appendChild(range.extractContents())
    range.insertNode(span)
    sel.removeAllRanges()
    editorRef.current?.focus()
  }

  // Download handlers
  const downloadAs = async (fmt) => {
    setDlOpen(false)
    const html = editorRef.current?.innerHTML || ''
    const text = editorRef.current?.innerText || ''
    const filename = `${fileNameRef.current}.${fmt}`

    if (fmt === 'txt') {
      saveBlob(text, filename, 'text/plain;charset=utf-8'); return
    }
    if (fmt === 'md') {
      saveBlob(htmlToMarkdown(html), filename, 'text/markdown;charset=utf-8'); return
    }
    if (fmt === 'json') {
      saveBlob(JSON.stringify({ html, text, items }, null, 2), filename, 'application/json'); return
    }
    if (fmt === 'html') {
      saveBlob(`<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${html}</body></html>`,
               filename, 'text/html'); return
    }
    // PDF / DOCX — send the plain text to the existing backend endpoints
    const exportItems = [{
      text,
      meta: { title: title || fileNameRef.current },
    }]
    const r = await fetch(`/api/transcript/${fmt}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: exportItems, filename }),
    })
    if (!r.ok) { alert(`Export failed: HTTP ${r.status}`); return }
    saveBlob(await r.blob(), filename)
  }

  const print_ = () => {
    if (!editorRef.current) return
    const html = editorRef.current.innerHTML
    const w = window.open('', '', 'width=800,height=900')
    if (!w) return
    w.document.write(`
      <!doctype html><html><head>
        <title>${title}</title>
        <style>
          body { font-family: Calibri, Arial, sans-serif; padding: 20mm; line-height: 1.6; color: #111; }
          h1, h2, h3 { color: #1e293b; margin-top: 1.2em; }
          h2 { border-bottom: 1px solid #cbd5e1; padding-bottom: 4px; }
          pre, code { font-family: Consolas, monospace; background: #f1f5f9; padding: 8px; border-radius: 4px; }
        </style>
      </head><body>${html}</body></html>
    `)
    w.document.close(); w.focus()
    setTimeout(() => { w.print(); w.close() }, 250)
  }

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(editorRef.current?.innerText || '')
      alert('Copied to clipboard ✓')
    } catch { /* ignore */ }
  }

  if (!isOpen) return null

  return (
    <div className="ed-backdrop" onClick={onClose}>
      <div className="ed-modal" onClick={e => e.stopPropagation()}>
        {/* Title bar */}
        <div className="ed-titlebar"
             style={{ background: `linear-gradient(135deg, ${accent} 0%, ${accentDark} 100%)` }}>
          <div className="ed-titlebar-left">
            <span className="ed-app-icon">📝</span>
            <div>
              <div className="ed-title">{title}</div>
              <div className="ed-subtitle">Document editor · {items.length} section{items.length !== 1 ? 's' : ''}</div>
            </div>
          </div>
          <div className="ed-titlebar-right">
            <div className="ed-dl-wrap">
              <button className="ed-tb-btn ed-primary-btn" onClick={() => setDlOpen(o => !o)}>
                ⬇ Save & Download ▾
              </button>
              {dlOpen && (
                <div className="ed-dl-menu" onClick={e => e.stopPropagation()}>
                  {[
                    { id: 'pdf',  label: 'PDF (.pdf)',      icon: '📕' },
                    { id: 'docx', label: 'Word (.docx)',    icon: '📘' },
                    { id: 'html', label: 'HTML (.html)',    icon: '🌐' },
                    { id: 'txt',  label: 'Plain text (.txt)', icon: '📄' },
                    { id: 'md',   label: 'Markdown (.md)',  icon: '📝' },
                    { id: 'json', label: 'JSON (.json)',    icon: '🧾' },
                  ].map(f => (
                    <button key={f.id} className="ed-dl-item" onClick={() => downloadAs(f.id)}>
                      <span>{f.icon}</span><span>{f.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button className="ed-close" onClick={onClose}>✕</button>
          </div>
        </div>

        {/* Toolbar — MS Word style ribbon */}
        <div className="ed-toolbar" onMouseDown={e => e.preventDefault()}>
          <div className="ed-tb-group">
            <select className="ed-select" onChange={e => setBlock(e.target.value)} defaultValue="p"
                    title="Paragraph style">
              <option value="p">Paragraph</option>
              <option value="h1">Heading 1</option>
              <option value="h2">Heading 2</option>
              <option value="h3">Heading 3</option>
              <option value="blockquote">Quote</option>
              <option value="pre">Code</option>
            </select>
            <select className="ed-select ed-size" onChange={e => setFontSize(Number(e.target.value))} defaultValue=""
                    title="Font size">
              <option value="" disabled>Size</option>
              {[10,11,12,13,14,16,18,20,24,28,32,36,48].map(s =>
                <option key={s} value={s}>{s}px</option>)}
            </select>
          </div>

          <div className="ed-tb-divider" />

          <div className="ed-tb-group">
            <ToolBtn active={activeStyles.bold}      title="Bold (Ctrl+B)"      onClick={() => exec('bold')}>𝐁</ToolBtn>
            <ToolBtn active={activeStyles.italic}    title="Italic (Ctrl+I)"    onClick={() => exec('italic')}>𝑰</ToolBtn>
            <ToolBtn active={activeStyles.underline} title="Underline (Ctrl+U)" onClick={() => exec('underline')}>U̲</ToolBtn>
            <ToolBtn active={activeStyles.strikeThrough} title="Strikethrough" onClick={() => exec('strikeThrough')}>S̶</ToolBtn>
          </div>

          <div className="ed-tb-divider" />

          <div className="ed-tb-group">
            <ColorBtn title="Text color"      onPick={c => exec('foreColor', c)} icon="A" />
            <ColorBtn title="Highlight"       onPick={c => exec('hiliteColor', c)} icon="🖌" defaults={['#fef08a','#fde047','#fdba74','#fca5a5','#86efac','#7dd3fc']} />
          </div>

          <div className="ed-tb-divider" />

          <div className="ed-tb-group">
            <ToolBtn active={activeStyles.insertUnorderedList} title="Bullet list" onClick={() => exec('insertUnorderedList')}>• ≡</ToolBtn>
            <ToolBtn active={activeStyles.insertOrderedList}   title="Numbered list" onClick={() => exec('insertOrderedList')}>1. ≡</ToolBtn>
            <ToolBtn title="Decrease indent" onClick={() => exec('outdent')}>⇤</ToolBtn>
            <ToolBtn title="Increase indent" onClick={() => exec('indent')}>⇥</ToolBtn>
          </div>

          <div className="ed-tb-divider" />

          <div className="ed-tb-group">
            <ToolBtn active={activeStyles.justifyLeft}   title="Align left"   onClick={() => exec('justifyLeft')}>⯇</ToolBtn>
            <ToolBtn active={activeStyles.justifyCenter} title="Align center" onClick={() => exec('justifyCenter')}>≡</ToolBtn>
            <ToolBtn active={activeStyles.justifyRight}  title="Align right"  onClick={() => exec('justifyRight')}>⯈</ToolBtn>
          </div>

          <div className="ed-tb-divider" />

          <div className="ed-tb-group">
            <ToolBtn title="Undo (Ctrl+Z)" onClick={() => exec('undo')}>↶</ToolBtn>
            <ToolBtn title="Redo (Ctrl+Y)" onClick={() => exec('redo')}>↷</ToolBtn>
            <ToolBtn title="Clear formatting" onClick={() => exec('removeFormat')}>✕F</ToolBtn>
          </div>

          <div className="ed-tb-spacer" />

          <div className="ed-tb-group">
            <button className="ed-tb-btn" title="Copy all" onClick={copyAll}>📋 Copy</button>
            <button className="ed-tb-btn" title="Print" onClick={print_}>🖨 Print</button>
          </div>
        </div>

        {/* Page-style editor area */}
        <div className="ed-page-area">
          <div
            ref={editorRef}
            className="ed-page"
            contentEditable
            suppressContentEditableWarning
            spellCheck="true"
            onKeyUp={updateActive}
            onMouseUp={updateActive}
          />
        </div>
      </div>
    </div>
  )
}


function ToolBtn({ children, onClick, title, active }) {
  return (
    <button className={`ed-tb-btn ${active ? 'is-active' : ''}`} onClick={onClick} title={title}
            onMouseDown={e => e.preventDefault()}>
      {children}
    </button>
  )
}

function ColorBtn({ title, icon, onPick, defaults }) {
  const [open, setOpen] = useState(false)
  const colors = defaults || ['#1e293b', '#dc2626', '#ea580c', '#ca8a04', '#16a34a', '#0891b2', '#4338ca', '#a855f7']
  return (
    <div className="ed-color-wrap">
      <button className="ed-tb-btn" title={title} onClick={() => setOpen(o => !o)}
              onMouseDown={e => e.preventDefault()}>
        {icon}
      </button>
      {open && (
        <div className="ed-color-pop" onMouseDown={e => e.preventDefault()}>
          {colors.map(c => (
            <button key={c} className="ed-color-swatch" style={{ background: c }}
                    onClick={() => { onPick(c); setOpen(false) }} />
          ))}
        </div>
      )}
    </div>
  )
}


function itemToHtml(item, i) {
  const m = item.meta || {}
  const tokens = []
  if (m.student) tokens.push(`<b>Student:</b> ${escapeHtml(String(m.student))}`)
  if (m.file)    tokens.push(`<b>File:</b> ${escapeHtml(String(m.file))}`)
  if (m.grade)   tokens.push(`<b>Grade:</b> ${m.grade}`)
  if (m.subject) tokens.push(`<b>Subject:</b> ${escapeHtml(String(m.subject))}`)
  if (m.chapter) tokens.push(`<b>Chapter:</b> ${escapeHtml(String(m.chapter))}`)
  if (m.marks !== undefined && m.marks_total !== undefined)
    tokens.push(`<b>Marks:</b> ${m.marks}/${m.marks_total}`)

  const head = `<h2>${escapeHtml(m.title || `Section ${i + 1}`)}</h2>`
  const meta = tokens.length ? `<p style="color:#64748b;font-size:13px;">${tokens.join(' · ')}</p>` : ''
  const body = (item.text || '').split('\n').map(line =>
    line.trim() ? `<p>${escapeHtml(line)}</p>` : '<p>&nbsp;</p>'
  ).join('')
  return head + meta + body
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

function htmlToMarkdown(html) {
  // Very small HTML → Markdown converter, enough for the editor output.
  let s = html
  s = s.replace(/<br\s*\/?>/gi, '\n')
  s = s.replace(/<\/p>/gi, '\n\n').replace(/<p[^>]*>/gi, '')
  s = s.replace(/<h1[^>]*>([\s\S]*?)<\/h1>/gi, '\n# $1\n')
  s = s.replace(/<h2[^>]*>([\s\S]*?)<\/h2>/gi, '\n## $1\n')
  s = s.replace(/<h3[^>]*>([\s\S]*?)<\/h3>/gi, '\n### $1\n')
  s = s.replace(/<strong[^>]*>([\s\S]*?)<\/strong>/gi, '**$1**')
  s = s.replace(/<b[^>]*>([\s\S]*?)<\/b>/gi, '**$1**')
  s = s.replace(/<em[^>]*>([\s\S]*?)<\/em>/gi, '*$1*')
  s = s.replace(/<i[^>]*>([\s\S]*?)<\/i>/gi, '*$1*')
  s = s.replace(/<u[^>]*>([\s\S]*?)<\/u>/gi, '$1')
  s = s.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, '- $1\n')
  s = s.replace(/<ul[^>]*>|<\/ul>|<ol[^>]*>|<\/ol>/gi, '\n')
  s = s.replace(/<hr[^>]*>/gi, '\n---\n')
  s = s.replace(/<[^>]+>/g, '')           // strip remaining tags
  s = s.replace(/&nbsp;/g, ' ')
  s = s.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
  return s.replace(/\n{3,}/g, '\n\n').trim()
}

function saveBlob(content, filename, mime) {
  const blob = (content instanceof Blob)
    ? content : new Blob([content], { type: mime || 'application/octet-stream' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  URL.revokeObjectURL(url)
}
