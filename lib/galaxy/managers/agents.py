"""Agent service layer for AI agent management."""

import logging
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from galaxy.config import GalaxyAppConfiguration
from galaxy.exceptions import ConfigurationError
from galaxy.managers.context import ProvidesUserContext
from galaxy.managers.jobs import JobManager
from galaxy.model import User

# Import agent system
try:
    from galaxy.agents import (
        agent_registry,
        GalaxyAgentDependencies,
        DataAnalysisAgent,
        DataAnalysisDSPyAgent,
    )
    from galaxy.agents.dspy_adapter import DSPyPlanResult
    from galaxy.agents.utils.summarize_exec import summarize_result
    from galaxy.agents.error_analysis import ErrorAnalysisAgent
    from galaxy.agents.router import QueryRouterAgent

    HAS_AGENTS = True
except ImportError:
    HAS_AGENTS = False
    agent_registry = None
    GalaxyAgentDependencies = None
    QueryRouterAgent = None
    ErrorAnalysisAgent = None

log = logging.getLogger(__name__)


class AgentService:
    """Service layer for AI agent execution and routing."""

    def __init__(
        self,
        config: GalaxyAppConfiguration,
        job_manager: JobManager,
    ):
        if not HAS_AGENTS:
            raise ConfigurationError("Agent system is not available")

        self.config = config
        self.job_manager = job_manager

    def create_dependencies(self, trans: ProvidesUserContext, user: User) -> GalaxyAgentDependencies:
        """Create agent dependencies for dependency injection."""
        toolbox = trans.app.toolbox if hasattr(trans, "app") and hasattr(trans.app, "toolbox") else None
        return GalaxyAgentDependencies(
            trans=trans,
            user=user,
            config=self.config,
            job_manager=self.job_manager,
            toolbox=toolbox,
        )

    async def execute_agent(
        self,
        agent_type: str,
        query: str,
        trans: ProvidesUserContext,
        user: User,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a specific agent and return response dict."""
        deps = self.create_dependencies(trans, user)

        if context is None:
            context = {}

        try:
            log.info(f"Executing {agent_type} agent for query: '{query[:100]}...'")
            agent = agent_registry.get_agent(agent_type, deps)

            if isinstance(agent, DataAnalysisDSPyAgent):
                return await self._execute_data_analysis_dspy(agent, query, context or {})

            response = await agent.process(query, context)

            return {
                "content": response.content,
                "agent_type": response.agent_type,
                "confidence": response.confidence,
                "suggestions": [s.model_dump() for s in response.suggestions],
                "metadata": response.metadata,
                "reasoning": response.reasoning,
            }
        except ValueError as e:
            log.warning(f"Unknown agent type {agent_type}, falling back to error_analysis: {e}")
            # Fallback to error analysis for unknown agents
            agent = ErrorAnalysisAgent(deps)
            response = await agent.process(query, context)
            return {
                "content": response.content,
                "agent_type": response.agent_type,
                "confidence": response.confidence,
                "suggestions": [s.model_dump() for s in response.suggestions],
                "metadata": response.metadata,
                "reasoning": response.reasoning,
                "fallback": True,
            }
        except Exception as e:
            log.error(f"Error executing agent {agent_type}: {e}", exc_info=True)
            raise

    async def _execute_data_analysis_dspy(
        self,
        agent: DataAnalysisDSPyAgent,
        question: str,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        datasets: List[str] = context.get("dataset_ids", [])
        conversation_history = [dict(entry) for entry in context.get("conversation_history", [])]
        data_agent = DataAnalysisAgent(agent.deps)
        execution_history: List[Dict[str, Any]] = []
        plan: Optional[DSPyPlanResult] = None
        need_new_plan = True
        step_budget = 6

        for _ in range(step_budget):
            if need_new_plan or plan is None:
                step_context = dict(context)
                step_context["conversation_history"] = conversation_history
                step_payload = agent.plan_step(question, step_context)
                plan = agent.last_plan()
                if plan is None:
                    return self._format_dspy_response(agent, None, execution_history, datasets)
            else:
                step_payload = agent.step_from_plan(plan, datasets)
                need_new_plan = True

            if "final_answer" in step_payload:
                return self._format_dspy_response(agent, plan, execution_history, datasets, step_payload["final_answer"])

            if "action" not in step_payload:
                return self._format_dspy_response(agent, plan, execution_history, datasets)

            action_payload = step_payload.get("action_payload") or {}
            code = action_payload.get("code") or step_payload.get("code")
            if not code:
                return self._format_dspy_response(agent, plan, execution_history, datasets)

            requirements = action_payload.get("packages") or step_payload.get("packages") or []
            dataset_refs = action_payload.get("files") or step_payload.get("datasets") or []
            dataset_ids = [ref.get("id") for ref in dataset_refs if isinstance(ref, dict) and ref.get("id")]
            if not dataset_ids:
                dataset_ids = datasets

            execution_result = data_agent._execute_generated_code_locally(code, dataset_ids, requirements)
            summary_payload = summarize_result(execution_result)

            execution_history.append({
                "raw": execution_result,
                "summary": summary_payload,
            })

            conversation_history.append(
                {
                    "role": "execution_result",
                    "content": summary_payload,
                }
            )

            plan = agent.refine_with_execution(
                question,
                {"dataset_ids": datasets, "conversation_history": conversation_history},
                plan,
                execution_result,
            )

            if plan.is_complete or not (plan.python_code or "").strip():
                return self._format_dspy_response(agent, plan, execution_history, datasets)

            need_new_plan = False

        return self._format_dspy_response(agent, plan, execution_history, datasets)

    def _format_dspy_response(
        self,
        agent: DataAnalysisDSPyAgent,
        plan: Optional[DSPyPlanResult],
        execution_history: List[Dict[str, Any]],
        datasets: List[str],
        override_summary: Optional[str] = None,
    ) -> Dict[str, Any]:
        summary = override_summary or (plan.summary if plan else "")
        if not summary and plan and plan.raw_answer:
            summary = (plan.raw_answer.get("explanation") or "").strip()
        summary = summary or "Analysis complete."

        follow_up = plan.follow_up if plan else []
        suggestions = [
            {
                "action_type": "refine_query",
                "description": item,
                "parameters": {},
                "confidence": "medium",
                "priority": index + 1,
            }
            for index, item in enumerate(follow_up or [])
        ]

        metadata: Dict[str, Any] = {
            "planner": "dspy",
            "datasets_used": agent._dataset_entries(datasets) if hasattr(agent, "_dataset_entries") else datasets,
            "summary": summary,
            "analysis_steps": plan.analysis_steps if plan else [],
            "plots": plan.plots if plan else [],
            "files": plan.files if plan else [],
            "follow_up": follow_up or [],
            "executions": execution_history,
        }
        if plan and plan.raw_answer:
            metadata["raw_answer"] = plan.raw_answer

        confidence = "medium"
        if execution_history:
            last_entry = execution_history[-1]
            last_success = False
            if isinstance(last_entry, dict):
                raw = last_entry.get("raw") if isinstance(last_entry.get("raw"), dict) else None
                if raw is not None:
                    last_success = bool(raw.get("success"))
            if last_success:
                confidence = "high"
            else:
                confidence = "low"

        return {
            "content": summary,
            "agent_type": agent.agent_type,
            "confidence": confidence,
            "suggestions": suggestions,
            "metadata": metadata,
        }

    async def route_and_execute(
        self,
        query: str,
        trans: ProvidesUserContext,
        user: User,
        context: Optional[Dict[str, Any]] = None,
        agent_type: str = "auto",
    ) -> Dict[str, Any]:
        """Route query to appropriate agent and execute. Uses router if agent_type is 'auto'."""
        deps = self.create_dependencies(trans, user)

        if context is None:
            context = {}

        # Route to appropriate agent
        actual_agent_type = agent_type
        routing_reasoning = None

        if agent_type == "auto":
            # Use router agent to determine best agent
            log.info(f"Router: Analyzing query for intent classification: '{query[:100]}...'")
            router = QueryRouterAgent(deps)
            routing_decision = await router.route_query(query, context)

            if routing_decision.direct_response:
                log.info("Router: Handling with direct response (no agent needed)")
                return {
                    "content": routing_decision.direct_response,
                    "agent_type": "router",
                    "confidence": routing_decision.confidence,
                    "suggestions": [],
                    "metadata": {"handled_directly": True},
                }

            # Use the primary agent recommended by router
            actual_agent_type = routing_decision.primary_agent
            routing_reasoning = routing_decision.reasoning
            log.info(f"Router: Selected agent '{actual_agent_type}' - Reason: {routing_reasoning}")
            if routing_decision.secondary_agents:
                log.info(f"Router: Secondary agents that could help: {routing_decision.secondary_agents}")
        else:
            log.info(f"User explicitly requested agent: {actual_agent_type}")

        # Execute the agent
        result = await self.execute_agent(actual_agent_type, query, trans, user, context)

        # Add routing information if we used the router
        if routing_reasoning:
            result["routing_info"] = {"selected_agent": actual_agent_type, "reasoning": routing_reasoning}

        return result
