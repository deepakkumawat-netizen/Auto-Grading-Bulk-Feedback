/**
 * Compound Card.
 *   <Card>
 *     <Card.Header eyebrow="Step 1" title="Pick scope" hint="Grade + chapter" />
 *     <Card.Body>...</Card.Body>
 *     <Card.Footer>...</Card.Footer>
 *   </Card>
 */
function Card({ children, className = '', glow = false, ...rest }) {
  return (
    <section className={`card ${glow ? 'card-glow' : ''} ${className}`} {...rest}>
      {children}
    </section>
  )
}

Card.Header = function CardHeader({ eyebrow, title, hint, action }) {
  return (
    <header className="card-head">
      <div>
        {eyebrow && <div className="card-eyebrow">{eyebrow}</div>}
        {title && <h3 className="card-title">{title}</h3>}
        {hint && <div className="card-hint">{hint}</div>}
      </div>
      {action && <div className="card-action">{action}</div>}
    </header>
  )
}

Card.Body = function CardBody({ children, className = '' }) {
  return <div className={`card-body ${className}`}>{children}</div>
}

Card.Footer = function CardFooter({ children }) {
  return <footer className="card-foot">{children}</footer>
}

export default Card
