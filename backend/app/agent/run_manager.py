from pydantic import BaseModel
from typing import List, Optional, Dict
from datetime import datetime

class AuditLogEntry(BaseModel):
    timestamp: str
    role: str # "agent", "human", "system"
    content: str
    command: Optional[str] = None
    output: Optional[str] = None

class TicketRun(BaseModel):
    ticket_id: int
    status: str # "analyzing", "waiting_approval", "running", "done", "report_ready"
    logs: List[AuditLogEntry] = []
    proposed_command: Optional[str] = None
    proposed_reasoning: Optional[str] = None
    final_report: Optional[dict] = None

active_runs: Dict[int, TicketRun] = {}

def get_or_create_run(ticket_id: int) -> TicketRun:
    if ticket_id not in active_runs:
        active_runs[ticket_id] = TicketRun(ticket_id=ticket_id, status="analyzing")
        active_runs[ticket_id].logs.append(AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            role="system",
            content="Agent run initialized."
        ))
    return active_runs[ticket_id]
