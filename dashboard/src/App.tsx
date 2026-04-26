import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import Overview from './pages/Overview'
import Matches from './pages/Matches'
import Opportunities from './pages/Opportunities'
import Players from './pages/Players'
import Settings from './pages/Settings'

const nav = [
  { to: '/', label: 'Overview' },
  { to: '/matches', label: 'Live Matches' },
  { to: '/opportunities', label: 'Opportunities' },
  { to: '/players', label: 'Players' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col">
        <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center gap-8">
          <span className="text-green-400 font-bold text-lg tracking-wide">🎾 Tennis Arb</span>
          <nav className="flex gap-6">
            {nav.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === '/'}
                className={({ isActive }) =>
                  `text-sm transition-colors ${isActive ? 'text-green-400' : 'text-gray-400 hover:text-gray-200'}`
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </header>
        <main className="flex-1 p-6">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/matches" element={<Matches />} />
            <Route path="/opportunities" element={<Opportunities />} />
            <Route path="/players" element={<Players />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
