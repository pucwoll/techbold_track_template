from __future__ import annotations

import json
import re
import shlex
from typing import Any, Literal, Protocol

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from .activity_generator import build_activity_draft
from .safety_layer import redact_output
from .schemas import (
    BackupRecord,
    CommandExecution,
    CommandExecutionStatus,
    InspectedSource,
    JsonObject,
    Run,
    RunEvent,
    ValidationExpectation,
    ValidationResult,
)


class PlannerAdapter(Protocol):
    def propose(self, context: JsonObject) -> JsonObject | str:
        ...


class PlannerStep(BaseModel):
    phase: Literal["diagnostic", "fix", "validation"]
    command: str = Field(min_length=1, max_length=4_000)
    purpose: str = Field(min_length=1, max_length=1_000)
    hypothesis: str = Field(min_length=1, max_length=1_000)
    expected_signal: str = Field(min_length=1, max_length=1_000)
    risk_level: Literal["read_only", "low", "medium", "requires_review"]
    requires_service_restart: bool = False
    persistence_consideration: str = Field(min_length=1, max_length=1_000)
    rollback_plan: str = Field(min_length=1, max_length=1_000)
    stop_if: str = Field(min_length=1, max_length=1_000)
    evidence_references: list[str] = Field(default_factory=list)
    needs_more_diagnosis: bool = False
    diagnosis_gap: str = ""

    @model_validator(mode="after")
    def require_fix_evidence_or_diagnosis_gap(self) -> "PlannerStep":
        if self.phase != "fix":
            return self
        if self.evidence_references:
            return self
        if self.needs_more_diagnosis and self.diagnosis_gap.strip():
            return self
        raise ValueError("Fix planner output requires evidence_references or a diagnosis_gap before fixes.")


class PlannerPhaseDecision(BaseModel):
    previous_phase: Literal["none", "diagnostic", "fix", "validation"]
    next_phase: Literal["diagnostic", "fix", "validation"]
    reason: str = Field(min_length=1, max_length=1_000)
    latest_command_id: int | None = None
    service: str | None = None


class PlannerOutputError(ValueError):
    pass


_SENSITIVE_CONTEXT_KEY_RE = re.compile(r"(secret|token|password|passwd|api[_-]?key|credential|private[_-]?key)", re.IGNORECASE)
LISTENING_TCP_DIAGNOSTIC_COMMAND = "ss -H -ltn"


class OpenAIChatPlannerAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_s: float = 20.0,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.client = client or OpenAI(api_key=api_key, base_url=self.base_url, timeout=timeout_s)

    def propose(self, context: JsonObject) -> JsonObject:
        completion = self.client.chat.completions.parse(
            model=self.model,
            temperature=0,
            response_format=PlannerStep,
            messages=self._messages(context),
        )
        try:
            message = completion.choices[0].message
        except (AttributeError, IndexError, TypeError) as error:
            raise PlannerOutputError("Planner provider response did not include message content") from error
        parsed = getattr(message, "parsed", None)
        if isinstance(parsed, PlannerStep):
            return parsed.model_dump(mode="json")
        if isinstance(parsed, BaseModel):
            return parse_planner_output(parsed.model_dump(mode="json")).model_dump(mode="json")
        if isinstance(parsed, dict):
            return parse_planner_output(parsed).model_dump(mode="json")
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            raise PlannerOutputError("Planner provider message content must be a JSON string")
        return parse_planner_output(content).model_dump(mode="json")

    def _messages(self, context: JsonObject) -> list[JsonObject]:
        return [
            {
                "role": "system",
                "content": (
                    "You are a service desk incident planner. Propose exactly one next SSH command as strict JSON. "
                    "Never execute commands. Never include secrets. Prefer read-only diagnostics until evidence supports a fix."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(_redact_planner_context(context), ensure_ascii=True),
            },
        ]


def build_planner_context(
    *,
    run: Run,
    events: list[RunEvent],
    commands: list[CommandExecution],
    inspected_sources: list[InspectedSource],
    backup_records: list[BackupRecord] | None = None,
    validation_results: list[ValidationResult] | None = None,
    validation_expectations: list[ValidationExpectation] | None = None,
) -> JsonObject:
    evidence = [
        {
            "source_type": source.source_type,
            "source_name": source.source_name,
            "path": source.path,
            "finding": source.finding,
            "supports": source.supports,
        }
        for source in inspected_sources[-10:]
    ]
    backups = backup_records or []
    validations = validation_results or []
    expectations = validation_expectations or []
    sanitized_timeline = [
        {
            "event_type": event.event_type,
            "actor": event.actor,
            "summary": event.summary,
            "command": event.command,
            "risk_class": event.risk_class,
            "error": event.error,
        }
        for event in events[-20:]
    ]
    safety_rules = (
        "The planner may propose exactly one non-interactive SSH command. "
        "The backend safety layer classifies it and a technician approval is required before execution. "
        "Hard-block destructive blanket changes, secret reads, log/history clearing, remote script piping, "
        "unrestricted shells, and credential-store access."
    )
    return {
        "ticket_snapshot": run.ticket_snapshot,
        "customer_system_snapshot": run.customer_system_snapshot,
        "run_status": run.status.value,
        "current_hypotheses": run.current_hypotheses,
        "sanitized_timeline": sanitized_timeline,
        "audit_summary": sanitized_timeline,
        "recent_command_results": [
            {
                "command": command.approved_command,
                "status": command.status.value,
                "exit_code": command.exit_code,
                "stdout_excerpt": command.sanitized_stdout[-600:],
                "stderr_excerpt": command.sanitized_stderr[-600:],
            }
            for command in commands[-6:]
        ],
        "latest_evidence": evidence,
        "inspected_sources": evidence,
        "backup_state": {
            "has_backup": any(record.backup_created for record in backups),
            "required_count": len([record for record in backups if record.backup_required]),
            "created_count": len([record for record in backups if record.backup_created]),
            "not_applicable_count": len([record for record in backups if record.backup_type == "not_applicable"]),
            "latest_records": [
                {
                    "source_path": record.source_path,
                    "backup_path": record.backup_path,
                    "backup_type": record.backup_type,
                    "backup_required": record.backup_required,
                    "backup_created": record.backup_created,
                    "restore_command": record.restore_command,
                }
                for record in backups[-5:]
            ],
        },
        "validation_state": {
            "has_passed_validation": any(result.passed for result in validations),
            "required_expectations": [
                {
                    "check_type": expectation.check_type,
                    "target": expectation.target,
                    "expected_result": expectation.expected_result,
                    "relation_to_customer_symptom": expectation.relation_to_customer_symptom,
                    "required": expectation.required,
                    "status": expectation.status,
                }
                for expectation in expectations[-8:]
            ],
            "latest_result": (
                {
                    "check_type": validations[-1].check_type,
                    "target": validations[-1].target,
                    "passed": validations[-1].passed,
                    "summary": validations[-1].summary,
                    "evidence": validations[-1].evidence,
                }
                if validations
                else None
            ),
            "results": [
                {
                    "check_type": result.check_type,
                    "target": result.target,
                    "passed": result.passed,
                    "summary": result.summary,
                }
                for result in validations[-5:]
            ],
        },
        "safety_rules": safety_rules,
        "safety_policy_summary": safety_rules,
        "required_output_schema": PlannerStep.model_json_schema(),
    }


def _redact_planner_context(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if _SENSITIVE_CONTEXT_KEY_RE.search(key_text):
                redacted[key_text] = "[REDACTED_SECRET]"
            else:
                redacted[key_text] = _redact_planner_context(nested)
        return redacted
    if isinstance(value, list):
        return [_redact_planner_context(item) for item in value]
    if isinstance(value, str):
        return redact_output(value)[0]
    return value


def ticket_analyzer(run: Run) -> JsonObject:
    description = str(run.ticket_snapshot.get("description") or "")
    title = str(run.ticket_snapshot.get("title") or f"Ticket #{run.ticket_id}")
    notes = str(run.customer_system_snapshot.get("system", {}).get("notes", ""))
    combined_text = " ".join([title, description, notes])
    services = _service_candidates(combined_text)
    symptom_categories = _symptom_categories(combined_text)
    system = run.customer_system_snapshot.get("system", {})
    likely_ports: list[int] = []
    likely_ports.extend(int(match) for match in re.findall(r"\bport\s+(\d{2,5})\b", combined_text.lower()))
    if isinstance(system, dict) and isinstance(system.get("port"), int):
        likely_ports.append(int(system["port"]))
    if any(service in {"nginx", "apache2"} for service in services):
        likely_ports.extend([80, 443])
    return {
        "symptom": title,
        "customer_benefit": description or title,
        "service_candidates": services,
        "symptom_categories": symptom_categories,
        "likely_ports": sorted(set(likely_ports)),
        "initial_uncertainty": "low" if services else "medium",
    }


def system_context_planner(run: Run, ticket_analysis: JsonObject | None = None) -> PlannerStep:
    analysis = ticket_analysis or ticket_analyzer(run)
    categories = analysis.get("symptom_categories") if isinstance(analysis.get("symptom_categories"), list) else []
    if "disk" in categories:
        return _step(
            phase="diagnostic",
            command="df -h /",
            purpose="Check root filesystem capacity before changing services or files.",
            hypothesis="Resource pressure from a full filesystem may explain the reported failure.",
            expected_signal="Filesystem usage should show whether disk space is exhausted.",
            risk_level="read_only",
        )
    if "memory" in categories:
        return _step(
            phase="diagnostic",
            command="free -m",
            purpose="Check memory pressure before choosing a service-specific fix.",
            hypothesis="Memory exhaustion may explain the customer-facing failure.",
            expected_signal="Memory totals should show whether available memory is critically low.",
            risk_level="read_only",
        )
    if "port" in categories:
        return _step(
            phase="diagnostic",
            command=LISTENING_TCP_DIAGNOSTIC_COMMAND,
            purpose="Inspect listening TCP sockets for the customer-facing port.",
            hypothesis="A missing listener or wrong process on the expected port may explain the outage.",
            expected_signal="The expected port should be present in the listening socket list.",
            risk_level="read_only",
        )
    if "permission" in categories:
        return _step(
            phase="diagnostic",
            command="stat /var/www",
            purpose="Inspect common web-root metadata before any permission change.",
            hypothesis="A wrong owner or mode on the served path may explain permission-related symptoms.",
            expected_signal="Metadata should show whether ownership or permissions are inconsistent.",
            risk_level="read_only",
        )
    service = _first_service_candidate(analysis) or _infer_service(run)
    if service:
        return _step(
            phase="diagnostic",
            command=f"systemctl status {service} --no-pager",
            purpose=f"Check the suspected {service} service state before making changes.",
            hypothesis=f"The ticket points to {service}; service state may reveal the first actionable symptom.",
            expected_signal="Service status should show whether the unit is active, failed, or misconfigured.",
            risk_level="read_only",
        )
    return _step(
        phase="diagnostic",
        command="systemctl --failed",
        purpose="Check failed systemd units before making changes.",
        hypothesis="A failed service may identify the customer-facing component behind the incident.",
        expected_signal="Failed units identify which service should be inspected next.",
        risk_level="read_only",
    )


def observation_interpreter(
    run: Run,
    latest_command: CommandExecution,
    latest_step: PlannerStep | Any | None = None,
) -> JsonObject:
    output = f"{latest_command.sanitized_stdout}\n{latest_command.sanitized_stderr}".lower()
    service = _service_from_command(latest_command.approved_command) or _infer_service(run)
    if "bind()" in output or "address already in use" in output:
        root_cause = f"{service or 'service'} reported a bind/listen failure."
        confidence = "high"
        next_action = "fix"
    elif _looks_restartable(output):
        root_cause = f"{service or 'service'} appears failed or inactive."
        confidence = "medium"
        next_action = "fix"
    elif _looks_healthy(output):
        root_cause = f"{service or 'service'} appears healthy; validate customer-facing behavior."
        confidence = "medium"
        next_action = "validation"
    else:
        root_cause = "More diagnosis is needed before selecting a fix."
        confidence = "low"
        next_action = "diagnostic"
    return {
        "root_cause_candidate": root_cause,
        "confidence": confidence,
        "supporting_evidence": [latest_command.approved_command],
        "contradicting_evidence": [],
        "inspected_sources": [],
        "next_best_action": next_action,
        "latest_phase": getattr(latest_step, "phase", None),
    }


def fix_planner(
    run: Run,
    *,
    observation: JsonObject | None = None,
    latest_command: CommandExecution | None = None,
    commands: list[CommandExecution] | None = None,
) -> PlannerStep:
    service = (
        _service_from_command(latest_command.approved_command)
        if latest_command is not None
        else None
    ) or _service_from_command_history(commands or []) or _infer_service(run) or "customer-status"
    root_cause = str((observation or {}).get("root_cause_candidate") or f"{service} service failure is the leading candidate.")
    evidence_references = [f"command_execution:{latest_command.id}"] if latest_command is not None else []
    latest_output = (
        f"{latest_command.sanitized_stdout}\n{latest_command.sanitized_stderr}".lower()
        if latest_command is not None
        else ""
    )
    if "disabled" in latest_output and any(marker in latest_output for marker in ("inactive", "dead", "failed")):
        return _step(
            phase="fix",
            command=f"sudo -n systemctl enable --now {service}",
            purpose=f"Enable and start the affected {service} service after evidence showed it was installed but disabled.",
            hypothesis=root_cause,
            expected_signal="The unit should become enabled and active, restoring the customer-facing endpoint.",
            risk_level="medium",
            requires_service_restart=True,
            persistence_consideration="This persistently enables the service across reboot and starts it immediately.",
            rollback_plan=f"Restore the captured service state with systemctl disable --now {service} if validation fails.",
            evidence_references=evidence_references,
        )
    return _step(
        phase="fix",
        command=f"sudo -n systemctl restart {service}",
        purpose=f"Restart only the affected {service} service after evidence showed a service-level failure.",
        hypothesis=root_cause,
        expected_signal="Restart should exit 0 and subsequent validation should report active service.",
        risk_level="low",
        requires_service_restart=True,
        persistence_consideration="Restart does not change persistent configuration.",
        rollback_plan="No file rollback required; service can be stopped or restarted again if needed.",
        evidence_references=evidence_references,
    )


def validation_planner(
    run: Run,
    *,
    service: str | None = None,
    latest_command: CommandExecution | None = None,
) -> PlannerStep:
    target_service = service or (
        _service_from_command(latest_command.approved_command)
        if latest_command is not None
        else None
    ) or _infer_service(run)
    if target_service:
        return _step(
            phase="validation",
            command=f"systemctl is-active {target_service}",
            purpose="Validate the affected service is active after the approved fix.",
            hypothesis="The minimal fix should restore the service to an active state.",
            expected_signal="The command should return active with exit code 0.",
            risk_level="read_only",
        )
    return _step(
        phase="validation",
        command=_endpoint_validation_command(run),
        purpose="Validate customer-facing behavior through a local endpoint check.",
        hypothesis="The previous action should restore the customer-facing path.",
        expected_signal="The endpoint should return an HTTP success response or expected health payload.",
        risk_level="read_only",
    )


def activity_writer(
    *,
    run: Run,
    events: list[RunEvent],
    commands: list[CommandExecution],
    inspected_sources: list[InspectedSource],
    backup_records: list[BackupRecord] | None = None,
    validation_results: list[ValidationResult] | None = None,
) -> JsonObject:
    draft = build_activity_draft(
        run=run,
        events=events,
        commands=commands,
        inspected_sources=inspected_sources,
        backup_records=backup_records or [],
        validation_results=validation_results or [],
    )
    return draft.model_dump(mode="json")


def planner_phase_transition(
    *,
    run: Run,
    commands: list[CommandExecution],
    step_for_execution: Any,
) -> PlannerPhaseDecision:
    completed = [command for command in commands if command.completed_at is not None]
    service = _service_from_command_history(commands) or _infer_service(run)
    if not completed:
        return PlannerPhaseDecision(
            previous_phase="none",
            next_phase="diagnostic",
            reason="No completed command observations exist yet.",
            service=service,
        )

    latest = completed[-1]
    latest_step = step_for_execution(latest.proposed_step_id)
    previous_phase = getattr(latest_step, "phase", "diagnostic")
    if previous_phase not in {"diagnostic", "fix", "validation"}:
        previous_phase = "diagnostic"
    output = f"{latest.sanitized_stdout}\n{latest.sanitized_stderr}".lower()
    command_service = _service_from_command(latest.approved_command)
    service = command_service or service

    if previous_phase == "fix":
        if latest.exit_code not in {None, 0} or latest.status.value in {"failed", "timed_out"}:
            return PlannerPhaseDecision(
                previous_phase="fix",
                next_phase="diagnostic",
                reason="The approved fix did not complete successfully; diagnose the failure before validation.",
                latest_command_id=latest.id,
                service=service,
            )
        return PlannerPhaseDecision(
            previous_phase="fix",
            next_phase="validation",
            reason="A fix command completed; validation must prove service health and customer benefit.",
            latest_command_id=latest.id,
            service=service,
        )
    if previous_phase == "validation":
        return PlannerPhaseDecision(
            previous_phase="validation",
            next_phase="validation",
            reason="Validation is in progress; continue with customer-benefit or persistence validation.",
            latest_command_id=latest.id,
            service=service,
        )
    if _looks_restartable(output):
        return PlannerPhaseDecision(
            previous_phase="diagnostic",
            next_phase="fix",
            reason="Latest diagnostic output showed failed or inactive service evidence.",
            latest_command_id=latest.id,
            service=service,
        )
    if _looks_healthy(output):
        return PlannerPhaseDecision(
            previous_phase="diagnostic",
            next_phase="validation",
            reason="Latest diagnostic output looked healthy; validate the customer-facing symptom.",
            latest_command_id=latest.id,
            service=service,
        )
    return PlannerPhaseDecision(
        previous_phase="diagnostic",
        next_phase="diagnostic",
        reason="Latest diagnostic output did not justify a fix yet.",
        latest_command_id=latest.id,
        service=service,
    )


def parse_planner_output(raw_output: JsonObject | str) -> PlannerStep:
    if isinstance(raw_output, str):
        try:
            raw_output = json.loads(raw_output)
        except json.JSONDecodeError as error:
            raise PlannerOutputError(f"Planner did not return valid JSON: {error}") from error
    if not isinstance(raw_output, dict):
        raise PlannerOutputError("Planner output must be a JSON object")
    try:
        return PlannerStep.model_validate(raw_output)
    except ValidationError as error:
        raise PlannerOutputError(str(error)) from error


def deterministic_next_step(
    *,
    run: Run,
    commands: list[CommandExecution],
    step_for_execution: Any,
    validation_expectations: list[ValidationExpectation] | None = None,
) -> PlannerStep:
    if run.status.value == "validating" and validation_expectations:
        suite_step = _validation_step_for_expectations(run, validation_expectations)
        if suite_step:
            attempted = _commands_attempted_since_latest_fix(commands, step_for_execution)
            if _canonical_command(suite_step.command) not in attempted:
                return suite_step
            return _next_untried_validation(run, validation_expectations, attempted)
    attempted = {
        _canonical_command(command.approved_command)
        for command in commands
        if command.completed_at is not None
    }
    discovered_service = _ticket_relevant_installed_service(run, commands)
    if discovered_service:
        discovered_status = _step(
            phase="diagnostic",
            command=f"systemctl status {discovered_service} --no-pager",
            purpose=f"Inspect the ticket-relevant {discovered_service} service discovered on the host.",
            hypothesis=f"The installed {discovered_service} unit may own the unavailable customer-facing endpoint.",
            expected_signal="Service status should show whether the discovered unit is inactive, failed, or misconfigured.",
            risk_level="read_only",
        )
        if _canonical_command(discovered_status.command) not in attempted:
            return discovered_status
    candidate = _deterministic_next_step_candidate(
        run=run,
        commands=commands,
        step_for_execution=step_for_execution,
        validation_expectations=validation_expectations,
    )
    if _canonical_command(candidate.command) not in attempted:
        return candidate
    return _next_untried_diagnostic(run, commands, attempted)


def _commands_attempted_since_latest_fix(
    commands: list[CommandExecution],
    step_for_execution: Any,
) -> set[str]:
    completed = [command for command in commands if command.completed_at is not None]
    latest_fix_index = -1
    for index, command in enumerate(completed):
        step = step_for_execution(command.proposed_step_id)
        if step.phase == "fix" and command.status == CommandExecutionStatus.COMPLETED and command.exit_code == 0:
            latest_fix_index = index
    return {
        _canonical_command(command.approved_command)
        for command in completed[latest_fix_index + 1 :]
    }


def _next_untried_validation(
    run: Run,
    expectations: list[ValidationExpectation],
    attempted: set[str],
) -> PlannerStep:
    for expectation in expectations:
        if not expectation.required or expectation.status != "pending":
            continue
        candidate = _validation_step_for_expectations(run, [expectation])
        if candidate and _canonical_command(candidate.command) not in attempted:
            return candidate
    return continuation_diagnostic_step(run=run, commands=[], attempted=attempted)


def _deterministic_next_step_candidate(
    *,
    run: Run,
    commands: list[CommandExecution],
    step_for_execution: Any,
    validation_expectations: list[ValidationExpectation] | None = None,
) -> PlannerStep:
    decision = planner_phase_transition(run=run, commands=commands, step_for_execution=step_for_execution)
    service = decision.service or _infer_service(run)
    completed = [command for command in commands if command.completed_at is not None]
    if run.status.value == "validating" and validation_expectations:
        suite_step = _validation_step_for_expectations(run, validation_expectations)
        if suite_step:
            return suite_step
        if any(expectation.status == "failed" for expectation in validation_expectations if expectation.required):
            latest = completed[-1] if completed else None
            return fix_planner(
                run,
                observation={"root_cause_candidate": "A required validation check failed after the previous fix."},
                latest_command=latest,
            )
    if not completed:
        return system_context_planner(run, ticket_analyzer(run))

    latest = completed[-1]
    latest_step = step_for_execution(latest.proposed_step_id)
    output = f"{latest.sanitized_stdout}\n{latest.sanitized_stderr}".lower()
    command = latest.approved_command
    environment_file = _environment_file_from_commands(commands)
    expected_port = _ticket_endpoint_port(run)

    if environment_file and expected_port is not None:
        port_check = _port_environment_check_command(environment_file)
        if _canonical_command(port_check) not in {
            _canonical_command(item.approved_command) for item in completed
        }:
            return _step(
                phase="diagnostic",
                command=port_check,
                purpose=f"Inspect the non-secret listen port declared for {service or 'the application service'}.",
                hypothesis=f"The service may be healthy but configured for a port other than {expected_port}.",
                expected_signal=f"The PORT setting should equal {expected_port}.",
                risk_level="read_only",
            )

    configured_port = _port_setting_from_command(latest)
    if environment_file and expected_port is not None and configured_port is not None and configured_port != expected_port:
        return _step(
            phase="fix",
            command=(
                "sudo -n sed -i.techbold-prechange "
                f"'s/^PORT=[0-9]\\+$/PORT={expected_port}/' {shlex.quote(environment_file)}"
            ),
            purpose=(
                f"Correct the persistent {service or 'application'} listen port from "
                f"{configured_port} to the ticket-required port {expected_port}."
            ),
            hypothesis=(
                f"The service is running on port {configured_port}, while the customer endpoint requires {expected_port}."
            ),
            expected_signal="The environment file should retain the corrected port and persistence validation should restart the service.",
            risk_level="medium",
            requires_service_restart=True,
            persistence_consideration="The environment file change persists across service restarts and host reboots.",
            rollback_plan=f"Restore the automatic pre-change backup of {environment_file} if validation fails.",
            evidence_references=[f"command_execution:{latest.id}"],
        )

    if decision.next_phase == "fix":
        return fix_planner(
            run,
            observation=observation_interpreter(run, latest, latest_step),
            latest_command=latest,
            commands=commands,
        )

    if decision.next_phase == "validation" and latest_step.phase == "fix":
        return validation_planner(run, service=service or _service_from_command(command) or "nginx", latest_command=latest)

    if decision.next_phase == "validation" and latest_step.phase == "validation":
        if latest.exit_code not in {None, 0} or latest.status.value in {"failed", "timed_out"}:
            return _failed_validation_diagnostic(run, commands, service=service)
        return _step(
            phase="validation",
            command=_endpoint_validation_command(run),
            purpose="Validate customer-facing behavior through a local endpoint check.",
            hypothesis="Service-level validation should be paired with a customer-benefit signal.",
            expected_signal="The endpoint should return an HTTP success response or expected health payload.",
            risk_level="read_only",
        )

    if "systemctl status" in command and service:
        return _step(
            phase="diagnostic",
            command=f"journalctl -u {service} --no-pager -n 80",
            purpose=f"Inspect recent {service} journal entries for concrete error evidence.",
            hypothesis=f"The {service} journal should explain why the service is unhealthy or failing requests.",
            expected_signal="Recent journal entries should show the original error or confirm the service is clean.",
            risk_level="read_only",
        )

    if "journalctl" in command and service:
        return _step(
            phase="diagnostic",
            command=_config_check_command(service),
            purpose=f"Validate {service} configuration before any restart or edit.",
            hypothesis="Configuration validation can separate syntax/config issues from runtime issues.",
            expected_signal="Config test should either pass or identify an exact file and line.",
            risk_level="read_only",
        )

    if _looks_healthy(output):
        return _step(
            phase="validation",
            command=_endpoint_validation_command(run),
            purpose="Confirm the customer-facing path is reachable from the affected host.",
            hypothesis="The service appears healthy locally; endpoint validation should prove customer benefit.",
            expected_signal="The endpoint should return an HTTP success response or expected health payload.",
            risk_level="read_only",
        )

    if service:
        return _step(
            phase="diagnostic",
            command=f"journalctl -u {service} --no-pager -n 80",
            purpose=f"Inspect recent {service} logs before selecting a fix.",
            hypothesis=f"The next observation should provide evidence for or against a {service} root cause.",
            expected_signal="Recent logs should reveal the relevant error, or absence of one.",
            risk_level="read_only",
        )

    if _is_listening_tcp_diagnostic(command):
        return _step(
            phase="diagnostic",
            command="systemctl --failed",
            purpose="Check failed systemd units after the socket list did not identify the customer-facing listener.",
            hypothesis="A failed service may explain why the expected customer-facing port is not listening.",
            expected_signal="Failed units should identify the service to inspect next, or confirm no unit is failed.",
            risk_level="read_only",
        )

    return _step(
        phase="diagnostic",
        command=LISTENING_TCP_DIAGNOSTIC_COMMAND,
        purpose="Inspect listening TCP sockets to find the customer-facing port.",
        hypothesis="A missing or wrong listening socket can explain unreachable services.",
        expected_signal="Expected ports should be listening on the affected host.",
        risk_level="read_only",
    )


def _next_untried_diagnostic(
    run: Run,
    commands: list[CommandExecution],
    attempted: set[str],
) -> PlannerStep:
    discovered_service = _ticket_relevant_installed_service(run, commands)
    if discovered_service:
        service_status = _step(
            phase="diagnostic",
            command=f"systemctl status {discovered_service} --no-pager",
            purpose=f"Inspect the ticket-relevant {discovered_service} service discovered on the host.",
            hypothesis=f"The installed {discovered_service} unit may own the unavailable customer-facing endpoint.",
            expected_signal="Service status should show whether the discovered unit is inactive, failed, or misconfigured.",
            risk_level="read_only",
        )
        if _canonical_command(service_status.command) not in attempted:
            return service_status

    service = _service_from_command_history(commands) or _infer_service(run)
    candidates: list[PlannerStep] = []
    if service:
        candidates.extend(
            [
                _step(
                    phase="diagnostic",
                    command=f"systemctl status {service} --no-pager",
                    purpose=f"Inspect the current {service} service state.",
                    hypothesis=f"The {service} unit state may explain the unresolved customer-facing failure.",
                    expected_signal="Service status should provide a new state or error signal.",
                    risk_level="read_only",
                ),
                _step(
                    phase="diagnostic",
                    command=f"journalctl -u {service} --no-pager -n 80",
                    purpose=f"Inspect recent {service} logs for unresolved errors.",
                    hypothesis=f"Recent {service} logs may identify the next evidence-backed action.",
                    expected_signal="The journal should reveal a concrete error or confirm the unit is clean.",
                    risk_level="read_only",
                ),
            ]
        )
    candidates.extend(
        [
            _step(
                phase="diagnostic",
                command="systemctl list-units --type=service --state=running --no-pager",
                purpose="Identify running services that may own the customer-facing workload.",
                hypothesis="The workload may run under a service unit not identified by the ticket.",
                expected_signal="The running service list should identify a relevant application or proxy unit.",
                risk_level="read_only",
            ),
            _step(
                phase="diagnostic",
                command="ps -eo pid,comm,args --sort=comm",
                purpose="Inspect running processes after service checks were inconclusive.",
                hypothesis="The application may run outside a recognizable systemd service.",
                expected_signal="The process list should identify the application runtime or confirm it is absent.",
                risk_level="read_only",
            ),
            _step(
                phase="diagnostic",
                command="systemctl list-sockets --all --no-pager",
                purpose="Inspect socket-activated units for the missing customer-facing listener.",
                hypothesis="A socket unit may define the expected listener even when no service is currently active.",
                expected_signal="Socket units should reveal configured listeners and their activation state.",
                risk_level="read_only",
            ),
            _step(
                phase="diagnostic",
                command="systemctl list-unit-files --type=service --no-pager",
                purpose="Inspect installed service units for the missing application service.",
                hypothesis="The application service may be installed but disabled or inactive.",
                expected_signal="Installed unit names should identify the relevant application or proxy service.",
                risk_level="read_only",
            ),
        ]
    )
    for candidate in candidates:
        if _canonical_command(candidate.command) not in attempted:
            return candidate
    return continuation_diagnostic_step(run=run, commands=commands, attempted=attempted)


def _ticket_relevant_installed_service(
    run: Run,
    commands: list[CommandExecution],
) -> str | None:
    ticket_text = " ".join(
        str(value).lower()
        for value in (
            run.ticket_snapshot.get("title", ""),
            run.ticket_snapshot.get("description", ""),
        )
    )
    ignored_words = {
        "api",
        "cannot",
        "customer",
        "endpoint",
        "intermittently",
        "reach",
        "the",
        "unavailable",
    }
    ticket_words = {
        word
        for word in re.findall(r"[a-z0-9]+", ticket_text)
        if len(word) >= 3 and word not in ignored_words
    }
    matches: list[tuple[int, int, str]] = []
    for command in commands:
        if "systemctl list-unit-files" not in command.approved_command:
            continue
        for unit in re.findall(r"(?m)^([A-Za-z0-9_.@-]+)\.service\s+\S+", command.sanitized_stdout):
            unit_words = set(re.findall(r"[a-z0-9]+", unit.lower()))
            score = len(ticket_words & unit_words)
            if score:
                customer_status_bonus = 2 if {"customer", "status"} <= unit_words else 0
                matches.append((score + customer_status_bonus, -len(unit), unit))
    if not matches:
        return None
    return max(matches)[2]


def _failed_validation_diagnostic(
    run: Run,
    commands: list[CommandExecution],
    *,
    service: str | None,
) -> PlannerStep:
    attempted = {_canonical_command(command.approved_command) for command in commands}
    candidates: list[PlannerStep] = []
    if service:
        candidates.extend(
            [
                _step(
                    phase="diagnostic",
                    command=f"systemctl status {service} --no-pager",
                    purpose=f"Re-check {service} state after the customer-facing validation failed.",
                    hypothesis=f"{service} may be inactive or unhealthy despite the previous observations.",
                    expected_signal="Service status should reveal whether the failed validation is caused by the service state.",
                    risk_level="read_only",
                ),
                _step(
                    phase="diagnostic",
                    command=f"journalctl -u {service} --no-pager -n 80",
                    purpose=f"Inspect recent {service} logs after the customer-facing validation failed.",
                    hypothesis=f"Recent {service} logs may explain why the endpoint remains unavailable.",
                    expected_signal="Recent errors should identify the next evidence-backed fix or diagnostic.",
                    risk_level="read_only",
                ),
            ]
        )
    candidates.extend(
        [
            _step(
                phase="diagnostic",
                command="systemctl list-units --type=service --state=running --no-pager",
                purpose="Identify running services after the expected endpoint refused the connection.",
                hypothesis="The customer-facing service may be running under a unit not identified by the ticket.",
                expected_signal="The running unit list should identify the service that owns the customer-facing workload.",
                risk_level="read_only",
            ),
            _step(
                phase="diagnostic",
                command="ps -eo pid,comm,args --sort=comm",
                purpose="Inspect running processes after service and endpoint checks were inconclusive.",
                hypothesis="The application process may run outside a recognizable systemd unit.",
                expected_signal="The process list should identify the application runtime or confirm it is absent.",
                risk_level="read_only",
            ),
        ]
    )
    for candidate in candidates:
        if _canonical_command(candidate.command) not in attempted:
            return candidate
    return continuation_diagnostic_step(run=run, commands=commands, attempted=attempted)


def continuation_diagnostic_step(
    *,
    run: Run,
    commands: list[CommandExecution],
    attempted: set[str] | None = None,
) -> PlannerStep:
    attempted = attempted or {
        _canonical_command(command.approved_command)
        for command in commands
        if command.completed_at is not None
    }
    service = _service_from_command_history(commands)
    service = service or _ticket_relevant_installed_service(run, commands) or _infer_service(run) or "customer-status"
    endpoint = _ticket_endpoint(run) or "http://localhost"
    candidates = [
        _step(
            phase="diagnostic",
            command=f"systemctl cat {service}",
            purpose=f"Inspect the complete {service} unit definition for startup and persistence settings.",
            hypothesis=f"The {service} unit definition may reveal an incorrect command, dependency, or install target.",
            expected_signal="The unit file should expose ExecStart, dependencies, restart policy, and install target.",
            risk_level="read_only",
        ),
        _step(
            phase="diagnostic",
            command=(
                f"systemctl show {service} "
                "--property=LoadState,ActiveState,SubState,UnitFileState,ExecStart,Restart,NRestarts,Result --no-pager"
            ),
            purpose=f"Inspect detailed runtime properties for {service}.",
            hypothesis=f"Detailed {service} properties may reveal a hidden startup or restart failure.",
            expected_signal="Runtime properties should expose the exact state, command, restart policy, and result.",
            risk_level="read_only",
        ),
        _step(
            phase="diagnostic",
            command=f"systemctl list-dependencies {service} --all --no-pager",
            purpose=f"Inspect dependencies that can prevent {service} from starting or staying available.",
            hypothesis=f"A missing or failed dependency may explain the unresolved {service} behavior.",
            expected_signal="Dependencies should identify ordering or availability constraints.",
            risk_level="read_only",
        ),
        _step(
            phase="diagnostic",
            command="ss -H -ltnp",
            purpose="Map listening TCP sockets to owning processes.",
            hypothesis="The expected port may be absent or owned by a different process.",
            expected_signal="Socket ownership should identify the process serving or blocking the customer endpoint.",
            risk_level="read_only",
        ),
        _step(
            phase="diagnostic",
            command=f"curl -fsS {endpoint}",
            purpose="Inspect the customer endpoint response body and fail on HTTP errors.",
            hypothesis="The response body may expose an application-level failure not visible in a HEAD request.",
            expected_signal="The request should return the health payload or a concrete HTTP/application error.",
            risk_level="read_only",
        ),
        _step(
            phase="diagnostic",
            command=f"journalctl -u {service} --since -15min --no-pager -n 200",
            purpose=f"Inspect a wider recent log window for {service}.",
            hypothesis=f"The shorter {service} journal sample may have omitted the relevant failure.",
            expected_signal="The expanded journal window should reveal startup, request, or restart errors.",
            risk_level="read_only",
        ),
    ]
    for candidate in candidates:
        if _canonical_command(candidate.command) not in attempted:
            return candidate

    minutes = max(30, (len(attempted) + 1) * 15)
    while True:
        candidate = _step(
            phase="diagnostic",
            command=f"journalctl -u {service} --since -{minutes}min --no-pager -n 200",
            purpose=f"Expand the {service} journal search window for unresolved evidence.",
            hypothesis=f"Older {service} events may contain the root-cause signal missing from recent logs.",
            expected_signal="The expanded time window should reveal an earlier startup or availability failure.",
            risk_level="read_only",
        )
        if _canonical_command(candidate.command) not in attempted:
            return candidate
        minutes += 15


def _validation_step_for_expectations(
    run: Run,
    expectations: list[ValidationExpectation],
) -> PlannerStep | None:
    pending = [expectation for expectation in expectations if expectation.required and expectation.status == "pending"]
    if not pending:
        return None
    expectation = pending[0]
    target = expectation.target or _infer_service(run) or "nginx"
    if expectation.check_type == "service_health":
        return _step(
            phase="validation",
            command=f"systemctl is-active {target}",
            purpose=f"Validate required service health for {target}.",
            hypothesis=expectation.relation_to_customer_symptom,
            expected_signal=expectation.expected_result,
            risk_level="read_only",
        )
    if expectation.check_type == "customer_benefit":
        endpoint = target if target.startswith(("http://", "https://")) else _endpoint_validation_command(run)
        return _step(
            phase="validation",
            command=f"curl -sS -i {endpoint}",
            purpose="Validate the required customer-facing benefit check.",
            hypothesis=expectation.relation_to_customer_symptom,
            expected_signal=expectation.expected_result,
            risk_level="read_only",
        )
    if expectation.check_type == "logs_clean":
        return _step(
            phase="validation",
            command=f"journalctl -u {target} --since -5min --no-pager -n 80",
            purpose=f"Validate recent {target} logs no longer show the original error.",
            hypothesis=expectation.relation_to_customer_symptom,
            expected_signal=expectation.expected_result,
            risk_level="read_only",
        )
    if expectation.check_type == "persistence":
        return _step(
            phase="validation",
            command=f"sudo -n systemctl restart {target}",
            purpose=f"Validate persistence after restarting the affected {target} service.",
            hypothesis=expectation.relation_to_customer_symptom,
            expected_signal=expectation.expected_result,
            risk_level="low",
            requires_service_restart=True,
            persistence_consideration="Technician approval is required because this validation restarts the affected service.",
            rollback_plan="No file rollback required; the service can be inspected and restarted again if needed.",
        )
    if expectation.check_type == "public_validation":
        return _step(
            phase="validation",
            command=target,
            purpose="Run the ticket-required public validation command.",
            hypothesis=expectation.relation_to_customer_symptom,
            expected_signal=expectation.expected_result,
            risk_level="requires_review",
        )
    return _step(
        phase="validation",
        command=_endpoint_validation_command(run),
        purpose="Run the next required validation check.",
        hypothesis=expectation.relation_to_customer_symptom,
        expected_signal=expectation.expected_result,
        risk_level="read_only",
    )


def _step(
    *,
    phase: Literal["diagnostic", "fix", "validation"],
    command: str,
    purpose: str,
    hypothesis: str,
    expected_signal: str,
    risk_level: Literal["read_only", "low", "medium", "requires_review"],
    requires_service_restart: bool = False,
    persistence_consideration: str = "Read-only diagnostic; no persistent change.",
    rollback_plan: str = "No rollback required for read-only diagnostics.",
    stop_if: str = "Stop and ask the technician if output contradicts the ticket or exposes secrets.",
    evidence_references: list[str] | None = None,
    needs_more_diagnosis: bool = False,
    diagnosis_gap: str = "",
) -> PlannerStep:
    return PlannerStep(
        phase=phase,
        command=command,
        purpose=purpose,
        hypothesis=hypothesis,
        expected_signal=expected_signal,
        risk_level=risk_level,
        requires_service_restart=requires_service_restart,
        persistence_consideration=persistence_consideration,
        rollback_plan=rollback_plan,
        stop_if=stop_if,
        evidence_references=evidence_references or [],
        needs_more_diagnosis=needs_more_diagnosis,
        diagnosis_gap=diagnosis_gap,
    )


def _infer_service(run: Run) -> str | None:
    text = " ".join(
        str(value).lower()
        for value in [
            run.ticket_snapshot.get("title"),
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
        ]
    )
    for service in ["nginx", "apache2", "apache", "redis", "postgresql", "mysql", "mariadb", "docker"]:
        if service in text:
            return "apache2" if service == "apache" else service
    return None


def _service_candidates(text: str) -> list[str]:
    normalized = text.lower()
    services: list[str] = []
    for service in ["nginx", "apache2", "apache", "redis", "postgresql", "mysql", "mariadb", "docker"]:
        if service in normalized:
            unit = "apache2" if service == "apache" else service
            if unit not in services:
                services.append(unit)
    return services


def _symptom_categories(text: str) -> list[str]:
    normalized = text.lower()
    categories: list[str] = []
    checks = [
        ("disk", ("no space left", "disk full", "filesystem full", "out of space", "storage full")),
        ("memory", ("out of memory", "oom", "memory pressure", "swap", "low memory")),
        ("port", ("port", "cannot connect", "connection refused", "unreachable", "not listening")),
        ("permission", ("permission denied", "forbidden", "ownership", "owner", "chmod", "chown")),
    ]
    for category, markers in checks:
        if any(marker in normalized for marker in markers):
            categories.append(category)
    return categories


def _is_listening_tcp_diagnostic(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts or parts[0] != "ss":
        return False
    flags = "".join(part[1:] for part in parts[1:] if part.startswith("-"))
    return all(flag in flags for flag in ("l", "t", "n"))


def _canonical_command(command: str) -> str:
    try:
        return shlex.join(shlex.split(command))
    except ValueError:
        return " ".join(command.split())


def _first_service_candidate(analysis: JsonObject) -> str | None:
    candidates = analysis.get("service_candidates")
    if not isinstance(candidates, list):
        return None
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _service_from_command(command: str) -> str | None:
    match = re.search(
        r"\b(?:sudo\s+-n\s+)?systemctl\s+(?:status|restart|reload|is-active|start|stop)\s+([A-Za-z0-9_.@:-]+)",
        command,
    )
    if not match:
        match = re.search(
            r"\b(?:sudo\s+-n\s+)?systemctl\s+(?:enable|disable)(?:\s+--now)?\s+([A-Za-z0-9_.@:-]+)",
            command,
        )
    if not match:
        match = re.search(r"\bjournalctl\b.*(?:-u|--unit(?:=|\s+))\s*([A-Za-z0-9_.@:-]+)", command)
    if not match:
        return None
    return match.group(1).removesuffix(".service")


def _service_from_command_history(commands: list[CommandExecution]) -> str | None:
    return next(
        (
            detected
            for command in reversed(commands)
            if (detected := _service_from_command(command.approved_command))
        ),
        None,
    )


def _looks_restartable(output: str) -> bool:
    return any(marker in output for marker in ("failed", "inactive", "dead", "connection refused", "bind()"))


def _looks_healthy(output: str) -> bool:
    return bool(re.search(r"\bactive\b", output)) or "200 ok" in output or "syntax is ok" in output


def _config_check_command(service: str) -> str:
    if service == "nginx":
        return "nginx -t"
    if service.startswith("apache"):
        return "apachectl configtest"
    return f"systemctl cat {service}"


def _ticket_endpoint(run: Run) -> str | None:
    text = " ".join(
        str(value)
        for value in [
            run.ticket_snapshot.get("title"),
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
        ]
    )
    match = re.search(r"https?://[^\s<>()\[\]{}\"']+", text)
    return match.group(0).rstrip(".,;:!?") if match else None


def _ticket_endpoint_port(run: Run) -> int | None:
    endpoint = _ticket_endpoint(run)
    if not endpoint:
        return None
    match = re.match(r"https?://[^/:]+:(\d+)", endpoint)
    return int(match.group(1)) if match else (443 if endpoint.startswith("https://") else 80)


def _environment_file_from_commands(commands: list[CommandExecution]) -> str | None:
    for command in reversed(commands):
        port_check_match = re.fullmatch(
            r"grep -E '\^PORT=\[0-9\]\+\$' (\S+)",
            command.approved_command,
        )
        if port_check_match:
            return port_check_match.group(1)
        if not command.approved_command.startswith("systemctl cat "):
            continue
        match = re.search(r"(?m)^EnvironmentFile=-?(\S+)\s*$", command.sanitized_stdout)
        if match:
            return match.group(1)
    return None


def _port_environment_check_command(environment_file: str) -> str:
    return f"grep -E '^PORT=[0-9]+$' {shlex.quote(environment_file)}"


def _port_setting_from_command(command: CommandExecution) -> int | None:
    if not command.approved_command.startswith("grep -E '^PORT=[0-9]+$' "):
        return None
    match = re.search(r"(?m)^PORT=(\d+)\s*$", command.sanitized_stdout)
    return int(match.group(1)) if match else None


def _endpoint_validation_command(run: Run) -> str:
    endpoint = _ticket_endpoint(run)
    if endpoint:
        return f"curl -sS -i {endpoint}"
    notes = str(run.customer_system_snapshot.get("system", {}).get("notes", "")).lower()
    if "https" in notes:
        return "curl -sS -i https://localhost"
    return "curl -sS -i http://localhost"
