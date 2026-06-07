from pydantic import BaseModel
from typing import Optional

class CustomerSystem(BaseModel):
    ip: str
    port: int
    username: str
    os: str
    notes: Optional[str] = None

class Ticket(BaseModel):
    id: int
    title: str
    description: str
    status: str
    priority: str
    customer_id: int
    created_at: str

class ActivityCreate(BaseModel):
    ticket_id: int
    start_datetime: str
    end_datetime: str
    summary: str
    root_cause: str
    actions_taken: str
    commands_summary: str
    validation_result: str
