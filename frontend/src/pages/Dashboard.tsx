import { useEffect, useState } from 'react';
import { getTickets } from '../lib/api';
import { Link } from 'react-router-dom';
import { Clock, AlertCircle, Search, Filter, Hash, ChevronRight, CheckCircle2, CircleDashed, ServerCrash } from 'lucide-react';

export default function Dashboard() {
  const [tickets, setTickets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  
  const [statusFilter, setStatusFilter] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  useEffect(() => {
    loadTickets();
  }, [statusFilter, priorityFilter]);

  const loadTickets = async () => {
    setLoading(true);
    try {
      const data = await getTickets(statusFilter, priorityFilter, 'date');
      setTickets(data);
    } catch (err: any) {
      setError(err.message || 'Failed to load tickets');
    } finally {
      setLoading(false);
    }
  };

  const filteredTickets = tickets.filter(t => 
    t.title.toLowerCase().includes(searchQuery.toLowerCase()) || 
    t.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
    t.customer_name?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="relative min-h-full pb-20 w-full overflow-hidden">
      {/* Abstract Background Elements */}
      <div className="absolute top-0 left-1/4 w-[500px] h-[500px] bg-blue-600/10 rounded-full blur-[120px] pointer-events-none -z-10"></div>
      <div className="absolute bottom-0 right-1/4 w-[600px] h-[600px] bg-indigo-600/10 rounded-full blur-[150px] pointer-events-none -z-10"></div>
      <div className="absolute inset-0 bg-[url('https://grainy-gradients.vercel.app/noise.svg')] opacity-[0.015] pointer-events-none -z-10 mix-blend-overlay"></div>

      <div className="max-w-[1600px] mx-auto px-6 py-12 relative z-10">
        {/* Header Section */}
        <header className="mb-12 flex flex-col md:flex-row md:items-end justify-between gap-6">
          <div className="animate-in slide-in-from-bottom-4 duration-500 fade-in">
            <h1 className="text-5xl font-black tracking-tighter text-transparent bg-clip-text bg-gradient-to-r from-white to-white/60 mb-3">Command Center</h1>
            <p className="text-[var(--text-secondary)] text-lg font-medium max-w-2xl">Monitor, triage, and resolve incoming infrastructure anomalies with the AI Autopilot.</p>
          </div>
          <div className="flex items-center gap-4 animate-in slide-in-from-bottom-4 duration-500 delay-100 fade-in fill-mode-both">
            <div className="bg-[var(--bg-elevated)] border border-[var(--border-strong)] px-4 py-2.5 rounded-full flex items-center gap-3 shadow-lg shadow-black/50 backdrop-blur-md">
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"></span>
              </span>
              <span className="text-xs font-mono font-bold tracking-widest text-emerald-400 uppercase">System Online</span>
            </div>
          </div>
        </header>

        {/* Filters & Search Toolbar */}
        <div className="bg-[var(--bg-elevated)]/80 backdrop-blur-xl p-2 rounded-2xl border border-[var(--border-subtle)] shadow-2xl mb-10 flex flex-col md:flex-row gap-2 items-center animate-in slide-in-from-bottom-4 duration-500 delay-200 fade-in fill-mode-both">
          <div className="relative w-full md:w-[400px]">
            <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500" size={18} />
            <input 
              type="text" 
              placeholder="Search by issue, customer, or ID..." 
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-11 pr-4 py-3 bg-black/40 border border-transparent focus:bg-black/60 focus:border-blue-500/50 rounded-xl text-sm transition-all outline-none font-medium placeholder:text-gray-600 text-white shadow-inner"
            />
          </div>
          
          <div className="flex gap-2 w-full md:w-auto ml-auto">
            <div className="relative flex-1 md:w-48">
              <Filter className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500" size={16} />
              <select 
                className="w-full pl-11 pr-10 py-3 bg-black/40 border border-[var(--border-subtle)] hover:border-[var(--border-strong)] focus:border-blue-500/50 rounded-xl text-sm appearance-none outline-none font-medium cursor-pointer transition-colors text-gray-300 shadow-inner"
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value)}
              >
                <option value="" className="bg-[#111]">Status: All</option>
                <option value="OPEN" className="bg-[#111]">Status: Open</option>
                <option value="PENDING" className="bg-[#111]">Status: Pending</option>
                <option value="DONE" className="bg-[#111]">Status: Done</option>
              </select>
              <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none">
                 <svg width="10" height="6" viewBox="0 0 10 6" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M1 1L5 5L9 1" stroke="#666" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </div>
            </div>
            <div className="relative flex-1 md:w-48">
              <AlertCircle className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500" size={16} />
              <select 
                className="w-full pl-11 pr-10 py-3 bg-black/40 border border-[var(--border-subtle)] hover:border-[var(--border-strong)] focus:border-blue-500/50 rounded-xl text-sm appearance-none outline-none font-medium cursor-pointer transition-colors text-gray-300 shadow-inner"
                value={priorityFilter}
                onChange={e => setPriorityFilter(e.target.value)}
              >
                <option value="" className="bg-[#111]">Priority: All</option>
                <option value="URGENT" className="bg-[#111]">Priority: Urgent</option>
                <option value="HIGH" className="bg-[#111]">Priority: High</option>
                <option value="NORMAL" className="bg-[#111]">Priority: Normal</option>
              </select>
              <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none">
                 <svg width="10" height="6" viewBox="0 0 10 6" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M1 1L5 5L9 1" stroke="#666" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
              </div>
            </div>
          </div>
        </div>

        {/* Content Area */}
        {loading ? (
          <div className="flex flex-col justify-center items-center py-40">
            <div className="w-12 h-12 border-4 border-blue-500/20 border-t-blue-500 rounded-full animate-spin mb-4"></div>
            <p className="text-gray-500 font-mono text-sm uppercase tracking-widest animate-pulse">Syncing Telemetry...</p>
          </div>
        ) : error ? (
          <div className="bg-red-500/10 text-red-400 p-8 rounded-2xl border border-red-500/20 flex flex-col items-center justify-center text-center">
            <ServerCrash className="mb-4 text-red-500/50" size={48} />
            <h3 className="font-bold text-lg mb-2">Connection Severed</h3>
            <span className="font-mono text-sm max-w-md">{error}</span>
          </div>
        ) : filteredTickets.length === 0 ? (
          <div className="text-center py-40 bg-[var(--bg-elevated)]/30 rounded-3xl border border-[var(--border-subtle)] border-dashed backdrop-blur-sm">
            <CircleDashed className="mx-auto text-gray-600 mb-6" size={56} strokeWidth={1} />
            <h3 className="text-2xl font-bold text-white mb-3">Zero active anomalies</h3>
            <p className="text-gray-500 max-w-sm mx-auto">No incidents match your current filtering criteria, or the queue is entirely clear.</p>
          </div>
        ) : (
          <div className="grid gap-4 grid-cols-1">
            {filteredTickets.map((ticket, idx) => (
              <Link 
                key={ticket.id} 
                to={`/ticket/${ticket.id}`}
                className="group block relative bg-[var(--bg-panel)] rounded-2xl p-1 shadow-lg border border-[var(--border-subtle)] hover:border-[var(--border-strong)] transition-all duration-300 animate-in slide-in-from-bottom-4 fade-in fill-mode-both"
                style={{ animationDelay: `${(idx % 10) * 50 + 300}ms` }}
              >
                {/* Glow effect on hover */}
                <div className="absolute inset-0 bg-gradient-to-r from-blue-500/0 via-blue-500/0 to-blue-500/0 group-hover:from-blue-600/5 group-hover:via-indigo-500/5 group-hover:to-purple-500/5 rounded-2xl transition-all duration-500 pointer-events-none"></div>
                
                <div className="relative bg-[#0c0c0e] rounded-xl p-6 h-full flex flex-col lg:flex-row lg:items-center justify-between gap-6 overflow-hidden">
                  
                  {/* Neon Left Border */}
                  <div className={`absolute left-0 top-0 bottom-0 w-1 transition-all duration-300 ${
                    ticket.priority === 'URGENT' ? 'bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.5)]' :
                    ticket.priority === 'HIGH' ? 'bg-amber-500 shadow-[0_0_10px_rgba(245,158,11,0.5)]' :
                    'bg-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.5)]'
                  } opacity-70 group-hover:opacity-100 group-hover:w-1.5`}></div>
                  
                  <div className="flex-1 pl-4 z-10">
                    <div className="flex flex-wrap items-center gap-3 mb-3">
                      <span className="font-mono text-xs font-bold text-gray-500 tracking-wider flex items-center bg-black/50 px-2 py-1 rounded-md border border-[var(--border-subtle)]">
                        <Hash size={12} className="mr-0.5 opacity-50" />{ticket.id}
                      </span>
                      {ticket.customer_name && (
                        <span className="px-2.5 py-1 bg-white/5 text-gray-300 text-xs font-semibold rounded-md border border-white/5 backdrop-blur-sm">
                          {ticket.customer_name}
                        </span>
                      )}
                      <span className="flex items-center gap-1.5 text-xs font-mono text-gray-500 bg-black/30 px-2 py-1 rounded-md">
                        <Clock size={12} /> 
                        {ticket.created_at ? new Date(ticket.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Unknown Date'}
                      </span>
                    </div>
                    
                    <h2 className="text-xl font-bold text-gray-100 mb-2 group-hover:text-white transition-colors line-clamp-1 pr-4">
                      {ticket.title}
                    </h2>
                    <p className="text-gray-500 text-sm line-clamp-1 pr-12 font-medium">
                      {ticket.description}
                    </p>
                  </div>
                  
                  <div className="flex items-center gap-6 lg:justify-end z-10 pl-4 lg:pl-0 border-t lg:border-t-0 lg:border-l border-[var(--border-subtle)] pt-4 lg:pt-0">
                    <div className="flex flex-row lg:flex-col items-center lg:items-end gap-3 lg:gap-2 w-full lg:w-auto">
                      <span className={`px-3 py-1.5 rounded-md text-[10px] font-mono font-bold uppercase tracking-widest flex items-center gap-1.5 ${
                        ticket.status === 'DONE' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 
                        ticket.status === 'OPEN' ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' : 
                        'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                      }`}>
                        {ticket.status === 'DONE' && <CheckCircle2 size={12} />}
                        {ticket.status}
                      </span>
                      <span className={`px-3 py-1.5 rounded-md text-[10px] font-mono font-bold uppercase tracking-widest flex items-center gap-1.5 border ${
                        ticket.priority === 'URGENT' ? 'bg-red-500/10 text-red-400 border-red-500/20' : 
                        ticket.priority === 'HIGH' ? 'bg-orange-500/10 text-orange-400 border-orange-500/20' : 
                        'bg-gray-500/10 text-gray-400 border-gray-500/20'
                      }`}>
                        <AlertCircle size={12} strokeWidth={2.5} /> {ticket.priority}
                      </span>
                    </div>
                    
                    <div className="w-12 h-12 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-strong)] flex items-center justify-center text-gray-500 group-hover:bg-white group-hover:text-black group-hover:border-white transition-all shadow-lg shrink-0">
                      <ChevronRight size={20} strokeWidth={2.5} className="group-hover:translate-x-0.5 transition-transform" />
                    </div>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}