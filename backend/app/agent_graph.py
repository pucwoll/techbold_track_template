from __future__ import annotations

import shlex
from typing import Any, TypedDict

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - exercised when the optional dependency is absent locally.
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment]

from .agent_orchestrator import (
    PlannerAdapter,
    PlannerOutputError,
    PlannerStep,
    build_planner_context,
    deterministic_next_step,
    continuation_diagnostic_step,
    parse_planner_output,
)
from .run_store import RunStore
from .safety_layer import SafetyVerdict, classify_command, redact_output
from .schemas import (
    ActivityDraft,
    BackupRecord,
    CommandExecution,
    InspectedSource,
    OutboxEvent,
    Run,
    RunEvent,
    RunStatus,
    ValidationExpectation,
    ValidationResult,
)
from .ssh_runner import CommandExecutionResult, CommandRunner, CommandTarget


PLANNER_NODE_NAMES = (
    "planner_context",
    "llm_planning",
    "deterministic_fallback",
    "validation_planning",
    "safety_classification",
    "approval_checkpoint",
)
COMMAND_NODE_NAMES = ("command_request", "command_execution", "validation")
ACTIVITY_NODE_NAMES = ("activity_context", "activity_drafting")
APPROVAL_CONTROLS = ["approve", "edit", "reject", "retry", "abort"]


class PlannerGraphState(TypedDict, total=False):
    run_id: int
    outbox_event_id: int
    halted: bool
    nodes_visited: list[str]
    run: Run
    events: list[RunEvent]
    commands: list[CommandExecution]
    inspected_sources: list[InspectedSource]
    backup_records: list[BackupRecord]
    validation_results: list[ValidationResult]
    validation_expectations: list[ValidationExpectation]
    planner_context: dict[str, Any]
    planner_step: PlannerStep
    planner_source: str
    planning_errors: list[str]
    validation_context: dict[str, Any]
    safety_verdict: dict[str, Any]
    proposed_step: dict[str, Any]
    approval_checkpoint: dict[str, Any]


class CommandGraphState(TypedDict, total=False):
    run_id: int
    outbox_event_id: int
    step_id: int
    halted: bool
    nodes_visited: list[str]
    execution: CommandExecution
    target: CommandTarget
    result: CommandExecutionResult
    completed_execution: CommandExecution


class ActivityGraphState(TypedDict, total=False):
    run_id: int
    nodes_visited: list[str]
    run: Run
    events: list[RunEvent]
    commands: list[CommandExecution]
    inspected_sources: list[InspectedSource]
    backup_records: list[BackupRecord]
    validation_results: list[ValidationResult]
    activity_draft: ActivityDraft


class PlannerWorkflow:
    """Planner orchestration as graph nodes around the existing durable store."""

    node_names = PLANNER_NODE_NAMES

    def __init__(
        self,
        *,
        store: RunStore,
        planner_adapter: PlannerAdapter | None,
        command_timeout_s: int,
    ) -> None:
        self.store = store
        self.planner_adapter = planner_adapter
        self.command_timeout_s = command_timeout_s
        self._compiled_graph = self._compile_langgraph()

    @property
    def uses_langgraph(self) -> bool:
        return self._compiled_graph is not None

    def invoke(self, outbox_event: OutboxEvent | None) -> PlannerGraphState:
        if outbox_event is None or outbox_event.run_id is None:
            return {"halted": True, "nodes_visited": []}
        state: PlannerGraphState = {
            "run_id": outbox_event.run_id,
            "outbox_event_id": outbox_event.id,
            "nodes_visited": [],
            "planning_errors": [],
        }
        if self._compiled_graph is not None:
            return self._compiled_graph.invoke(state)
        return self._invoke_sequential(state)

    def _compile_langgraph(self) -> Any | None:
        if StateGraph is None:
            return None
        builder = StateGraph(PlannerGraphState)
        builder.add_node("planner_context", self._planner_context_node)
        builder.add_node("llm_planning", self._llm_planning_node)
        builder.add_node("deterministic_fallback", self._deterministic_fallback_node)
        builder.add_node("validation_planning", self._validation_planning_node)
        builder.add_node("safety_classification", self._safety_classification_node)
        builder.add_node("approval_checkpoint", self._approval_checkpoint_node)
        builder.add_edge(START, "planner_context")
        builder.add_conditional_edges(
            "planner_context",
            self._route_after_context,
            {"llm_planning": "llm_planning", END: END},
        )
        builder.add_conditional_edges(
            "llm_planning",
            self._route_after_llm,
            {
                "deterministic_fallback": "deterministic_fallback",
                "validation_planning": "validation_planning",
            },
        )
        builder.add_conditional_edges(
            "deterministic_fallback",
            self._route_after_fallback,
            {"validation_planning": "validation_planning", END: END},
        )
        builder.add_edge("validation_planning", "safety_classification")
        builder.add_edge("safety_classification", "approval_checkpoint")
        builder.add_edge("approval_checkpoint", END)
        return builder.compile()

    def _invoke_sequential(self, state: PlannerGraphState) -> PlannerGraphState:
        state = _merge_state(state, self._planner_context_node(state))
        if state.get("halted"):
            return state
        state = _merge_state(state, self._llm_planning_node(state))
        if "planner_step" not in state:
            state = _merge_state(state, self._deterministic_fallback_node(state))
        if state.get("halted"):
            return state
        state = _merge_state(state, self._validation_planning_node(state))
        state = _merge_state(state, self._safety_classification_node(state))
        return _merge_state(state, self._approval_checkpoint_node(state))

    def _planner_context_node(self, state: PlannerGraphState) -> PlannerGraphState:
        run = self.store.get_run(state["run_id"])
        visited = _visited(state, "planner_context")
        if run.status in {RunStatus.ABORTED, RunStatus.READY_FOR_ACTIVITY, RunStatus.SUBMITTED, RunStatus.FAILED}:
            self.store.append_event(
                run.id,
                actor="worker",
                event_type="agent.plan_skipped",
                summary="Planning skipped because the run is no longer active.",
                payload={"outbox_event_id": state["outbox_event_id"], "status": run.status.value},
            )
            return {"halted": True, "nodes_visited": visited, "run": run}
        if run.pending_step:
            self.store.append_event(
                run.id,
                actor="worker",
                event_type="agent.plan_skipped",
                summary="Planning skipped because a step is already pending approval.",
                payload={"outbox_event_id": state["outbox_event_id"], "pending_step_id": run.pending_step.get("id")},
            )
            return {"halted": True, "nodes_visited": visited, "run": run}

        events = self.store.list_events(run.id)
        commands = self.store.list_command_executions(run.id)
        inspected_sources = self.store.list_inspected_sources(run.id)
        backup_records = self.store.list_backup_records(run.id)
        validation_results = self.store.list_validation_results(run.id)
        validation_expectations = self.store.list_validation_expectations(run.id)
        context = build_planner_context(
            run=run,
            events=events,
            commands=commands,
            inspected_sources=inspected_sources,
            backup_records=backup_records,
            validation_results=validation_results,
            validation_expectations=validation_expectations,
        )
        self.store.append_event(
            run.id,
            actor="agent",
            event_type="agent.context_built",
            summary="Planner context built from sanitized ticket, system, audit, command, and evidence data.",
            payload={
                "outbox_event_id": state["outbox_event_id"],
                "graph_node": "planner_context",
                "event_count": len(events),
                "command_count": len(commands),
                "inspected_source_count": len(inspected_sources),
                "backup_record_count": len(backup_records),
                "validation_result_count": len(validation_results),
                "validation_expectation_count": len(validation_expectations),
            },
        )
        return {
            "nodes_visited": visited,
            "run": run,
            "events": events,
            "commands": commands,
            "inspected_sources": inspected_sources,
            "backup_records": backup_records,
            "validation_results": validation_results,
            "validation_expectations": validation_expectations,
            "planner_context": context,
        }

    def _llm_planning_node(self, state: PlannerGraphState) -> PlannerGraphState:
        visited = _visited(state, "llm_planning")
        if self.planner_adapter is None:
            return {"nodes_visited": visited}
        run = state["run"]
        errors = list(state.get("planning_errors", []))
        for attempt in range(1, 3):
            try:
                self.store.append_event(
                    run.id,
                    actor="agent",
                    event_type="agent.prompt_submitted",
                    summary=f"Structured planner prompt submitted, attempt {attempt}.",
                    payload={"attempt": attempt, "graph_node": "llm_planning"},
                )
                raw_output = self.planner_adapter.propose(state["planner_context"] | {"attempt": attempt})
                self.store.append_event(
                    run.id,
                    actor="agent",
                    event_type="agent.output_received",
                    summary=f"Planner output received, attempt {attempt}.",
                    payload={"attempt": attempt, "graph_node": "llm_planning"},
                )
                return {
                    "nodes_visited": visited,
                    "planner_step": parse_planner_output(raw_output),
                    "planner_source": "llm",
                    "planning_errors": errors,
                }
            except (PlannerOutputError, Exception) as error:
                errors.append(str(error)[:600])
                self.store.append_event(
                    run.id,
                    actor="agent",
                    event_type="agent.output_invalid",
                    summary=f"Planner output invalid on attempt {attempt}.",
                    payload={"attempt": attempt, "error": str(error)[:600], "graph_node": "llm_planning"},
                )
        return {"nodes_visited": visited, "planning_errors": errors}

    def _deterministic_fallback_node(self, state: PlannerGraphState) -> PlannerGraphState:
        try:
            planner_step = deterministic_next_step(
                run=state["run"],
                commands=state["commands"],
                step_for_execution=lambda step_id: self.store.get_step(state["run_id"], step_id),
                validation_expectations=state["validation_expectations"],
            )
        except PlannerOutputError as error:
            planner_step = continuation_diagnostic_step(
                run=state["run"],
                commands=state["commands"],
            )
            self.store.append_event(
                state["run_id"],
                actor="agent",
                event_type="agent.planning_recovered",
                summary="Planner error recovered with an expanding deterministic diagnostic.",
                payload={
                    "error": str(error),
                    "graph_node": "deterministic_fallback",
                    "command": planner_step.command,
                },
            )
            return {
                "nodes_visited": _visited(state, "deterministic_fallback"),
                "planning_errors": [*state.get("planning_errors", []), str(error)],
                "planner_step": planner_step,
                "planner_source": "deterministic_recovery",
            }
        self.store.append_event(
            state["run_id"],
            actor="agent",
            event_type="agent.fallback_used",
            summary="Deterministic planner fallback selected one next step.",
            payload={"phase": planner_step.phase, "reason": "no_valid_structured_llm_output", "graph_node": "deterministic_fallback"},
        )
        return {
            "nodes_visited": _visited(state, "deterministic_fallback"),
            "planner_step": planner_step,
            "planner_source": "deterministic",
        }

    def _validation_planning_node(self, state: PlannerGraphState) -> PlannerGraphState:
        step = state["planner_step"]
        return {
            "nodes_visited": _visited(state, "validation_planning"),
            "validation_context": {
                "is_validation_step": step.phase == "validation",
                "expectation_count": len(state.get("validation_expectations", [])),
                "passed_validation_count": len([result for result in state.get("validation_results", []) if result.passed]),
            },
        }

    def _safety_classification_node(self, state: PlannerGraphState) -> PlannerGraphState:
        verdict = classify_command(state["planner_step"].command)
        return {
            "nodes_visited": _visited(state, "safety_classification"),
            "safety_verdict": _safety_verdict_dict(verdict),
        }

    def _approval_checkpoint_node(self, state: PlannerGraphState) -> PlannerGraphState:
        planner_step = state["planner_step"]
        selected_command = _canonical_command(planner_step.command)
        repeated_execution = next(
            (
                command
                for command in state.get("commands", [])
                if _canonical_command(command.approved_command) == selected_command
            ),
            None,
        )
        if repeated_execution is not None:
            replacement = deterministic_next_step(
                run=state["run"],
                commands=state["commands"],
                step_for_execution=lambda step_id: self.store.get_step(state["run_id"], step_id),
                validation_expectations=state["validation_expectations"],
            )
            self.store.append_event(
                state["run_id"],
                actor="agent",
                event_type="agent.repeated_command_replaced",
                summary="Repeated planner command replaced with an untried diagnostic step.",
                command=planner_step.command,
                payload={
                    "graph_node": "approval_checkpoint",
                    "command_execution_id": repeated_execution.id,
                    "planner_source": state.get("planner_source"),
                    "replacement_command": replacement.command,
                },
            )
            planner_step = replacement
            state["planner_step"] = replacement
            state["safety_verdict"] = _safety_verdict_dict(classify_command(replacement.command))
        self.store.append_event(
            state["run_id"],
            actor="agent",
            event_type="agent.step_selected",
            summary=planner_step.purpose,
            command=planner_step.command,
            payload={
                "graph_node": "approval_checkpoint",
                "phase": planner_step.phase,
                "hypothesis": planner_step.hypothesis,
                "risk_level": planner_step.risk_level,
                "requires_service_restart": planner_step.requires_service_restart,
                "persistence_consideration": planner_step.persistence_consideration,
                "rollback_plan": planner_step.rollback_plan,
                "stop_if": planner_step.stop_if,
            },
        )
        step = self.store.propose_agent_step(
            state["run_id"],
            command=planner_step.command,
            purpose=planner_step.purpose,
            expected_signal=planner_step.expected_signal,
            phase=planner_step.phase,
            timeout_s=self.command_timeout_s,
        )
        return {
            "nodes_visited": _visited(state, "approval_checkpoint"),
            "proposed_step": step.model_dump(mode="json"),
            "approval_checkpoint": {
                "type": "technician_step_approval",
                "step_id": step.id,
                "status": step.status.value,
                "controls": APPROVAL_CONTROLS,
                "llm_may_execute": False,
            },
        }

    def _route_after_context(self, state: PlannerGraphState) -> str:
        return END if state.get("halted") else "llm_planning"

    def _route_after_llm(self, state: PlannerGraphState) -> str:
        return "validation_planning" if "planner_step" in state else "deterministic_fallback"

    def _route_after_fallback(self, state: PlannerGraphState) -> str:
        return END if state.get("halted") else "validation_planning"


class CommandExecutionWorkflow:
    """Command execution graph; no LLM node is present on this path."""

    node_names = COMMAND_NODE_NAMES

    def __init__(
        self,
        *,
        store: RunStore,
        runner: CommandRunner,
        command_timeout_s: int,
    ) -> None:
        self.store = store
        self.runner = runner
        self.command_timeout_s = command_timeout_s

    def invoke(self, outbox_event: OutboxEvent | None) -> CommandGraphState:
        if outbox_event is None or outbox_event.run_id is None:
            return {"halted": True, "nodes_visited": []}
        state: CommandGraphState = {
            "run_id": outbox_event.run_id,
            "outbox_event_id": outbox_event.id,
            "step_id": int(outbox_event.payload["step_id"]),
            "nodes_visited": [],
        }
        state = _merge_state(state, self._command_request_node(state))
        if state.get("halted"):
            return state
        state = _merge_state(state, self._command_execution_node(state))
        return _merge_state(state, self._validation_node(state))

    def _command_request_node(self, state: CommandGraphState) -> CommandGraphState:
        run = self.store.get_run(state["run_id"])
        visited = _visited(state, "command_request")
        if run.status == RunStatus.ABORTED:
            self.store.append_event(
                run.id,
                actor="worker",
                event_type="command.skipped",
                summary="Queued command was skipped because the run was aborted before execution.",
                payload={"outbox_event_id": state["outbox_event_id"], "step_id": state["step_id"]},
            )
            return {"halted": True, "nodes_visited": visited}
        execution = self.store.start_command_execution(run.id, state["step_id"])
        target = CommandTarget(
            host=execution.target_host,
            port=execution.target_port,
            username=execution.target_username,
            os=None,
            key_number=run.ticket_id % 10,
        )
        return {"nodes_visited": visited, "execution": execution, "target": target}

    def _command_execution_node(self, state: CommandGraphState) -> CommandGraphState:
        execution = state["execution"]

        def on_chunk(stream: str, content: str) -> None:
            redacted_content, redacted = redact_output(content)
            self.store.append_command_output_chunk(
                state["run_id"],
                execution.id,
                stream=stream,
                content=redacted_content,
                redacted=redacted,
            )

        try:
            result = self.runner.execute(
                target=state["target"],
                command=execution.approved_command,
                timeout_s=min(execution.timeout_s, self.command_timeout_s),
                on_chunk=on_chunk,
            )
        except Exception as error:
            result = CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=0,
                error=str(error),
            )
        return {"nodes_visited": _visited(state, "command_execution"), "result": result}

    def _validation_node(self, state: CommandGraphState) -> CommandGraphState:
        result = state["result"]
        completed = self.store.complete_command_execution(
            state["run_id"],
            state["execution"].id,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            error=result.error,
            timed_out=result.timed_out,
        )
        return {"nodes_visited": _visited(state, "validation"), "completed_execution": completed}


class ActivityDraftWorkflow:
    node_names = ACTIVITY_NODE_NAMES

    def __init__(self, *, store: RunStore) -> None:
        self.store = store

    def invoke(self, run_id: int) -> ActivityGraphState:
        state: ActivityGraphState = {"run_id": run_id, "nodes_visited": []}
        state = _merge_state(state, self._activity_context_node(state))
        return _merge_state(state, self._activity_drafting_node(state))

    def _activity_context_node(self, state: ActivityGraphState) -> ActivityGraphState:
        run = self.store.get_run(state["run_id"])
        return {
            "nodes_visited": _visited(state, "activity_context"),
            "run": run,
            "events": self.store.list_events(run.id),
            "commands": self.store.list_command_executions(run.id),
            "inspected_sources": self.store.list_inspected_sources(run.id),
            "backup_records": self.store.list_backup_records(run.id),
            "validation_results": self.store.list_validation_results(run.id),
        }

    def _activity_drafting_node(self, state: ActivityGraphState) -> ActivityGraphState:
        draft = self.store.create_activity_draft(state["run_id"])
        return {"nodes_visited": _visited(state, "activity_drafting"), "activity_draft": draft}


def draft_activity_with_graph(*, store: RunStore, run_id: int) -> ActivityDraft:
    state = ActivityDraftWorkflow(store=store).invoke(run_id)
    return state["activity_draft"]


def _visited(state: dict[str, Any], node_name: str) -> list[str]:
    return [*state.get("nodes_visited", []), node_name]


def _canonical_command(command: str) -> str:
    try:
        return shlex.join(shlex.split(command))
    except ValueError:
        return " ".join(command.split())


def _merge_state[T: dict[str, Any]](state: T, update: dict[str, Any]) -> T:
    merged = dict(state)
    merged.update(update)
    return merged  # type: ignore[return-value]


def _safety_verdict_dict(verdict: SafetyVerdict) -> dict[str, Any]:
    return {
        "verdict": verdict.verdict,
        "risk_class": verdict.risk_class,
        "summary": verdict.summary,
        "notes": verdict.notes,
    }
