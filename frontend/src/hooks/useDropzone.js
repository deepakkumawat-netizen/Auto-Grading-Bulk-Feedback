import { useCallback, useRef, useState } from 'react'

/**
 * Drag-and-drop file zone. Wire up `getRootProps` to a div and
 * `getInputProps` to a hidden <input>. Files arrive via onDrop(files).
 *
 *   const { getRootProps, getInputProps, isDragging } = useDropzone({
 *     onDrop: setFiles, multiple: true, accept: '.pdf,.png'
 *   })
 */
export function useDropzone({ onDrop, multiple = false, accept = '' } = {}) {
  const [isDragging, setDragging] = useState(false)
  const inputRef = useRef(null)
  const dragCounter = useRef(0)

  const open = useCallback(() => inputRef.current?.click(), [])

  const onDragEnter = useCallback((e) => {
    e.preventDefault(); e.stopPropagation()
    dragCounter.current++
    if (e.dataTransfer?.items?.length) setDragging(true)
  }, [])

  const onDragLeave = useCallback((e) => {
    e.preventDefault(); e.stopPropagation()
    dragCounter.current--
    if (dragCounter.current <= 0) { dragCounter.current = 0; setDragging(false) }
  }, [])

  const onDragOver = useCallback((e) => {
    e.preventDefault(); e.stopPropagation()
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
  }, [])

  const onDropEvt = useCallback((e) => {
    e.preventDefault(); e.stopPropagation()
    dragCounter.current = 0; setDragging(false)
    const files = Array.from(e.dataTransfer?.files || [])
    if (!files.length) return
    onDrop?.(multiple ? files : [files[0]])
  }, [onDrop, multiple])

  const onChange = useCallback((e) => {
    const files = Array.from(e.target.files || [])
    if (!files.length) return
    onDrop?.(multiple ? files : [files[0]])
    e.target.value = ''
  }, [onDrop, multiple])

  const getRootProps = useCallback(() => ({
    onDragEnter, onDragLeave, onDragOver, onDrop: onDropEvt, onClick: open,
    'data-dragging': isDragging || undefined,
  }), [onDragEnter, onDragLeave, onDragOver, onDropEvt, open, isDragging])

  const getInputProps = useCallback(() => ({
    ref: inputRef, type: 'file', multiple, accept, onChange,
    style: { display: 'none' },
  }), [multiple, accept, onChange])

  return { getRootProps, getInputProps, isDragging, open }
}
