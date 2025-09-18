"""
Base classes for Galaxy AI agents.
"""

import asyncio
import logging
import os
import time
from abc import (
    ABC,
    abstractmethod,
)
from dataclasses import dataclass
from enum import Enum
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Union,
)

from pydantic import BaseModel

from galaxy.managers.context import ProvidesUserContext
from galaxy.model import User

# Try to import pydantic-ai components
try:
    from pydantic_ai import Agent
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    HAS_PYDANTIC_AI = True
except ImportError:
    HAS_PYDANTIC_AI = False
    Agent = None
    UnexpectedModelBehavior = Exception
    OpenAIChatModel = None
    OpenAIProvider = None

log = logging.getLogger(__name__)


class ConfidenceLevel(str, Enum):
    """Confidence levels for agent responses."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ActionType(str, Enum):
    """Types of actions agents can suggest."""

    TOOL_RUN = "tool_run"
    PARAMETER_CHANGE = "parameter_change"
    WORKFLOW_STEP = "workflow_step"
    DOCUMENTATION = "documentation"
    CONTACT_SUPPORT = "contact_support"
    VIEW_EXTERNAL = "view_external"  # Open external URL in new tab
    SAVE_TOOL = "save_tool"
    TEST_TOOL = "test_tool"
    REFINE_QUERY = "refine_query"


class ActionSuggestion(BaseModel):
    """Structured suggestion for user action."""

    action_type: ActionType
    description: str
    parameters: Dict[str, Any] = {}
    confidence: str  # "low", "medium", or "high"
    priority: int = 1  # 1=high, 2=medium, 3=low


class AgentResponse(BaseModel):
    """Structured response from an AI agent."""

    content: str
    confidence: str  # "low", "medium", or "high"
    agent_type: str
    suggestions: List[ActionSuggestion] = []
    metadata: Dict[str, Any] = {}
    reasoning: Optional[str] = None


@dataclass
class GalaxyAgentDependencies:
    """Dependencies passed to Galaxy agents via dependency injection."""

    trans: ProvidesUserContext
    user: User
    config: Any  # GalaxyAppConfiguration
    # Additional managers will be added as needed
    job_manager: Optional[Any] = None
    dataset_manager: Optional[Any] = None
    workflow_manager: Optional[Any] = None
    tool_cache: Optional[Any] = None


class BaseGalaxyAgent(ABC):
    """Base class for all Galaxy AI agents."""

    def __init__(self, deps: GalaxyAgentDependencies):
        """Initialize the agent with dependencies."""
        self.deps = deps
        self.agent_type = self.__class__.__name__.lower().replace("agent", "")

        if not HAS_PYDANTIC_AI:
            raise ImportError(
                "pydantic-ai is required for agent functionality. " "Please install with: pip install pydantic-ai"
            )

        self.agent = self._create_agent()

    @abstractmethod
    def _create_agent(self) -> Agent:
        """Create the pydantic-ai Agent instance."""
        pass

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent."""
        pass

    async def process(self, query: str, context: Dict[str, Any] = None) -> AgentResponse:
        """
        Process a query and return structured response.

        Args:
            query: The user's query/request
            context: Optional additional context for the query

        Returns:
            AgentResponse with structured output
        """
        try:
            # Prepare the full prompt with context
            full_prompt = self._prepare_prompt(query, context or {})

            # Run the agent with retry logic
            result = await self._run_with_retry(full_prompt)

            # Format the response
            return self._format_response(result, query, context)

        except UnexpectedModelBehavior as e:
            log.error(f"Unexpected model behavior in {self.agent_type} agent: {e}")
            return self._get_fallback_response(query, f"Unexpected model behavior: {str(e)}")

        except Exception as e:
            log.error(f"Error in {self.agent_type} agent: {e}")
            return self._get_fallback_response(query, str(e))

    async def _run_with_retry(self, prompt: str, max_retries: int = 3, base_delay: float = 1.0):
        """
        Run the agent with exponential backoff retry logic.

        Args:
            prompt: The prompt to send to the agent
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds for exponential backoff

        Returns:
            Agent result

        Raises:
            Exception: If all retries are exhausted
        """
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                # Run the agent
                result = await self.agent.run(prompt, deps=self.deps)

                # If successful, return the result
                return result

            except Exception as e:
                last_exception = e
                error_msg = str(e).lower()

                # Check if this is a retryable error
                is_retryable = any(
                    indicator in error_msg
                    for indicator in [
                        "timeout",
                        "connection",
                        "rate limit",
                        "502",
                        "503",
                        "504",
                        "server error",
                        "temporary",
                        "overloaded",
                        "network",
                        "ssl",
                    ]
                )

                if not is_retryable or attempt == max_retries:
                    # Don't retry for non-retryable errors or if we've exhausted retries
                    raise e

                # Calculate exponential backoff delay
                delay = base_delay * (2**attempt)

                log.warning(
                    f"Retryable error in {self.agent_type} agent (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )

                # Wait before retrying
                await asyncio.sleep(delay)

        # This should never be reached, but just in case
        raise last_exception or Exception("Max retries exhausted")

    def _prepare_prompt(self, query: str, context: Dict[str, Any]) -> str:
        """Prepare the full prompt including context."""
        prompt_parts = [query]

        # Add context if available
        if context:
            context_str = "\n".join([f"{k}: {v}" for k, v in context.items() if v])
            if context_str:
                prompt_parts.insert(0, f"Context:\n{context_str}\n")

        return "\n".join(prompt_parts)

    def _format_response(self, result: Any, query: str, context: Dict[str, Any]) -> AgentResponse:
        """Convert pydantic-ai result to AgentResponse."""
        # Default implementation - subclasses can override
        content = str(result.data) if hasattr(result, "data") else str(result)

        return AgentResponse(
            content=content,
            confidence="medium",
            agent_type=self.agent_type,
            suggestions=[],
            metadata={
                "model": getattr(self.agent, "model_name", "unknown"),
                "query_length": len(query),
                "has_context": bool(context),
            },
        )

    def _get_fallback_response(self, query: str, error_msg: str) -> AgentResponse:
        """Return a fallback response when agent processing fails.

        This should indicate service unavailability rather than pretending
        to have analyzed the query.
        """
        # Check if this is likely a service/connectivity issue
        is_service_error = any(
            indicator in error_msg.lower()
            for indicator in ["connection", "timeout", "api", "401", "403", "500", "502", "503", "rate limit"]
        )

        if is_service_error:
            content = "Unable to access the AI inference service. Please try again later."
        else:
            content = f"I'm having trouble processing your request right now. {self._get_fallback_content()}"

        return AgentResponse(
            content=content,
            confidence="low",
            agent_type=self.agent_type,
            suggestions=[
                ActionSuggestion(
                    action_type=ActionType.CONTACT_SUPPORT,
                    description="Contact Galaxy support for assistance",
                    confidence="high",
                    priority=1,
                )
            ],
            metadata={"fallback": True, "error": error_msg, "service_unavailable": is_service_error},
        )

    def _get_fallback_content(self) -> str:
        """Get fallback content specific to this agent type."""
        return "Please try again later or contact support if the issue persists."

    def _supports_structured_output(self) -> bool:
        """Check if current model supports structured output (tool calling/JSON mode)."""
        model_name = (self.deps.config.ai_model or "").lower()

        # DeepSeek models don't support structured output at all
        if "deepseek" in model_name:
            return False

        # These models have good structured output support
        if any(m in model_name for m in ["gpt", "claude", "scout"]):
            return True

        # Default to attempting structured output for unknown models
        return True

    def _get_model_name(self) -> str:
        """Get the model name for this agent from configuration."""
        # Check for global AI model configuration first
        if hasattr(self.deps.config, "ai_model") and self.deps.config.ai_model:
            # Use the global AI model configuration
            return f"openai:{self.deps.config.ai_model}"

        # Fall back to agent-specific configuration
        agent_config = getattr(self.deps.config, "agents", {}).get(self.agent_type, {})
        return agent_config.get("model", "openai:gpt-3.5-turbo")

    def _get_model(self):
        """Get the configured model with proper base URL."""
        model_name = self._get_model_name()

        # Check if we need to use a custom base URL
        if hasattr(self.deps.config, "ai_api_base_url") and self.deps.config.ai_api_base_url:
            if HAS_PYDANTIC_AI and OpenAIChatModel:
                # Remove the "openai:" prefix if present
                if model_name.startswith("openai:"):
                    model_name = model_name[7:]

                # Create a custom OpenAIProvider with the configured base URL
                custom_provider = OpenAIProvider(
                    api_key=self.deps.config.ai_api_key or "sk-local-test-master-key",
                    base_url=self.deps.config.ai_api_base_url,
                )
                # Return the OpenAIChatModel with custom provider
                return OpenAIChatModel(model_name, provider=custom_provider)

        # Default case - use standard OpenAI configuration
        if self.deps.config.ai_api_key:
            os.environ["OPENAI_API_KEY"] = self.deps.config.ai_api_key

        return model_name

    def _get_temperature(self) -> float:
        """Get the temperature setting for this agent."""
        agent_config = getattr(self.deps.config, "agents", {}).get(self.agent_type, {})
        return agent_config.get("temperature", 0.7)

    def _get_max_tokens(self) -> int:
        """Get the max tokens setting for this agent."""
        agent_config = getattr(self.deps.config, "agents", {}).get(self.agent_type, {})
        return agent_config.get("max_tokens", 1500)

    async def _call_agent_from_tool(self, agent_type: str, query: str, ctx, usage=None) -> str:
        """
        Centralized helper method for calling other agents from within tool functions.

        This method standardizes agent-to-agent communication within @agent.tool decorated functions,
        reducing code duplication and providing consistent error handling.

        Args:
            agent_type: Type of agent to call (e.g., "tool_recommendation", "gtn_training")
            query: Query to send to the target agent
            ctx: RunContext from the calling tool function
            usage: Optional usage tracking object (defaults to ctx.usage)

        Returns:
            String response from the target agent

        Raises:
            Exception: If the target agent cannot be called

        Example usage in @agent.tool functions:
            response = await self._call_agent_from_tool(
                "tool_recommendation",
                f"Find alternatives for: {task}",
                ctx
            )
        """
        try:
            # Import here to avoid circular imports
            from galaxy.agents import agent_registry

            # Get the target agent
            target_agent = agent_registry.get_agent(agent_type, ctx.deps)

            # Call the agent with proper usage tracking
            result = await target_agent.agent.run(
                query, deps=ctx.deps, usage=usage or ctx.usage  # Use provided usage or fall back to ctx.usage
            )

            # Extract response data
            response_data = result.data if hasattr(result, "data") else str(result)

            log.debug(f"Agent {self.agent_type} called {agent_type} via tool: '{query[:50]}...'")

            return str(response_data)

        except Exception as e:
            error_msg = f"Unable to call {agent_type} agent: {str(e)}"
            log.warning(f"Agent-to-agent call failed: {error_msg}")
            return error_msg


class SimpleGalaxyAgent(BaseGalaxyAgent):
    """
    Simple agent that uses basic text completion without structured output.
    Useful for agents that don't need complex response schemas.
    """

    def _create_agent(self) -> Agent:
        """Create a simple agent with text output."""
        return Agent(self._get_model(), deps_type=GalaxyAgentDependencies, system_prompt=self.get_system_prompt())

    def _format_response(self, result: Any, query: str, context: Dict[str, Any]) -> AgentResponse:
        """Format simple text response."""
        content = str(result.data) if hasattr(result, "data") else str(result)

        # Try to extract confidence from the response
        confidence = self._extract_confidence(content)

        return AgentResponse(
            content=content,
            confidence=confidence,
            agent_type=self.agent_type,
            suggestions=self._extract_suggestions(content),
            metadata={
                "model": self._get_model_name(),
                "query_length": len(query),
                "has_context": bool(context),
                "response_length": len(content),
            },
        )

    def _extract_confidence(self, content: str) -> ConfidenceLevel:
        """Extract confidence level from response content."""
        content_lower = content.lower()

        if any(word in content_lower for word in ["uncertain", "might", "possibly", "unclear"]):
            return "low"
        elif any(word in content_lower for word in ["likely", "probably", "confident"]):
            return "high"
        else:
            return "medium"

    def _extract_suggestions(self, content: str) -> List[ActionSuggestion]:
        """Extract action suggestions from response content."""
        suggestions = []

        # Simple heuristics to extract suggestions
        if "try" in content.lower() or "recommend" in content.lower():
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.TOOL_RUN,
                    description="Follow the suggested approach",
                    confidence="medium",
                )
            )

        if "documentation" in content.lower() or "manual" in content.lower():
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.DOCUMENTATION,
                    description="Check the relevant documentation",
                    confidence="medium",
                )
            )

        return suggestions
