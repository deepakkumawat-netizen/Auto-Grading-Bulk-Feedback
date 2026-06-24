// Inline SVG hero — stack of answer sheets + AI brain + checkmarks
export default function HeroIllustration() {
  return (
    <svg viewBox="0 0 600 480" xmlns="http://www.w3.org/2000/svg" className="hero-svg">
      <defs>
        <linearGradient id="bgGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%"  stopColor="#7c3aed" stopOpacity="0.15"/>
          <stop offset="100%" stopColor="#3b82f6" stopOpacity="0.05"/>
        </linearGradient>
        <linearGradient id="paperGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%"  stopColor="#ffffff"/>
          <stop offset="100%" stopColor="#f8fafc"/>
        </linearGradient>
        <linearGradient id="brainGrad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%"  stopColor="#6366f1"/>
          <stop offset="100%" stopColor="#a855f7"/>
        </linearGradient>
        <filter id="softShadow">
          <feDropShadow dx="0" dy="6" stdDeviation="8" floodOpacity="0.12"/>
        </filter>
      </defs>

      <circle cx="300" cy="240" r="220" fill="url(#bgGrad)"/>

      {/* Stack of papers */}
      <g filter="url(#softShadow)">
        <rect x="80"  y="140" width="220" height="280" rx="8" fill="url(#paperGrad)"
              stroke="#e2e8f0" strokeWidth="1" transform="rotate(-6 190 280)"/>
        <rect x="100" y="120" width="220" height="280" rx="8" fill="url(#paperGrad)"
              stroke="#e2e8f0" strokeWidth="1" transform="rotate(-2 210 260)"/>
        <rect x="120" y="100" width="220" height="280" rx="8" fill="#fff"
              stroke="#cbd5e1" strokeWidth="1.5"/>
      </g>

      {/* Lines of "writing" on the top paper */}
      <g stroke="#cbd5e1" strokeWidth="3" strokeLinecap="round">
        <line x1="140" y1="135" x2="280" y2="135"/>
        <line x1="140" y1="155" x2="320" y2="155"/>
        <line x1="140" y1="175" x2="260" y2="175"/>
        <line x1="140" y1="205" x2="320" y2="205"/>
        <line x1="140" y1="225" x2="290" y2="225"/>
        <line x1="140" y1="245" x2="310" y2="245"/>
        <line x1="140" y1="275" x2="280" y2="275"/>
      </g>

      {/* Big green checkmark on top paper */}
      <g transform="translate(255, 165)">
        <circle cx="35" cy="35" r="32" fill="#10b981"/>
        <path d="M20 36 L31 46 L52 24" stroke="#fff" strokeWidth="5"
              strokeLinecap="round" strokeLinejoin="round" fill="none"/>
      </g>

      {/* AI brain on the right */}
      <g transform="translate(380, 130)" filter="url(#softShadow)">
        <circle cx="80" cy="80" r="80" fill="url(#brainGrad)"/>
        {/* Brain lines */}
        <g stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round">
          <path d="M40 70 Q50 50 80 50 Q110 50 120 70"/>
          <path d="M40 90 Q60 80 80 90 Q100 80 120 90"/>
          <path d="M40 110 Q50 130 80 130 Q110 130 120 110"/>
          <circle cx="55" cy="70" r="3" fill="#fff"/>
          <circle cx="80" cy="80" r="3" fill="#fff"/>
          <circle cx="105" cy="70" r="3" fill="#fff"/>
        </g>
        {/* Sparkles around brain */}
        <g fill="#fde047">
          <path d="M150 30 l5 -12 l5 12 l12 5 l-12 5 l-5 12 l-5 -12 l-12 -5 z"/>
          <path d="M-15 50 l3 -7 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 z"/>
          <path d="M155 140 l3 -7 l3 7 l7 3 l-7 3 l-3 7 l-3 -7 l-7 -3 z"/>
        </g>
      </g>

      {/* Floating mark numbers */}
      <g fontFamily="Plus Jakarta Sans, sans-serif" fontWeight="700">
        <g transform="translate(45, 90)">
          <rect width="56" height="32" rx="16" fill="#10b981"/>
          <text x="28" y="22" textAnchor="middle" fill="#fff" fontSize="14">A+</text>
        </g>
        <g transform="translate(490, 320)">
          <rect width="56" height="32" rx="16" fill="#3b82f6"/>
          <text x="28" y="22" textAnchor="middle" fill="#fff" fontSize="14">9/10</text>
        </g>
        <g transform="translate(40, 380)">
          <rect width="56" height="32" rx="16" fill="#f59e0b"/>
          <text x="28" y="22" textAnchor="middle" fill="#fff" fontSize="14">8/10</text>
        </g>
      </g>
    </svg>
  )
}
