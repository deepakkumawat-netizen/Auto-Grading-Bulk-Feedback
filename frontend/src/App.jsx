import { Suspense, lazy } from 'react'
import ToastProvider from './providers/ToastProvider.jsx'
import ThemeProvider from './providers/ThemeProvider.jsx'
import { useHashRoute } from './hooks/useHashRoute.js'
import Skeleton from './ui/Skeleton.jsx'

const Landing = lazy(() => import('./pages/Landing.jsx'))
const Grader  = lazy(() => import('./pages/Grader.jsx'))

function PageFallback() {
  return (
    <div className="page-fallback">
      <Skeleton w={420} h={32} radius={10} />
      <div style={{ height: 12 }} />
      <Skeleton w={680} h={20} count={3} radius={8} />
    </div>
  )
}

export default function App() {
  const [route, navigate] = useHashRoute('#/')
  const view = route === '#/grader' ? 'grader' : 'home'

  return (
    <ThemeProvider>
      <ToastProvider>
        <div className="app">
          <Suspense fallback={<PageFallback />}>
            <div key={view} className="route-in">
              {view === 'grader'
                ? <Grader onHome={() => navigate('#/')} />
                : <Landing onStart={() => navigate('#/grader')} />}
            </div>
          </Suspense>
        </div>
      </ToastProvider>
    </ThemeProvider>
  )
}
