"""Persistent Hermes goal operations scoped to one agent profile."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Mapping

from .hermes_support import AgentAPIError, Path


@contextmanager
def _goal_profile_scope(profile_dir: Path):
    """Redirect Hermes state access without bootstrapping the full gateway."""
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    token = set_hermes_home_override(str(profile_dir))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


def _goal_payload(state: Any) -> dict[str, Any] | None:
    if state is None or str(getattr(state, "status", "")) == "cleared":
        return None
    contract = getattr(state, "contract", None)
    waiting = bool(
        getattr(state, "waiting_on_pid", None)
        or getattr(state, "waiting_on_session", None)
        or float(getattr(state, "waiting_until", 0) or 0) > 0
    )
    return {
        "objective": str(getattr(state, "goal", "") or ""),
        "status": str(getattr(state, "status", "") or ""),
        "turns_used": int(getattr(state, "turns_used", 0) or 0),
        "max_turns": int(getattr(state, "max_turns", 20) or 20),
        "created_at": float(getattr(state, "created_at", 0) or 0),
        "last_turn_at": float(getattr(state, "last_turn_at", 0) or 0),
        "last_verdict": getattr(state, "last_verdict", None),
        "last_reason": getattr(state, "last_reason", None),
        "paused_reason": getattr(state, "paused_reason", None),
        "waiting": waiting,
        "waiting_reason": getattr(state, "waiting_reason", None),
        "subgoals": list(getattr(state, "subgoals", None) or []),
        "contract": contract.to_dict() if contract is not None else {},
    }


class ConversationGoalsMixin:
    def _goal_max_turns(self, profile_dir: Path) -> int:
        configured = self._get_nested(
            self._read_config(profile_dir),
            ("goals", "max_turns"),
            20,
        )
        try:
            value = int(configured or 20)
        except (TypeError, ValueError):
            value = 20
        return value if value in {10, 15, 20, 25, 30} else 20

    def _goal_manager(self, raw_name: Any, conversation_id: Any):
        from hermes_cli.goals import GoalManager

        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(conversation_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        return profile_dir, session_id, _goal_profile_scope, GoalManager

    def get_conversation_goal(self, raw_name: Any, conversation_id: Any) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            return {"goal": _goal_payload(manager_type(session_id).state)}

    def set_conversation_goal(
        self,
        raw_name: Any,
        conversation_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        from hermes_cli.goals import GoalContract

        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        objective = self._text_value(body.get("objective"), field="objective", max_chars=10_000)
        max_turns = int(body.get("max_turns") or self._goal_max_turns(Path(profile_dir)))
        if max_turns < 1 or max_turns > 100:
            raise AgentAPIError("max_turns must be between 1 and 100", code="invalid_goal_budget")
        contract = GoalContract.from_dict(dict(body.get("contract") or {}))
        with scope(Path(profile_dir)):
            state = manager_type(session_id).set(objective, max_turns=max_turns, contract=contract)
            return {"goal": _goal_payload(state)}

    def update_conversation_goal(
        self,
        raw_name: Any,
        conversation_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        from hermes_cli.goals import GoalContract, save_goal

        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            manager = manager_type(session_id)
            current = manager.state
            if current is None:
                raise AgentAPIError("no goal exists for this conversation", code="goal_not_found", status=404)
            objective = self._text_value(body.get("objective"), field="objective", max_chars=10_000)
            max_turns = int(body.get("max_turns") or current.max_turns)
            if max_turns < 1 or max_turns > 100:
                raise AgentAPIError("max_turns must be between 1 and 100", code="invalid_goal_budget")
            contract_data = body.get("contract")
            contract = GoalContract.from_dict(dict(contract_data)) if isinstance(contract_data, Mapping) else current.contract
            subgoals = list(current.subgoals)
            state = manager.set(objective, max_turns=max_turns, contract=contract)
            state.subgoals = subgoals
            save_goal(session_id, state)
            manager.pause("edited — resume when ready")
            return {"goal": _goal_payload(manager.state)}

    def pause_conversation_goal(self, raw_name: Any, conversation_id: Any) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            state = manager_type(session_id).pause()
            if state is None:
                raise AgentAPIError("no goal exists for this conversation", code="goal_not_found", status=404)
            return {"goal": _goal_payload(state)}

    def resume_conversation_goal(self, raw_name: Any, conversation_id: Any) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            state = manager_type(session_id).resume()
            if state is None:
                raise AgentAPIError("no goal exists for this conversation", code="goal_not_found", status=404)
            return {"goal": _goal_payload(state)}

    def clear_conversation_goal(self, raw_name: Any, conversation_id: Any) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            manager_type(session_id).clear()
        return {"goal": None}

    def add_conversation_subgoal(
        self,
        raw_name: Any,
        conversation_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        text = self._text_value(body.get("text"), field="text", max_chars=2_000)
        with scope(Path(profile_dir)):
            manager = manager_type(session_id)
            try:
                manager.add_subgoal(text)
            except RuntimeError as error:
                raise AgentAPIError(str(error), code="goal_not_found", status=404) from error
            return {"goal": _goal_payload(manager.state)}

    def remove_conversation_subgoal(
        self,
        raw_name: Any,
        conversation_id: Any,
        index: Any,
    ) -> dict[str, Any]:
        profile_dir, session_id, scope, manager_type = self._goal_manager(raw_name, conversation_id)
        with scope(Path(profile_dir)):
            manager = manager_type(session_id)
            try:
                manager.remove_subgoal(int(index))
            except RuntimeError as error:
                raise AgentAPIError(str(error), code="goal_not_found", status=404) from error
            except (TypeError, ValueError, IndexError) as error:
                raise AgentAPIError("subgoal not found", code="subgoal_not_found", status=404) from error
            return {"goal": _goal_payload(manager.state)}
