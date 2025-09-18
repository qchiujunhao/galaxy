"""
Galaxy AI Agents Module

This module provides AI agent functionality built on pydantic-ai for Galaxy.
Agents provide specialized assistance for workflows, tool errors, data quality, and more.
"""

from .base import (
    BaseGalaxyAgent,
    GalaxyAgentDependencies,
)
from .custom_tool import CustomToolAgent
from .dspy_agent import DSPyGalaxyAgent
from .error_analysis import ErrorAnalysisAgent
from .gtn_training import GTNTrainingAgent
from .orchestrator import WorkflowOrchestratorAgent
from .registry import AgentRegistry
from .router import QueryRouterAgent
from .tools import ToolRecommendationAgent

__all__ = [
    "BaseGalaxyAgent",
    "GalaxyAgentDependencies",
    "AgentRegistry",
    "QueryRouterAgent",
    "ErrorAnalysisAgent",
    "ToolRecommendationAgent",
    "CustomToolAgent",
    "GTNTrainingAgent",
    "WorkflowOrchestratorAgent",
    "DSPyGalaxyAgent",
]

# Global agent registry instance
agent_registry = AgentRegistry()

# Register default agents
agent_registry.register("router", QueryRouterAgent)
agent_registry.register("error_analysis", ErrorAnalysisAgent)
agent_registry.register("tool_recommendation", ToolRecommendationAgent)
agent_registry.register("custom_tool", CustomToolAgent)
agent_registry.register("gtn_training", GTNTrainingAgent)
agent_registry.register("orchestrator", WorkflowOrchestratorAgent)
agent_registry.register("dspy_tool_recommendation", DSPyGalaxyAgent)
