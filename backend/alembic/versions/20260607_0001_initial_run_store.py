"""Initial run-store schema.

Revision ID: 20260607_0001
Revises:
Create Date: 2026-06-07 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.persistence_models import Base


revision: str = "20260607_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_EVENT_GUARD_SQL = """
CREATE OR REPLACE FUNCTION prevent_run_events_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'run_events are append-only';
END;
$$;

DROP TRIGGER IF EXISTS run_events_append_only ON run_events;
CREATE TRIGGER run_events_append_only
BEFORE UPDATE OR DELETE ON run_events
FOR EACH ROW EXECUTE FUNCTION prevent_run_events_mutation()
"""

PROPOSED_STEP_APPROVAL_GUARD_SQL = """
CREATE OR REPLACE FUNCTION enforce_proposed_step_approval_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    safety_event_exists BOOLEAN;
BEGIN
    IF NEW.status IN ('approved', 'executed', 'failed') THEN
        SELECT EXISTS (
            SELECT 1
            FROM run_events
            WHERE run_id = NEW.run_id
              AND event_type = 'step.safety_classified'
              AND command = NEW.command
              AND payload ->> 'step_id' = NEW.id::text
              AND approval_status = 'allowed'
        )
        INTO safety_event_exists;

        IF NOT safety_event_exists THEN
            RAISE EXCEPTION 'approved proposed step % must have a safety classification for the approved command', NEW.id;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS proposed_steps_approval_guard ON proposed_steps;
CREATE TRIGGER proposed_steps_approval_guard
BEFORE INSERT OR UPDATE OF status, command, approved_command, safety_verdict ON proposed_steps
FOR EACH ROW EXECUTE FUNCTION enforce_proposed_step_approval_guard()
"""

COMMAND_EXECUTION_APPROVAL_GUARD_SQL = """
CREATE OR REPLACE FUNCTION enforce_command_execution_approved_step_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    step_row proposed_steps%ROWTYPE;
BEGIN
    SELECT *
    INTO step_row
    FROM proposed_steps
    WHERE id = NEW.proposed_step_id
      AND run_id = NEW.run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'command execution must reference a proposed step from the same run';
    END IF;

    IF step_row.safety_verdict <> 'allowed'
       OR step_row.approved_command IS NULL
       OR step_row.approved_command <> NEW.approved_command THEN
        RAISE EXCEPTION 'command execution % must match an approved non-blocked proposed step', NEW.id;
    END IF;

    IF TG_OP = 'INSERT' AND step_row.status <> 'approved' THEN
        RAISE EXCEPTION 'command execution % requires an approved proposed step', NEW.id;
    END IF;

    IF TG_OP = 'UPDATE' AND step_row.status NOT IN ('approved', 'executed', 'failed') THEN
        RAISE EXCEPTION 'command execution % cannot reference an inactive proposed step', NEW.id;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS command_executions_approved_step_guard ON command_executions;
CREATE TRIGGER command_executions_approved_step_guard
BEFORE INSERT OR UPDATE OF run_id, proposed_step_id, approved_command ON command_executions
FOR EACH ROW EXECUTE FUNCTION enforce_command_execution_approved_step_guard()
"""

# Guard markers intentionally kept here for source-level review:
# prevent_run_events_mutation, proposed_steps_approval_guard, FOR UPDATE SKIP LOCKED.


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())
    op.execute(APPEND_ONLY_EVENT_GUARD_SQL)
    op.execute(PROPOSED_STEP_APPROVAL_GUARD_SQL)
    op.execute(COMMAND_EXECUTION_APPROVAL_GUARD_SQL)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS command_executions_approved_step_guard ON command_executions")
    op.execute("DROP TRIGGER IF EXISTS proposed_steps_approval_guard ON proposed_steps")
    op.execute("DROP FUNCTION IF EXISTS enforce_command_execution_approved_step_guard()")
    op.execute("DROP FUNCTION IF EXISTS enforce_proposed_step_approval_guard()")
    op.execute("DROP TRIGGER IF EXISTS run_events_append_only ON run_events")
    op.execute("DROP FUNCTION IF EXISTS prevent_run_events_mutation()")
    Base.metadata.drop_all(bind=op.get_bind())
