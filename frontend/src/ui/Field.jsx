import { useId } from 'react'

/**
 * Form field wrapper. Provides label + error/hint slots. Pass any control
 * (input/select/textarea) as children with `id` auto-wired.
 *
 *   <Field label="Grade" hint="Pick a CBSE class">
 *     {({ id }) => <select id={id}>...</select>}
 *   </Field>
 */
export default function Field({ label, hint, error, wide = false, children }) {
  const id = useId()
  return (
    <div className={`field ${wide ? 'field-wide' : ''} ${error ? 'is-invalid' : ''}`}>
      {label && <label htmlFor={id}>{label}</label>}
      {typeof children === 'function' ? children({ id }) : children}
      {error ? <div className="field-error">{error}</div>
             : hint && <div className="field-hint">{hint}</div>}
    </div>
  )
}
