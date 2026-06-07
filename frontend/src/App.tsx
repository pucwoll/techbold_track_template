import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import TicketView from './pages/TicketView';
import { Shield } from 'lucide-react';

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-[var(--bg-base)] text-[var(--text-primary)] font-sans flex flex-col">
        {/* Top Navigation Bar with Glowing Bottom Border */}
        <nav className="sticky top-0 z-50 bg-[var(--bg-base)]/80 backdrop-blur-xl border-b border-[var(--border-subtle)] px-6 py-4">
          <div className="max-w-[1600px] mx-auto flex items-center justify-between">
            <Link to="/" className="flex items-center gap-3 group">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-indigo-600 flex items-center justify-center shadow-[0_0_15px_rgba(59,130,246,0.5)] group-hover:shadow-[0_0_25px_rgba(59,130,246,0.7)] transition-shadow">
                <Shield size={16} className="text-white" />
              </div>
              <div className="flex flex-col">
                <span className="font-bold text-sm tracking-widest uppercase text-white/90 group-hover:text-white transition-colors">Techbold</span>
                <span className="text-[10px] font-mono text-blue-400/80 uppercase tracking-widest">Autopilot v2.0</span>
              </div>
            </Link>
            
            <div className="flex items-center gap-6 text-sm font-medium text-[var(--text-secondary)]">
              <Link to="/" className="hover:text-white transition-colors">Dashboard</Link>
              <div className="h-4 w-px bg-[var(--border-strong)]"></div>
              <div className="flex items-center gap-2">
                <div className="w-5 h-5 rounded-full bg-gradient-to-tr from-purple-500 to-pink-500 border border-white/10"></div>
                <span className="text-xs">Technician</span>
              </div>
            </div>
          </div>
          {/* Subtle gradient line below nav */}
          <div className="absolute bottom-0 left-0 right-0 h-[1px] bg-gradient-to-r from-transparent via-blue-500/20 to-transparent"></div>
        </nav>

        <main className="flex-1 flex flex-col">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/ticket/:id" element={<TicketView />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

export default App;
