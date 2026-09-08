"""Concurrent composer contract validation; no provider calls."""

import itertools
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from xnobrain.models.conversations import ChatRequest


class ConcurrentWorkToolsContractTests(unittest.TestCase):
    def test_all_eight_subsets_normalize_without_losing_selection(self):
        names = ("todo", "delegate", "goal")
        for size in range(4):
            for subset in itertools.combinations(names, size):
                with self.subTest(subset=subset):
                    request = ChatRequest(input="Synthetic task", capabilities=list(reversed(subset)))
                    self.assertEqual(request.capabilities, list(subset))
                    self.assertEqual(request.input, "Synthetic task")
                    self.assertIsNone(request.feature)

    def test_legacy_features_still_decode(self):
        for feature in ("todo", "delegate", "goal", "learn", "agent_maker", "optimize_skills"):
            request = ChatRequest(input="Synthetic task", feature=feature)
            self.assertEqual(request.feature, feature)
            self.assertIsNone(request.capabilities)

    def test_ambiguous_duplicate_and_unknown_selections_rejected(self):
        for body in (
            {"capabilities": ["todo", "todo"]},
            {"capabilities": ["unknown"]},
            {"capabilities": ["learn"]},
            {"feature": "todo", "capabilities": []},
            {"feature": "todo", "capabilities": ["todo", "goal"]},
            {"feature": "goal", "capabilities": ["delegate"]},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                ChatRequest(input="Synthetic task", **body)

    def test_matching_legacy_and_collection_are_unambiguous(self):
        request = ChatRequest(input="Synthetic task", feature="todo", capabilities=["todo"])
        self.assertEqual(request.capabilities, ["todo"])


class ConcurrentWorkToolsPreparationTests(unittest.TestCase):
    def test_preparation_keeps_all_capabilities_in_one_command(self):
        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations import AgentManager

        self.assertIsNotNone(XNOBrainApplication)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            manager = AgentManager(
                root_profile=root / "root",
                profiles_root=root / "profiles",
                legacy_agents_root=root / "legacy",
            )
            manager.create_agent({"name": "writer"})
            prepared = manager._prepare_chat_command(
                "writer",
                {"message": "Synthetic task", "capabilities": ["goal", "delegate", "todo"]},
                require_conversation=False,
            )
            self.assertEqual(prepared["capabilities"], ["todo", "delegate", "goal"])
            self.assertEqual(prepared["command"].count("Synthetic task"), 1)
            self.assertEqual(prepared["message"], "Synthetic task")


class ComposerGoalPreservationTests(unittest.TestCase):
    def test_existing_goal_states_are_not_replaced_or_resumed(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.conversation_goals import ensure_composer_goal

        self.assertIsNotNone(XNOBrainApplication)
        for status in ("active", "paused", "done", "completed", "blocked", "failed"):
            state = SimpleNamespace(status=status, goal="Original", turns_used=3)
            manager = SimpleNamespace(state=state, set=Mock())
            result = ensure_composer_goal(manager, "New message", 20)
            self.assertIs(result, state)
            manager.set.assert_not_called()

    def test_absent_or_cleared_goal_is_created_once(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.conversation_goals import ensure_composer_goal

        self.assertIsNotNone(XNOBrainApplication)
        for state in (None, SimpleNamespace(status="cleared")):
            manager = SimpleNamespace(state=state, set=Mock(return_value="new-state"))
            self.assertEqual(ensure_composer_goal(manager, "Synthetic objective", 12), "new-state")
            manager.set.assert_called_once_with("Synthetic objective", max_turns=12)


class ComposerGoalPersistenceTests(unittest.TestCase):
    def test_reopening_preserves_real_goal_and_profile_isolation(self):
        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.conversation_goals import (
            _goal_profile_scope,
            ensure_composer_goal,
        )
        from hermes_cli.goals import GoalManager, save_goal

        self.assertIsNotNone(XNOBrainApplication)
        with TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            with _goal_profile_scope(first):
                manager = GoalManager("synthetic-session")
                ensure_composer_goal(manager, "Original objective", 12)
                manager.add_subgoal("Synthetic completion criterion")
                manager.state.turns_used = 4
                save_goal("synthetic-session", manager.state)
                manager.pause()
                reopened = GoalManager("synthetic-session")
                before = asdict(reopened.state)
                ensure_composer_goal(reopened, "Do not replace", 99)
                self.assertEqual(asdict(GoalManager("synthetic-session").state), before)
            with _goal_profile_scope(second):
                other = GoalManager("synthetic-session")
                self.assertIsNone(other.state)
                ensure_composer_goal(other, "Other profile objective", 3)
                self.assertEqual(GoalManager("synthetic-session").state.goal, "Other profile objective")
            with _goal_profile_scope(first):
                self.assertEqual(asdict(GoalManager("synthetic-session").state), before)


class ComposerGoalBudgetTests(unittest.TestCase):
    def test_real_goal_judge_stops_at_shared_turn_limit(self):
        from unittest.mock import patch

        from xnobrain.app import XNOBrainApplication
        from xnobrain.integrations.conversation_goals import (
            _goal_profile_scope,
            ensure_composer_goal,
        )
        from hermes_cli.goals import GoalManager

        self.assertIsNotNone(XNOBrainApplication)
        with TemporaryDirectory() as directory, _goal_profile_scope(Path(directory)):
            manager = GoalManager("synthetic-budget-session")
            ensure_composer_goal(manager, "Synthetic objective", 1)
            # Deterministic judge requests continuation; the persisted turn cap
            # must still stop it. No provider request occurs in this fixture.
            with patch(
                "hermes_cli.goals.judge_goal",
                return_value=("continue", "More work", False, None, False),
            ):
                decision = manager.evaluate_after_turn("Synthetic result", user_initiated=True)
            self.assertFalse(decision["should_continue"])
            reopened = GoalManager("synthetic-budget-session")
            self.assertEqual(reopened.state.status, "paused")
            self.assertEqual(reopened.state.turns_used, 1)
            ensure_composer_goal(reopened, "Another selected message", 20)
            self.assertEqual(reopened.state.max_turns, 1)
            self.assertEqual(reopened.state.turns_used, 1)
            self.assertEqual(reopened.state.status, "paused")
