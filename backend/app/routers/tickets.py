from fastapi import APIRouter, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from datetime import datetime
from app.services.erp import erp_client
from app.services.ssh import ssh_runner
from app.services.safety import safety_layer
from app.agent.run_manager import get_or_create_run, AuditLogEntry, active_runs
from app.agent.orchestrator import orchestrator
from typing import Optional

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

@router.get("/")
async def list_tickets(status: Optional[str] = None, priority: Optional[str] = None, sort: Optional[str] = None):
    try:
        return await erp_client.get_my_tickets(status, priority, sort)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{ticket_id}")
async def get_ticket_details(ticket_id: int):
    try:
        ticket = await erp_client.get_ticket(ticket_id)
        system_response = await erp_client.get_customer_system(ticket_id)
        return {"ticket": ticket, "system": system_response["system"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Agent run endpoints ---

@router.get("/{ticket_id}/run")
async def get_run_status(ticket_id: int):
    run = get_or_create_run(ticket_id)
    return run

async def step_agent(ticket_id: int):
    run = get_or_create_run(ticket_id)
    if run.status != "analyzing":
        return

    try:
        ticket = await erp_client.get_ticket(ticket_id)
        system = await erp_client.get_customer_system(ticket_id)
        
        run.logs.append(AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            role="system",
            content="Agent is analyzing the problem..."
        ))
        
        next_step = await orchestrator.get_next_step(ticket, system, run.logs)
        
        if next_step.get("type") == "report":
            run.status = "report_ready"
            run.final_report = next_step
            run.logs.append(AuditLogEntry(
                timestamp=datetime.utcnow().isoformat(),
                role="agent",
                content=f"I have successfully verified the fix. I am ready to submit the final report.\nReasoning: {next_step.get('reasoning', '')}"
            ))
        else:
            run.status = "waiting_approval"
            run.proposed_command = next_step.get("command")
            run.proposed_reasoning = next_step.get("reasoning")
            run.logs.append(AuditLogEntry(
                timestamp=datetime.utcnow().isoformat(),
                role="agent",
                content=f"Proposed step: {run.proposed_reasoning}",
                command=run.proposed_command
            ))
    except Exception as e:
        run.logs.append(AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            role="system",
            content=f"Agent encountered an error: {str(e)}"
        ))
        run.status = "analyzing" # try again later

@router.post("/{ticket_id}/run/start")
async def start_run(ticket_id: int, bg_tasks: BackgroundTasks):
    run = get_or_create_run(ticket_id)
    if run.status == "analyzing" and len(run.logs) == 1:
        bg_tasks.add_task(step_agent, ticket_id)
    return run

class ApproveRequest(BaseModel):
    command: str

@router.post("/{ticket_id}/run/approve")
async def approve_command(ticket_id: int, req: ApproveRequest, bg_tasks: BackgroundTasks):
    run = get_or_create_run(ticket_id)
    if run.status != "waiting_approval":
        raise HTTPException(status_code=400, detail="Not waiting for approval")
    
    command = req.command
    is_safe, reason = safety_layer.is_safe(command)
    if not is_safe:
        run.logs.append(AuditLogEntry(
            timestamp=datetime.utcnow().isoformat(),
            role="system",
            content=f"BLOCKED BY SAFETY LAYER: {reason}"
        ))
        run.status = "analyzing"
        bg_tasks.add_task(step_agent, ticket_id)
        return {"status": "blocked", "reason": reason}

    run.status = "running"
    run.logs.append(AuditLogEntry(
        timestamp=datetime.utcnow().isoformat(),
        role="human",
        content="Approved command execution."
    ))

    async def run_command():
        try:
            system_response = await erp_client.get_customer_system(ticket_id)
            sys_info = system_response["system"]
            output = await ssh_runner.execute(sys_info["ip"], sys_info["port"], command, ticket_id)
            
            run.logs.append(AuditLogEntry(
                timestamp=datetime.utcnow().isoformat(),
                role="system",
                content="Command output received.",
                output=output
            ))
            run.status = "analyzing"
            run.proposed_command = None
            run.proposed_reasoning = None
            # Agent analyzes the output and proposes next step
            await step_agent(ticket_id)
        except Exception as e:
            run.logs.append(AuditLogEntry(
                timestamp=datetime.utcnow().isoformat(),
                role="system",
                content=f"Execution failed: {str(e)}"
            ))
            run.status = "analyzing"
            run.proposed_command = None
            run.proposed_reasoning = None
            await step_agent(ticket_id)

    bg_tasks.add_task(run_command)
    return {"status": "running"}

@router.post("/{ticket_id}/run/reject")
async def reject_command(ticket_id: int, bg_tasks: BackgroundTasks):
    run = get_or_create_run(ticket_id)
    if run.status != "waiting_approval":
        raise HTTPException(status_code=400, detail="Not waiting for approval")
    
    run.logs.append(AuditLogEntry(
        timestamp=datetime.utcnow().isoformat(),
        role="human",
        content="Rejected proposed command. Asking agent to try another approach."
    ))
    run.status = "analyzing"
    run.proposed_command = None
    run.proposed_reasoning = None
    bg_tasks.add_task(step_agent, ticket_id)
    return {"status": "rejected"}

@router.post("/{ticket_id}/run/submit-activity")
async def submit_activity(ticket_id: int):
    run = get_or_create_run(ticket_id)
    if run.status != "report_ready" or not run.final_report:
        raise HTTPException(status_code=400, detail="Report is not ready yet.")
    
    # We submit to ERP
    try:
        activity_payload = {
            "ticket_id": ticket_id,
            "start_datetime": run.logs[0].timestamp,
            "end_datetime": datetime.utcnow().isoformat() + "Z",
            "summary": run.final_report.get("summary", "Resolved"),
            "root_cause": run.final_report.get("root_cause", "Unknown"),
            "actions_taken": run.final_report.get("actions_taken", "See logs"),
            "commands_summary": run.final_report.get("commands_summary", ""),
            "validation_result": run.final_report.get("validation_result", "Validated by Agent")
        }
        await erp_client.create_activity(activity_payload)
        run.status = "done"
        return {"status": "done"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit to ERP: {str(e)}")