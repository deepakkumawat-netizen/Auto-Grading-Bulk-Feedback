import { useContext } from 'react'
import { ToastCtx } from '../providers/ToastProvider.jsx'

/** Returns { push, dismiss, items } — works only inside <ToastProvider>. */
export function useToast() {
  const ctx = useContext(ToastCtx)
  if (!ctx) throw new Error('useToast must be used inside <ToastProvider>')
  return ctx
}
