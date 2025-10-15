"""DSPy-powered data analysis agent that emits browser-executable actions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    GalaxyAgentDependencies,
)

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    GalaxyAgentDependencies,
)
from .data_analysis import DataAnalysisAgent
from .dspy_adapter import DSPyPlanResult, GalaxyDSPyPlanner, build_context_text

log = logging.getLogger(__name__)


class DataAnalysisDSPyAgent(BaseGalaxyAgent):
    """Lightweight DSPy agent that plans analysis steps but defers execution."""

    USE_PYDANTIC_AGENT = False

    DEFAULT_TIMEOUT_MS = 20_000

    def __init__(self, deps: GalaxyAgentDependencies):
        super().__init__(deps)
        self._planner = GalaxyDSPyPlanner(deps)
        self._examples_path = Path(__file__).parent / "examples.json"
        self._last_plan: Optional[DSPyPlanResult] = None
        self._last_context_text: str = ""

    # Base class requires definition, but the DSPy variant bypasses pydantic agent.
    def _create_agent(self):  # type: ignore[override]
        raise NotImplementedError("DataAnalysisDSPyAgent does not use the pydantic runtime")

    def get_system_prompt(self) -> str:  # type: ignore[override]
        return (
            "You are Galaxy's DSPy data analysis planner. Produce concise, tabular-focused reasoning, "
            "generate Python when code execution is required, and keep summary text short."
        )

    async def process(self, query: str, context: Optional[Dict[str, Any]] = None):
        """Fallback process implementation to keep compatibility with existing service layer."""
        step = self.plan_step(query, context or {})
        if "final_answer" in step:
            content = step["final_answer"] or "Generated analysis summary."
        else:
            content = (
                "Generated Python code for execution. Use the action payload to run it in the browser."
            )
        suggestions: List[ActionSuggestion] = []
        if "action" in step:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.PYODIDE_EXECUTE,
                    description="Run the generated Python snippet in the browser sandbox.",
                    parameters={"code": step.get("code") or step.get("action_payload", {}).get("code", "")},
                    confidence="medium",
                    priority=1,
                )
            )
        return AgentResponse(
            content=content,
            confidence="medium",
            agent_type=self.agent_type,
            suggestions=suggestions,
            metadata={"planner_step": step},
        )

    def plan_step(self, question: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Run a single DSPy planning step and emit either a final answer or an execution request."""
        datasets = context.get("dataset_ids", [])
        conversation_history = context.get("conversation_history", [])
        execution_messages = [
            entry for entry in conversation_history if entry.get("role") == "execution_result"
        ]

        context_text = build_context_text(
            question,
            datasets,
            conversation_history,
            execution_messages,
            self._load_example_snippets(),
        )
        self._last_context_text = context_text

        plan = self._planner.plan(question, context_text)
        self._last_plan = plan
        return self._plan_to_step(plan, datasets)

    def refine_with_execution(
        self,
        question: str,
        context: Dict[str, Any],
        plan: DSPyPlanResult,
        execution_result: Dict[str, Any],
    ) -> DSPyPlanResult:
        datasets = context.get("dataset_ids", [])
        conversation_history = context.get("conversation_history", [])
        execution_messages = [
            entry for entry in conversation_history if entry.get("role") == "execution_result"
        ]
        context_text = build_context_text(
            question,
            datasets,
            conversation_history,
            execution_messages,
            self._load_example_snippets(),
        )
        self._last_context_text = context_text
        refined = self._planner.augment_with_execution(question, context_text, plan, execution_result)
        self._last_plan = refined
        return refined

    def step_from_plan(self, plan: DSPyPlanResult, datasets: List[str]) -> Dict[str, Any]:
        self._last_plan = plan
        return self._plan_to_step(plan, datasets)

    def last_plan(self) -> Optional[DSPyPlanResult]:
        return self._last_plan

    def _load_example_snippets(self) -> str:
        # Example snippets are currently disabled; keep hook for future use.
        return ""

    def _dataset_entries(self, dataset_ids: List[str]) -> List[Dict[str, Any]]:
        if not dataset_ids:
            return []
        try:
            analysis_agent = DataAnalysisAgent(self.deps)
            alias_map, metadata = analysis_agent._prepare_dataset_aliases(dataset_ids)
        except Exception as exc:
            log.debug("Unable to gather dataset metadata: %s", exc)
            return [{"id": dataset_id} for dataset_id in dataset_ids]

        entries: List[Dict[str, Any]] = []
        for entry in metadata:
            entries.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "path": entry.get("path"),
                    "size": entry.get("size"),
                    "aliases": entry.get("aliases", []),
                }
            )
        # Ensure all ids are present even if metadata missing
        known = {e.get("id") for e in entries}
        for dataset_id in dataset_ids:
            if dataset_id not in known:
                entries.append({"id": dataset_id})
        return entries

    def _plan_to_step(self, plan: DSPyPlanResult, datasets: List[str]) -> Dict[str, Any]:
        summary = plan.summary or (plan.raw_answer.get("explanation") if plan.raw_answer else "")
        summary = (summary or "").strip()

        if (not plan.python_code) or plan.is_complete:
            if not summary:
                summary = "No further analysis steps required."
            return {
                "final_answer": summary,
                "plots": plan.plots,
                "files": plan.files,
                "follow_up": plan.follow_up,
            }

        code = (plan.python_code or "").strip()
        if not code:
            return {
                "final_answer": summary or "Generated plan without executable code.",
                "follow_up": plan.follow_up,
            }

        dataset_entries = self._dataset_entries(datasets)
        action_payload: Dict[str, Any] = {
            "action": "ExecutePythonInBrowser",
            "code": code,
            "packages": plan.requirements or [],
            "vars": {},
            "files": dataset_entries,
            "timeout_ms": self.DEFAULT_TIMEOUT_MS,
        }

        return {
            "summary": summary,
            "action": action_payload["action"],
            "code": code,
            "packages": action_payload["packages"],
            "datasets": dataset_entries,
            "timeout_ms": action_payload["timeout_ms"],
            "action_payload": action_payload,
            "follow_up": plan.follow_up,
        }
