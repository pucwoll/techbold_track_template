from __future__ import annotations

from datetime import UTC, datetime

from .schemas import ActivityDraft, BackupRecord, CommandExecution, InspectedSource, Run, RunEvent, ValidationResult


def build_activity_draft(
    *,
    run: Run,
    events: list[RunEvent],
    commands: list[CommandExecution],
    inspected_sources: list[InspectedSource],
    backup_records: list[BackupRecord],
    validation_results: list[ValidationResult],
) -> ActivityDraft:
    ticket_title = str(run.ticket_snapshot.get("title") or f"Ticket #{run.ticket_id}")
    customer_name = str(run.ticket_snapshot.get("customer_name") or "customer")
    start_datetime = run.started_at.isoformat()
    end_datetime = (run.ended_at or datetime.now(UTC)).isoformat()

    evidence_lines = _evidence_lines(inspected_sources)
    command_lines = _command_lines(commands)
    backup_lines = _backup_lines(backup_records)
    validation = _validation_result(run, commands, inspected_sources, validation_results)

    root_cause = _root_cause(inspected_sources, commands)
    actions = _actions_taken(events, inspected_sources, backup_records, validation_results)

    return ActivityDraft(
        ticket_id=run.ticket_id,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        description="\n".join(
            line
            for line in [
                f"Troubleshooting run #{run.id} for {customer_name}.",
                f"Ticket: {ticket_title}.",
                *evidence_lines,
                *backup_lines,
            ]
            if line
        ),
        summary=f"{ticket_title}: investigated via approved, audited commands for {customer_name}.",
        root_cause=root_cause,
        actions_taken=actions,
        commands_summary="\n".join(command_lines) if command_lines else "No SSH commands were executed.",
        validation_result=validation,
    )


def _command_lines(commands: list[CommandExecution]) -> list[str]:
    lines: list[str] = []
    for command in commands:
        status = command.status.value
        exit_code = "n/a" if command.exit_code is None else str(command.exit_code)
        duration = "n/a" if command.duration_ms is None else f"{command.duration_ms} ms"
        lines.append(f"- command execution #{command.id}: `{command.approved_command}` -> {status}, exit {exit_code}, {duration}")
    return lines


def _evidence_lines(sources: list[InspectedSource]) -> list[str]:
    lines: list[str] = []
    for source in sources:
        label = source.path or source.source_name or source.source_type
        lines.append(
            f"Evidence checked: inspected source #{source.id} from command execution #{source.command_execution_id} "
            f"({source.source_type} {label}) -> {source.finding}"
        )
    return lines


def _backup_lines(records: list[BackupRecord]) -> list[str]:
    lines: list[str] = []
    for record in records:
        if record.backup_type == "not_applicable":
            lines.append(f"Backup record #{record.id}: not applicable for {record.source_path or 'change'}: {record.reason}")
        elif record.backup_created:
            lines.append(f"Backup record #{record.id}: rollback available for {record.source_path}: {record.restore_command}")
    return lines


def _root_cause(sources: list[InspectedSource], commands: list[CommandExecution]) -> str:
    for source in sources:
        if source.supports == "root_cause":
            label = source.path or source.source_name or source.source_type
            return f"inspected source #{source.id} from command execution #{source.command_execution_id} ({label}) indicated: {source.finding}"
    for command in commands:
        output = (command.sanitized_stderr or command.sanitized_stdout).strip()
        if output:
            return f"Technician review required: command execution #{command.id} produced evidence but no inspected source was marked as root cause."
    return "Technician review required: no concrete root-cause evidence was recorded."


def _actions_taken(
    events: list[RunEvent],
    sources: list[InspectedSource],
    backup_records: list[BackupRecord],
    validation_results: list[ValidationResult],
) -> str:
    approval_events = [event for event in events if event.event_type in {"step.approved", "step.edited_and_approved"}]
    evidence_ids = [source.id for source in sources]
    backup_ids = [record.id for record in backup_records if record.backup_created or record.backup_type == "not_applicable"]
    validation_ids = [result.id for result in validation_results]
    parts = [
        f"Technician approval event IDs: {_id_list(event.id for event in approval_events)}.",
        f"Inspected-source IDs used: {_id_list(evidence_ids)}.",
    ]
    if backup_ids:
        parts.append(f"Backup record IDs considered: {_id_list(backup_ids)}.")
    if validation_ids:
        parts.append(f"Validation result IDs considered: {_id_list(validation_ids)}.")
    return " ".join(parts)


def _validation_result(
    run: Run,
    commands: list[CommandExecution],
    sources: list[InspectedSource],
    validation_results: list[ValidationResult],
) -> str:
    passed_results = [result for result in validation_results if result.passed]
    if passed_results:
        primary = next(
            (result for result in reversed(passed_results) if result.check_type == "customer_benefit"),
            passed_results[-1],
        )
        source = _source_for_validation_result(sources, primary)
        source_text = ""
        if source:
            label = source.path or source.source_name or source.source_type
            source_text = f" Inspected source #{source.id} ({label}) recorded this validation."
        suite_text = " ".join(
            f"{result.check_type} result #{result.id}: {result.summary}"
            for result in passed_results
            if result.id != primary.id
        )
        return (
            f"validation result #{primary.id} from command execution #{primary.command_execution_id}: {primary.summary}"
            f"{source_text}"
            f"{' Supporting validation suite: ' + suite_text if suite_text else ''}"
        )
    for result in reversed(validation_results):
        return result.summary
    if run.validation_result:
        return run.validation_result
    for source in reversed(sources):
        if source.supports == "validation":
            label = source.path or source.source_name or source.source_type
            return f"Validation evidence from source #{source.id} ({label}): {source.finding}"
    for command in reversed(commands):
        if "is-active" in command.approved_command or command.approved_command.strip().startswith("curl "):
            output = (command.sanitized_stdout or command.sanitized_stderr).strip()
            return f"`{command.approved_command}` returned {output or 'no output'}."
    return "Validation has not been recorded yet; technician review required before final submission."


def _source_for_validation_result(
    sources: list[InspectedSource],
    result: ValidationResult,
) -> InspectedSource | None:
    for source in reversed(sources):
        if source.command_execution_id == result.command_execution_id and source.supports == "validation":
            return source
    for source in reversed(sources):
        if source.command_execution_id == result.command_execution_id:
            return source
    for source in reversed(sources):
        if source.supports == "validation":
            return source
    return None


def _id_list(ids: object) -> str:
    values = [str(value) for value in ids]
    return ", ".join(values) if values else "none"
