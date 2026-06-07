import { useEffect, useState, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { getTicketDetails, getRunStatus, startRun, approveCommand, rejectCommand, submitActivity } from '../lib/api';
import { ArrowLeft, Server, Terminal, Play, CheckCircle2, XCircle, FileText, Activity, Clock, Hash, ShieldAlert, Cpu, Network, User } from 'lucide-react';

export default function TicketView() {
  const { id } = useParams<{ id: string }>();
  const ticketId = Number(id);
  const [data, setData] = useState<any>(null);
  const [run, setRun] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [editableCommand, setEditableCommand] = useState('');
  const logsEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadTicket();
  }, [ticketId]);

  useEffect(() => {
    let interval: any;
    if (data) {
      loadRun();
      interval = setInterval(loadRun, 2000);
    }
    return () => clearInterval(interval);
  }, [data]);

  useEffect(() => {
    if (run?.proposed_command && run.status === 'waiting_approval') {
      setEditableCommand(run.proposed_command);
    }
    logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [run]);

  const loadTicket = async () => {
    try {
      const res = await getTicketDetails(ticketId);
      setData(res);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const loadRun = async () => {
    try {
      const res = await getRunStatus(ticketId);
      setRun(res);
    } catch (err) {
      console.error(err);
    }
  };

  const handleStart = async () => {
    await startRun(ticketId);
    loadRun();
  };

  const handleApprove = async () => {
    await approveCommand(ticketId, editableCommand);
    loadRun();
  };

  const handleReject = async () => {
    await rejectCommand(ticketId);
    loadRun();
  };

  const handleSubmitReport = async () => {
    await submitActivity(ticketId);
    loadRun();
    loadTicket();
  };

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <div className="w-12 h-12 border-4 border-blue-500/20 border-t-blue-500 rounded-full animate-spin"></div>
        <p className="text-[var(--text-secondary)] font-mono text-sm uppercase tracking-widest animate-pulse">Establishing Uplink...</p>
      </div>
    </div>
  );
  if (!data) return <div className="p-8 text-red-500 flex justify-center w-full"><div className="bg-red-500/10 p-6 rounded-2xl border border-red-500/20">Error loading ticket context.</div></div>;

  const { ticket, system } = data;

  return (
    <div className="relative min-h-full pb-12 w-full overflow-hidden">
      {/* Abstract Background Elements */}
      <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-purple-600/10 rounded-full blur-[120px] pointer-events-none -z-10"></div>
      <div className="absolute bottom-1/4 left-0 w-[400px] h-[400px] bg-blue-600/10 rounded-full blur-[120px] pointer-events-none -z-10"></div>
      
      <div className="max-w-[1600px] mx-auto px-6 pt-8 grid grid-cols-1 xl:grid-cols-12 gap-8 relative z-10">
        
        {/* Left Column: Context (Ticket + System) */}
        <div className="xl:col-span-4 space-y-6 flex flex-col h-[calc(100vh-8rem)]">
          <Link to="/" className="inline-flex items-center gap-2 text-[var(--text-secondary)] hover:text-white font-medium transition-colors mb-2 w-fit">
            <ArrowLeft size={16} /> Back to Command Center
          </Link>
          
          {/* Ticket Card */}
          <div className="bg-[var(--bg-panel)] p-6 rounded-2xl shadow-xl border border-[var(--border-subtle)] relative overflow-hidden group">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-purple-500 opacity-50"></div>
            <div className="flex items-center justify-between mb-5">
              <span className="font-mono text-xs font-bold text-gray-500 tracking-wider flex items-center bg-black/40 px-2.5 py-1 rounded-md border border-[var(--border-subtle)]">
                <Hash size={12} className="mr-0.5 opacity-50" />{ticket.id}
              </span>
              <div className="flex gap-2">
                <span className={`px-2.5 py-1 rounded-md text-[10px] font-mono font-bold uppercase tracking-widest flex items-center gap-1.5 ${
                  ticket.status === 'DONE' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 
                  ticket.status === 'OPEN' ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' : 
                  'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                }`}>
                  {ticket.status === 'DONE' && <CheckCircle2 size={12} />}
                  {ticket.status}
                </span>
              </div>
            </div>
            
            <h2 className="text-2xl font-black text-white mb-4 tracking-tight leading-snug">{ticket.title}</h2>
            
            <div className="bg-black/40 p-4 rounded-xl border border-[var(--border-subtle)] mb-6 shadow-inner">
              <p className="text-gray-300 text-sm leading-relaxed font-medium">
                {ticket.description}
              </p>
            </div>
            
            <div className="flex items-center justify-between border-t border-[var(--border-subtle)] pt-4">
               <div className="flex items-center gap-2 text-xs font-mono font-medium text-gray-500">
                <Clock size={14} /> {new Date(ticket.created_at).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
               </div>
               {ticket.customer_name && (
                 <span className="text-xs font-bold text-gray-400 bg-white/5 px-2.5 py-1 rounded-md">
                   {ticket.customer_name}
                 </span>
               )}
            </div>
          </div>

          {/* System Card */}
          <div className="bg-[var(--bg-panel)] p-6 rounded-2xl shadow-xl border border-[var(--border-subtle)] flex-1 relative overflow-hidden flex flex-col">
            <h3 className="flex items-center gap-2 font-bold mb-6 text-white tracking-tight">
              <Server size={18} className="text-blue-500" /> Target Environment
            </h3>
            
            <div className="grid grid-cols-1 gap-y-4 flex-1">
              <div className="bg-black/30 border border-[var(--border-subtle)] p-3.5 rounded-xl flex items-center justify-between group hover:border-blue-500/30 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
                    <Network size={16} />
                  </div>
                  <span className="text-xs font-bold text-gray-500 uppercase tracking-widest">IP / Host</span>
                </div>
                <span className="font-mono text-sm font-medium text-gray-200">{system.ip}:{system.port}</span>
              </div>
              
              <div className="bg-black/30 border border-[var(--border-subtle)] p-3.5 rounded-xl flex items-center justify-between group hover:border-purple-500/30 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-purple-500/10 flex items-center justify-center text-purple-400">
                    <User size={16} />
                  </div>
                  <span className="text-xs font-bold text-gray-500 uppercase tracking-widest">User</span>
                </div>
                <span className="font-mono text-sm font-medium text-gray-200">{system.username}</span>
              </div>
              
              <div className="bg-black/30 border border-[var(--border-subtle)] p-3.5 rounded-xl flex items-center justify-between group hover:border-emerald-500/30 transition-colors">
                <div className="flex items-center gap-3">
                  <div className="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center text-emerald-400">
                    <Cpu size={16} />
                  </div>
                  <span className="text-xs font-bold text-gray-500 uppercase tracking-widest">OS</span>
                </div>
                <span className="text-sm font-medium text-gray-200">{system.os}</span>
              </div>
            </div>
            
            {system.notes && (
              <div className="mt-6 pt-5 border-t border-[var(--border-subtle)]">
                <p className="text-[10px] text-gray-500 font-bold tracking-widest uppercase mb-2">Internal Notes</p>
                <p className="text-sm text-gray-400 italic bg-black/20 p-3 rounded-lg border border-[var(--border-subtle)]">{system.notes}</p>
              </div>
            )}
          </div>
        </div>

        {/* Right Column: Agent Workspace & Terminal */}
        <div className="xl:col-span-8 bg-[#0a0a0c] rounded-2xl flex flex-col h-[calc(100vh-8rem)] shadow-2xl border border-[var(--border-strong)] overflow-hidden relative backdrop-blur-3xl">
          
          {/* Terminal Header */}
          <div className="bg-black/40 backdrop-blur-md px-6 py-4 border-b border-[var(--border-strong)] flex items-center justify-between z-20">
            <div className="flex items-center gap-4">
              <div className="flex gap-2 mr-2">
                <div className="w-3 h-3 rounded-full bg-red-500/50 hover:bg-red-500 transition-colors"></div>
                <div className="w-3 h-3 rounded-full bg-yellow-500/50 hover:bg-yellow-500 transition-colors"></div>
                <div className="w-3 h-3 rounded-full bg-green-500/50 hover:bg-green-500 transition-colors"></div>
              </div>
              <div className="h-4 w-px bg-[var(--border-strong)]"></div>
              <Terminal size={16} className="text-gray-400" />
              <h3 className="font-mono text-sm font-bold text-gray-200 tracking-tight">AUTOPILOT_TERMINAL</h3>
            </div>
            {run && (
              <div className="flex items-center gap-3 bg-black/50 px-3 py-1.5 rounded-full border border-[var(--border-subtle)]">
                <span className="relative flex h-2 w-2">
                  {(run.status === 'analyzing' || run.status === 'running') && (
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                  )}
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${
                    run.status === 'done' ? 'bg-emerald-500' :
                    run.status === 'waiting_approval' ? 'bg-yellow-500' : 'bg-blue-500'
                  }`}></span>
                </span>
                <span className="text-[10px] uppercase font-mono font-bold tracking-widest text-gray-300">
                  {run.status.replace('_', ' ')}
                </span>
              </div>
            )}
          </div>
          
          {/* Terminal Output */}
          <div className="flex-1 overflow-y-auto p-6 font-mono text-[13px] leading-relaxed space-y-6 scroll-smooth bg-black/20">
            {(!run || run.logs.length === 0) ? (
               <div className="h-full flex flex-col items-center justify-center opacity-50 select-none">
                 <Terminal size={48} className="text-gray-700 mb-4" strokeWidth={1} />
                 <span className="text-gray-500 font-mono tracking-widest text-sm uppercase">Awaiting Initialization...</span>
               </div>
            ) : (
              run.logs.map((log: any, i: number) => (
                <div key={i} className={`flex flex-col group ${
                  log.role === 'agent' ? 'items-start' : 
                  log.role === 'human' ? 'items-end' : 
                  'items-start opacity-70'
                }`}>
                  <div className="flex items-center gap-2 mb-2 opacity-60 group-hover:opacity-100 transition-opacity">
                    <span className="text-[10px] text-gray-500">
                      {new Date(log.timestamp).toLocaleTimeString([], { hour12: false })}
                    </span>
                    <span className={`text-[10px] font-bold uppercase tracking-widest px-2 py-0.5 rounded-sm ${
                      log.role === 'agent' ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20' :
                      log.role === 'human' ? 'bg-purple-500/10 text-purple-400 border border-purple-500/20' : 
                      'bg-gray-500/10 text-gray-400 border border-gray-500/20'
                    }`}>
                      {log.role}
                    </span>
                  </div>
                  
                  <div className={`max-w-[90%] font-sans text-[14px] leading-relaxed ${
                    log.role === 'agent' ? 'text-gray-200' : 
                    log.role === 'human' ? 'text-gray-100 bg-white/5 border border-[var(--border-subtle)] px-5 py-3 rounded-2xl rounded-tr-sm shadow-lg backdrop-blur-sm' : 
                    'text-gray-400 italic'
                  }`}>
                    {log.content}
                  </div>
                  
                  {log.command && (
                    <div className="mt-3 w-full max-w-[90%] bg-[#050505] border border-[#1a1a1a] rounded-xl p-4 text-emerald-400 font-mono text-[13px] shadow-[inset_0_0_20px_rgba(0,0,0,0.5)] relative overflow-hidden">
                      <div className="absolute top-0 left-0 w-1 h-full bg-emerald-500/50"></div>
                      <span className="text-gray-600 mr-3 select-none">❯</span>
                      {log.command}
                    </div>
                  )}
                  {log.output && (
                    <div className="mt-2 w-full max-w-[90%] bg-black/80 border border-[#1a1a1a] rounded-xl p-4 text-gray-400 text-xs whitespace-pre-wrap overflow-x-auto shadow-inner">
                      {log.output}
                    </div>
                  )}
                </div>
              ))
            )}
            <div ref={logsEndRef} className="h-4" />
          </div>

          {/* Action Area (Bottom) */}
          <div className="bg-black/60 backdrop-blur-xl border-t border-[var(--border-strong)] p-6 z-20 shadow-[0_-20px_40px_rgba(0,0,0,0.5)] relative">
            {!run || (run.status === 'analyzing' && run.logs.length <= 1) ? (
              <button onClick={handleStart} className="group w-full flex items-center justify-center gap-3 bg-white hover:bg-gray-100 text-black font-extrabold py-4 px-6 rounded-xl transition-all shadow-[0_0_30px_rgba(255,255,255,0.1)] hover:shadow-[0_0_40px_rgba(255,255,255,0.2)] active:scale-[0.99] uppercase tracking-widest text-sm">
                <Activity size={18} className="group-hover:animate-pulse" /> Initialize Diagnostics
              </button>
            ) : run.status === 'analyzing' || run.status === 'running' ? (
              <div className="flex flex-col items-center justify-center gap-4 py-4 bg-white/5 rounded-xl border border-[var(--border-subtle)]">
                <div className="flex gap-2">
                  <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce [animation-delay:-0.3s] shadow-[0_0_10px_rgba(59,130,246,0.8)]"></div>
                  <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce [animation-delay:-0.15s] shadow-[0_0_10px_rgba(59,130,246,0.8)]"></div>
                  <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce shadow-[0_0_10px_rgba(59,130,246,0.8)]"></div>
                </div>
                <span className="text-xs font-mono font-bold text-blue-400 uppercase tracking-widest">
                  {run.status === 'running' ? 'Executing Command Sequence...' : 'Synthesizing Next Action...'}
                </span>
              </div>
            ) : run.status === 'waiting_approval' ? (
              <div className="space-y-4 animate-in slide-in-from-bottom-4 duration-300">
                <div className="flex items-start gap-4 bg-yellow-500/10 border border-yellow-500/20 p-5 rounded-xl relative overflow-hidden">
                  <div className="absolute top-0 left-0 w-1 h-full bg-yellow-500"></div>
                  <ShieldAlert className="text-yellow-500 shrink-0 mt-0.5" size={20} />
                  <div>
                    <h4 className="text-yellow-500 font-bold text-[11px] uppercase tracking-widest mb-1.5">Action Requires Authorization</h4>
                    <p className="text-sm text-gray-300 font-medium leading-relaxed">{run.proposed_reasoning}</p>
                  </div>
                </div>
                <div className="relative group">
                  <div className="absolute inset-0 bg-blue-500/20 rounded-xl blur-md opacity-0 group-focus-within:opacity-100 transition-opacity"></div>
                  <span className="absolute left-5 top-1/2 -translate-y-1/2 text-emerald-500 font-mono text-lg font-bold select-none">❯</span>
                  <input 
                    type="text"
                    value={editableCommand}
                    onChange={e => setEditableCommand(e.target.value)}
                    className="w-full relative bg-[#050505] text-emerald-400 pl-11 pr-5 py-4 rounded-xl font-mono text-sm border border-[var(--border-strong)] focus:border-blue-500/50 outline-none transition-all shadow-inner placeholder:text-gray-700"
                    placeholder="Enter command..."
                    spellCheck="false"
                  />
                </div>
                <div className="flex gap-4">
                  <button onClick={handleReject} className="flex-[0.3] flex items-center justify-center gap-2 bg-[#111] hover:bg-[#1a1a1a] border border-[var(--border-strong)] text-gray-300 font-bold py-4 px-4 rounded-xl transition-colors hover:text-red-400 group">
                    <XCircle size={18} className="group-hover:scale-110 transition-transform" /> Reject
                  </button>
                  <button onClick={handleApprove} className="flex-[0.7] flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-500 text-white font-bold py-4 px-4 rounded-xl transition-all shadow-[0_0_20px_rgba(37,99,235,0.3)] hover:shadow-[0_0_30px_rgba(37,99,235,0.5)] active:scale-[0.99] group">
                    <CheckCircle2 size={18} className="group-hover:scale-110 transition-transform" /> Authorize & Execute
                  </button>
                </div>
              </div>
            ) : run.status === 'report_ready' ? (
               <div className="space-y-4 animate-in slide-in-from-bottom-4 duration-300">
                  <div className="flex items-start gap-4 bg-emerald-500/10 border border-emerald-500/20 p-5 rounded-xl relative overflow-hidden">
                    <div className="absolute top-0 left-0 w-1 h-full bg-emerald-500"></div>
                    <CheckCircle2 className="text-emerald-500 shrink-0 mt-0.5" size={20} />
                    <div>
                      <h4 className="text-emerald-500 font-bold text-[11px] uppercase tracking-widest mb-1.5">Resolution Validated</h4>
                      <p className="text-sm text-gray-300 font-medium">Autopilot has drafted the final activity report. Review JSON payload before committing to ERP.</p>
                    </div>
                  </div>
                  <div className="bg-[#050505] border border-[var(--border-strong)] p-5 rounded-xl text-xs text-gray-400 font-mono h-40 overflow-y-auto shadow-inner relative group">
                    <div className="absolute right-4 top-4 text-[10px] text-gray-600 uppercase font-bold select-none">Payload</div>
                    <pre className="text-gray-300">{JSON.stringify(run.final_report, null, 2)}</pre>
                  </div>
                  <button onClick={handleSubmitReport} className="w-full flex items-center justify-center gap-2 bg-white hover:bg-gray-100 text-black font-extrabold py-4 px-4 rounded-xl transition-all shadow-[0_0_20px_rgba(255,255,255,0.1)] active:scale-[0.99] group uppercase tracking-widest text-sm">
                    <FileText size={18} className="group-hover:-translate-y-0.5 transition-transform" /> Commit Activity to ERP
                  </button>
               </div>
            ) : run.status === 'done' ? (
              <div className="flex flex-col items-center justify-center gap-3 py-8 bg-emerald-500/5 rounded-xl border border-emerald-500/10">
                <div className="w-12 h-12 bg-emerald-500/20 rounded-full flex items-center justify-center text-emerald-500 mb-2">
                  <CheckCircle2 size={24} strokeWidth={3} />
                </div>
                <span className="font-bold tracking-tight text-white text-lg">Incident Resolved</span>
                <span className="text-sm text-gray-500 font-medium">Activity securely logged to Phoenix ERP</span>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}