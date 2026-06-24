import { useTheme } from '../providers/ThemeProvider.jsx'

/** Sun/Moon toggle. Animated, accessible. */
export default function ThemeToggle({ size = 'md' }) {
  const { theme, toggle } = useTheme()
  const isDark = theme === 'dark'
  return (
    <button
      type="button"
      className={`theme-toggle ${isDark ? 'is-dark' : 'is-light'} theme-toggle-${size}`}
      onClick={toggle}
      title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
    >
      <span className="tt-track">
        <span className="tt-thumb">
          <span className="tt-icon tt-sun">☀</span>
          <span className="tt-icon tt-moon">☾</span>
        </span>
      </span>
    </button>
  )
}
