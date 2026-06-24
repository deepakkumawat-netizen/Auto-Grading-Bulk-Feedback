import { forwardRef } from 'react'

/**
 * Polymorphic button. `as="a"` renders an anchor (preserves all <a> props).
 * Variants: primary | ghost | subtle | danger
 * Sizes:    sm | md | lg
 */
const Button = forwardRef(function Button(
  { as: Tag = 'button', variant = 'subtle', size = 'md', loading, icon, children, className = '', ...rest }, ref
) {
  const cls = [
    'btn',
    `btn-${variant}`,
    size !== 'md' && `btn-${size}`,
    loading && 'is-loading',
    className,
  ].filter(Boolean).join(' ')

  return (
    <Tag ref={ref} className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <span className="btn-spinner" aria-hidden="true" /> : icon}
      <span>{children}</span>
    </Tag>
  )
})

export default Button
