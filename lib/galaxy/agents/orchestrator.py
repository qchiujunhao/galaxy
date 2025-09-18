"""
Workflow orchestration agent for coordinating multiple agents on complex tasks.

WHAT THIS AGENT DOES:
- Coordinates multiple specialist agents for complex, multi-faceted queries
- Decomposes complex tasks into sub-tasks for appropriate agents
- Manages sequential and parallel agent execution patterns
- Aggregates and synthesizes results from multiple agents
- Provides comprehensive responses by combining multiple agents' expertise

CURRENT CAPABILITIES:
- Task decomposition and agent selection
- Sequential agent coordination (A → B → C)
- Parallel agent execution (A + B + C simultaneously)
- Result aggregation and synthesis
- Context sharing between agents
- Error handling and fallback strategies

COORDINATION PATTERNS:
- Debug → Fix → Test workflows for error resolution
- Analyze → Recommend → Train workflows for learning paths
- Multiple concurrent agents for comprehensive coverage
- Adaptive routing based on intermediate results

PLANNED IMPROVEMENTS:
- Dynamic workflow adjustment based on intermediate results
- Learning from successful orchestration patterns
- User preference integration for workflow preferences
- Performance optimization for agent coordination
- Advanced result synthesis with conflict resolution
- Workflow caching for common task patterns
"""

import asyncio
import logging
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)

from pydantic import BaseModel
from pydantic_ai import Agent

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    ConfidenceLevel,
    GalaxyAgentDependencies,
)

log = logging.getLogger(__name__)


class TaskDecomposition(BaseModel):
    """Result from task decomposition analysis."""

    primary_task: str
    sub_tasks: List[str]
    required_agents: List[str]
    execution_strategy: str  # "sequential", "parallel", "hybrid"
    dependencies: Dict[str, List[str]] = {}  # agent -> list of prerequisite agents
    confidence: str  # "low", "medium", or "high"
    reasoning: str


class WorkflowOrchestratorAgent(BaseGalaxyAgent):
    """
    Agent that orchestrates multiple specialist agents for complex tasks.

    This agent analyzes complex queries, decomposes them into sub-tasks,
    and coordinates multiple agents to provide comprehensive solutions.
    """

    def _create_agent(self) -> Agent:
        """Create the orchestrator agent with conditional structured output."""
        if self._supports_structured_output():
            agent = Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                output_type=TaskDecomposition,
                system_prompt=self.get_system_prompt(),
            )
        else:
            # DeepSeek and other models without structured output
            agent = Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                system_prompt=self._get_simple_system_prompt(),
            )

        return agent

    def get_system_prompt(self) -> str:
        """Get the system prompt for orchestration analysis."""
        return """
        You are a Galaxy workflow coordinator. Your job is to identify when a query needs multiple agents working together.
        
        AVAILABLE AGENTS:
        - error_analysis: For debugging job failures
        - tool_recommendation: For finding appropriate tools  
        - gtn_training: For tutorials and learning materials
        
        WHEN TO ORCHESTRATE:
        Only orchestrate when the user explicitly requests multiple types of help in one query, such as:
        - "Fix my error AND find alternative tools AND teach me the workflow"
        - "Debug this issue AND show me tutorials"
        - "Help with this problem AND recommend better approaches"
        
        EXECUTION STRATEGIES:
        - sequential: When one task depends on another (error → tools → training)
        - parallel: When tasks can be done simultaneously (rare)
        
        DEFAULT: Most queries should go to a single appropriate agent.
        
        OUTPUT FORMAT:
        - primary_task: Main user goal
        - required_agents: List of 1-3 agents needed
        - execution_strategy: "sequential" or "parallel"
        - confidence: "low", "medium", or "high"
        - reasoning: Why this approach was chosen
        
        Be conservative - only orchestrate for explicitly multi-part requests.
        """

    async def process(self, query: str, context: Dict[str, Any] = None) -> AgentResponse:
        """
        Process an orchestration request and coordinate multiple agents.

        Args:
            query: Complex user query requiring multiple agents
            context: Additional context for orchestration

        Returns:
            Comprehensive response from multiple coordinated agents
        """
        try:
            # First, analyze if orchestration is needed and how to decompose the task
            decomposition = await self._analyze_task_decomposition(query, context)

            # Much more conservative about when to actually orchestrate
            should_orchestrate = (
                decomposition.confidence in ["medium", "high"]
                and len(decomposition.required_agents) >= 2
                and decomposition.execution_strategy in ["sequential", "hybrid"]  # Avoid parallel unless truly needed
            )

            if not should_orchestrate:
                # Not complex enough for orchestration, fall back to single agent
                primary_agent = (
                    decomposition.required_agents[0] if decomposition.required_agents else "tool_recommendation"
                )
                log.info(f"Orchestrator determined single agent sufficient: {primary_agent}")

                from galaxy.agents import agent_registry

                delegate_agent = agent_registry.get_agent(primary_agent, self.deps)
                single_agent_response = await delegate_agent.process(query, context or {})
                return AgentResponse(
                    content=single_agent_response.content,
                    confidence=single_agent_response.confidence,
                    agent_type=self.agent_type,
                    suggestions=single_agent_response.suggestions,
                    metadata={
                        "orchestration_attempted": True,
                        "orchestration_needed": False,
                        "delegated_to": primary_agent,
                        "reasoning": f"Single agent sufficient: {decomposition.reasoning}",
                    },
                )

            # Execute the orchestration strategy
            log.info(
                f"Orchestrating {decomposition.execution_strategy} workflow with agents: {decomposition.required_agents}"
            )

            if decomposition.execution_strategy == "parallel":
                agent_responses = await self._execute_parallel_workflow(decomposition, query, context)
            elif decomposition.execution_strategy == "sequential":
                agent_responses = await self._execute_sequential_workflow(decomposition, query, context)
            elif decomposition.execution_strategy == "hybrid":
                agent_responses = await self._execute_hybrid_workflow(decomposition, query, context)
            else:
                raise ValueError(f"Unknown execution strategy: {decomposition.execution_strategy}")

            # Synthesize results from all agents
            synthesized_response = await self._synthesize_agent_responses(
                agent_responses, decomposition, query, context
            )

            return synthesized_response

        except Exception as e:
            log.error(f"Orchestration failed: {e}")
            return self._get_fallback_response(query, str(e))

    async def _analyze_task_decomposition(self, query: str, context: Dict[str, Any]) -> TaskDecomposition:
        """Analyze the query to determine orchestration strategy."""
        try:
            # Run the decomposition analysis
            result = await self._run_with_retry(query)

            if self._supports_structured_output():
                if hasattr(result, "data"):
                    return result.data
                elif hasattr(result, "output"):
                    return result.output
                else:
                    return result
            else:
                # Parse simple text response
                response_text = str(result.data) if hasattr(result, "data") else str(result)
                return self._parse_simple_decomposition(response_text, query)

        except Exception as e:
            log.warning(f"Task decomposition analysis failed, using fallback: {e}")
            return self._fallback_decomposition(query, context)

    async def _execute_parallel_workflow(
        self, decomposition: TaskDecomposition, query: str, context: Dict[str, Any]
    ) -> Dict[str, AgentResponse]:
        """Execute agents in parallel and collect responses."""
        log.info(f"Executing parallel workflow with {len(decomposition.required_agents)} agents")

        # Create tasks for each agent
        tasks = []
        agent_types = []

        for i, agent_type in enumerate(decomposition.required_agents):
            # Use specific sub-task if available, otherwise use main query
            agent_query = decomposition.sub_tasks[i] if i < len(decomposition.sub_tasks) else query
            from galaxy.agents import agent_registry

            delegate_agent = agent_registry.get_agent(agent_type, self.deps)
            task = delegate_agent.process(agent_query, context or {})
            tasks.append(task)
            agent_types.append(agent_type)

        # Execute all tasks in parallel
        try:
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            # Process responses and handle any exceptions
            agent_responses = {}
            for agent_type, response in zip(agent_types, responses):
                if isinstance(response, Exception):
                    log.error(f"Parallel agent {agent_type} failed: {response}")
                    # Create error response
                    agent_responses[agent_type] = AgentResponse(
                        content=f"Agent {agent_type} encountered an error: {str(response)}",
                        confidence="low",
                        agent_type=agent_type,
                        suggestions=[],
                        metadata={"execution_error": True},
                    )
                else:
                    agent_responses[agent_type] = response

            return agent_responses

        except Exception as e:
            log.error(f"Parallel execution failed: {e}")
            raise

    async def _execute_sequential_workflow(
        self, decomposition: TaskDecomposition, query: str, context: Dict[str, Any]
    ) -> Dict[str, AgentResponse]:
        """Execute agents sequentially, passing context between them."""
        log.info(f"Executing sequential workflow with {len(decomposition.required_agents)} agents")

        agent_responses = {}
        current_context = dict(context) if context else {}

        # Determine execution order based on dependencies
        execution_order = self._determine_execution_order(decomposition)

        for agent_type in execution_order:
            try:
                # Find the appropriate sub-task for this agent
                agent_query = query  # Default to main query
                for i, req_agent in enumerate(decomposition.required_agents):
                    if req_agent == agent_type and i < len(decomposition.sub_tasks):
                        agent_query = decomposition.sub_tasks[i]
                        break

                log.info(f"Sequential step: {agent_type} processing: {agent_query[:100]}...")
                from galaxy.agents import agent_registry

                delegate_agent = agent_registry.get_agent(agent_type, self.deps)
                response = await delegate_agent.process(agent_query, current_context)
                agent_responses[agent_type] = response

                # Enrich context for next agents
                current_context[f"previous_{agent_type}_response"] = response.content
                current_context[f"previous_{agent_type}_confidence"] = response.confidence

                # Add any specific findings to context
                if agent_type == "error_analysis" and response.metadata.get("error_category"):
                    current_context["error_category"] = response.metadata["error_category"]
                elif agent_type == "tool_recommendation" and response.metadata.get("num_tools_found", 0) > 0:
                    current_context["tools_recommended"] = True

            except Exception as e:
                log.error(f"Sequential agent {agent_type} failed: {e}")
                # Create error response but continue with workflow
                agent_responses[agent_type] = AgentResponse(
                    content=f"Agent {agent_type} encountered an error: {str(e)}",
                    confidence="low",
                    agent_type=agent_type,
                    suggestions=[],
                    metadata={"execution_error": True},
                )

        return agent_responses

    async def _execute_hybrid_workflow(
        self, decomposition: TaskDecomposition, query: str, context: Dict[str, Any]
    ) -> Dict[str, AgentResponse]:
        """Execute a hybrid workflow mixing parallel and sequential execution."""
        log.info("Executing hybrid workflow")

        # For now, implement a simple hybrid: parallel analysis followed by sequential synthesis
        # This can be made more sophisticated based on specific dependency patterns

        # Phase 1: Parallel analysis agents
        analysis_agents = [a for a in decomposition.required_agents if a in ["error_analysis", "dataset_analyzer"]]
        if analysis_agents:
            log.info(f"Hybrid Phase 1: Parallel analysis with {analysis_agents}")
            analysis_decomposition = TaskDecomposition(
                primary_task=decomposition.primary_task,
                sub_tasks=decomposition.sub_tasks,
                required_agents=analysis_agents,
                execution_strategy="parallel",
                confidence=decomposition.confidence,
                reasoning="Hybrid phase 1: parallel analysis",
            )
            phase1_responses = await self._execute_parallel_workflow(analysis_decomposition, query, context)
        else:
            phase1_responses = {}

        # Phase 2: Sequential solution agents using analysis results
        solution_agents = [a for a in decomposition.required_agents if a not in analysis_agents]
        if solution_agents:
            log.info(f"Hybrid Phase 2: Sequential solutions with {solution_agents}")
            # Enrich context with analysis results
            enriched_context = dict(context) if context else {}
            for agent_type, response in phase1_responses.items():
                enriched_context[f"analysis_{agent_type}_result"] = response.content

            solution_decomposition = TaskDecomposition(
                primary_task=decomposition.primary_task,
                sub_tasks=decomposition.sub_tasks,
                required_agents=solution_agents,
                execution_strategy="sequential",
                dependencies=decomposition.dependencies,
                confidence=decomposition.confidence,
                reasoning="Hybrid phase 2: sequential solutions",
            )
            phase2_responses = await self._execute_sequential_workflow(solution_decomposition, query, enriched_context)
        else:
            phase2_responses = {}

        # Combine all responses
        all_responses = {**phase1_responses, **phase2_responses}
        return all_responses

    async def _synthesize_agent_responses(
        self,
        agent_responses: Dict[str, AgentResponse],
        decomposition: TaskDecomposition,
        query: str,
        context: Dict[str, Any],
    ) -> AgentResponse:
        """Synthesize responses from multiple agents into a coherent result."""

        # Collect all successful responses
        successful_responses = {
            k: v for k, v in agent_responses.items() if not v.metadata.get("execution_error", False)
        }
        failed_agents = [k for k, v in agent_responses.items() if v.metadata.get("execution_error", False)]

        if not successful_responses:
            return AgentResponse(
                content="All coordinated agents encountered errors. Please try a simpler query or contact support.",
                confidence="low",
                agent_type=self.agent_type,
                suggestions=[
                    ActionSuggestion(
                        action_type=ActionType.CONTACT_SUPPORT,
                        description="Contact support for assistance",
                        confidence="high",
                        priority=1,
                    )
                ],
                metadata={"orchestration_failed": True, "failed_agents": failed_agents},
            )

        # Build synthesized content
        content_parts = [
            f"I've coordinated {len(successful_responses)} specialist agents to address your complex query:\n"
        ]

        # Add each agent's contribution with clear sections
        agent_order = ["error_analysis", "dataset_analyzer", "tool_recommendation", "gtn_training", "custom_tool"]

        # Process in logical order, then add any remaining
        processed_agents = set()

        for agent_type in agent_order:
            if agent_type in successful_responses:
                response = successful_responses[agent_type]
                agent_name = agent_type.replace("_", " ").title()
                content_parts.append(f"\n## {agent_name} Results\n{response.content}")
                processed_agents.add(agent_type)

        # Add any remaining agents not in the standard order
        for agent_type, response in successful_responses.items():
            if agent_type not in processed_agents:
                agent_name = agent_type.replace("_", " ").title()
                content_parts.append(f"\n## {agent_name} Results\n{response.content}")

        # Add summary and next steps
        if len(successful_responses) > 1:
            content_parts.append(
                f"\n## Summary\nThis orchestrated response combines insights from {len(successful_responses)} specialist agents to provide you with comprehensive guidance."
            )

        # Collect suggestions from all agents
        all_suggestions = []
        for response in successful_responses.values():
            all_suggestions.extend(response.suggestions)

        # Deduplicate and prioritize suggestions
        unique_suggestions = []
        seen_descriptions = set()
        for suggestion in sorted(all_suggestions, key=lambda x: x.priority):
            if suggestion.description not in seen_descriptions:
                unique_suggestions.append(suggestion)
                seen_descriptions.add(suggestion.description)

        # Determine overall confidence (lowest of successful agents)
        confidence_levels = [r.confidence for r in successful_responses.values()]
        overall_confidence = "high"
        if "low" in confidence_levels:
            overall_confidence = "low"
        elif "medium" in confidence_levels:
            overall_confidence = "medium"

        return AgentResponse(
            content="\n".join(content_parts),
            confidence=overall_confidence,
            agent_type=self.agent_type,
            suggestions=unique_suggestions[:5],  # Limit to top 5 suggestions
            metadata={
                "orchestration_successful": True,
                "agents_coordinated": list(successful_responses.keys()),
                "execution_strategy": decomposition.execution_strategy,
                "failed_agents": failed_agents,
                "task_decomposition": decomposition.dict() if hasattr(decomposition, "dict") else {},
            },
            reasoning=f"Orchestrated {len(successful_responses)} agents using {decomposition.execution_strategy} strategy: {decomposition.reasoning}",
        )

    def _determine_execution_order(self, decomposition: TaskDecomposition) -> List[str]:
        """Determine the order of agent execution based on dependencies."""
        if not decomposition.dependencies:
            return decomposition.required_agents

        # Simple topological sort for dependencies
        remaining = set(decomposition.required_agents)
        ordered = []

        while remaining:
            # Find agents with no unmet dependencies
            ready = []
            for agent in remaining:
                deps = decomposition.dependencies.get(agent, [])
                if not deps or all(dep in ordered for dep in deps):
                    ready.append(agent)

            if not ready:
                # Circular dependency or missing agent, just use remaining order
                log.warning(f"Dependency resolution issue, using default order for: {remaining}")
                ordered.extend(remaining)
                break

            # Add ready agents and remove from remaining
            ordered.extend(ready)
            remaining -= set(ready)

        return ordered

    def _fallback_decomposition(self, query: str, context: Dict[str, Any]) -> TaskDecomposition:
        """Provide a very conservative fallback decomposition when analysis fails."""
        query_lower = query.lower()

        # Only use orchestration for VERY explicit multi-part requests
        has_error = any(word in query_lower for word in ["error", "fail", "crash", "broken"])
        explicit_learning = any(
            word in query_lower for word in ["and teach", "and learn", "and tutorial", "and show me how"]
        )
        explicit_alternatives = any(word in query_lower for word in ["and find", "and suggest", "and recommend"])

        # Only orchestrate if there are EXPLICIT conjunctions indicating multiple needs
        if has_error and explicit_learning and explicit_alternatives:
            return TaskDecomposition(
                primary_task="Debug error and provide comprehensive guidance",
                sub_tasks=["Analyze the error", "Find alternative tools", "Locate relevant tutorials"],
                required_agents=["error_analysis", "tool_recommendation", "gtn_training"],
                execution_strategy="sequential",
                dependencies={"tool_recommendation": ["error_analysis"], "gtn_training": ["tool_recommendation"]},
                confidence="high",  # High confidence since explicitly requested
                reasoning="Explicit multi-part request: error + alternatives + learning",
            )
        elif has_error and (explicit_learning or explicit_alternatives):
            # Two explicit parts
            agents = ["error_analysis"]
            if explicit_alternatives:
                agents.append("tool_recommendation")
            if explicit_learning:
                agents.append("gtn_training")

            return TaskDecomposition(
                primary_task="Debug error with additional support",
                sub_tasks=["Analyze error"] + ["Provide additional guidance"],
                required_agents=agents,
                execution_strategy="sequential",
                confidence="medium",
                reasoning="Explicit two-part request detected",
            )

        # Default: ALWAYS route to single most appropriate agent
        if has_error:
            primary_agent = "error_analysis"
        elif any(word in query_lower for word in ["tutorial", "learn", "guide", "training"]):
            primary_agent = "gtn_training"
        elif any(word in query_lower for word in ["create", "build", "custom", "new tool"]):
            primary_agent = "custom_tool"
        else:
            primary_agent = "tool_recommendation"

        return TaskDecomposition(
            primary_task=query,
            sub_tasks=[query],
            required_agents=[primary_agent],
            execution_strategy="parallel",  # Single agent
            confidence="low",  # Low confidence discourages orchestration
            reasoning=f"Fallback: single agent ({primary_agent}) routing",
        )

    def _parse_simple_decomposition(self, response_text: str, query: str) -> TaskDecomposition:
        """Parse simple text response into structured decomposition."""
        import re

        # Extract structured information from text
        agents_match = re.search(r"AGENTS:\s*([^\n]+)", response_text, re.IGNORECASE)
        strategy_match = re.search(r"STRATEGY:\s*([^\n]+)", response_text, re.IGNORECASE)
        reason_match = re.search(r"REASONING:\s*([^\n]+)", response_text, re.IGNORECASE)

        if agents_match:
            agents_text = agents_match.group(1)
            required_agents = [a.strip() for a in agents_text.split(",")]
        else:
            required_agents = ["tool_recommendation"]  # fallback

        strategy = strategy_match.group(1).lower() if strategy_match else "parallel"
        if strategy not in ["parallel", "sequential", "hybrid"]:
            strategy = "parallel"

        reasoning = reason_match.group(1) if reason_match else "Simple text parsing"

        return TaskDecomposition(
            primary_task=query,
            sub_tasks=[query] * len(required_agents),
            required_agents=required_agents,
            execution_strategy=strategy,
            confidence="medium",
            reasoning=reasoning,
        )

    def _get_simple_system_prompt(self) -> str:
        """Simple system prompt for models without structured output."""
        return """
        You are a Galaxy workflow orchestrator. Analyze if the query needs multiple agents and how to coordinate them.
        
        Respond in this exact format:
        AGENTS: [comma-separated list of agents needed: error_analysis, tool_recommendation, gtn_training, etc.]
        STRATEGY: [parallel, sequential, or hybrid]
        REASONING: [brief explanation of why these agents and strategy]
        
        Only use multiple agents if truly needed. Simple queries should use just one agent.
        
        Example:
        AGENTS: error_analysis, tool_recommendation
        STRATEGY: sequential
        REASONING: Need to diagnose error first, then find alternative tools
        """

    def _get_fallback_content(self) -> str:
        """Get fallback content for orchestration failures."""
        return "Unable to coordinate multiple agents for this complex task. Try breaking your query into smaller parts."
