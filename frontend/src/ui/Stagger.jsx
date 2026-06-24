import { Children } from 'react'

/**
 * Stagger children with a small entrance delay each.
 *   <Stagger gap={70}>
 *     <Item/><Item/><Item/>
 *   </Stagger>
 * Each child gets a CSS class `stagger-in` and inline animation-delay.
 */
export default function Stagger({ children, gap = 80, initialDelay = 0, as: Tag = 'div', className = '', ...rest }) {
  return (
    <Tag className={className} {...rest}>
      {Children.map(children, (child, i) => {
        if (!child) return null
        const delay = initialDelay + i * gap
        return (
          <div className="stagger-in" style={{ animationDelay: `${delay}ms` }}>
            {child}
          </div>
        )
      })}
    </Tag>
  )
}
