"""
Error analysis agent for enhanced tool error diagnosis.

WHAT THIS AGENT DOES:
- Analyzes Galaxy job failures and provides actionable debugging guidance
- Examines stderr, stdout, exit codes, and tool parameters
- Identifies common error patterns and suggests fixes
- Provides step-by-step troubleshooting recommendations
- Helps users understand why their Galaxy jobs failed

CURRENT CAPABILITIES:
- Parses error messages from various bioinformatics tools
- Identifies memory issues, file format problems, parameter errors
- Provides structured error analysis with confidence levels
- Suggests specific actions to resolve issues
- Handles both simple and complex error scenarios

KNOWN ISSUES:
- Limited to pattern matching, no deep semantic understanding
- Can't access actual job files or datasets for validation
- No integration with Galaxy's job metrics or resource usage
- Generic suggestions for unfamiliar error patterns
- No learning from resolved issues

PLANNED IMPROVEMENTS:
- Add ML-based error classification for better pattern recognition
- Integrate with Galaxy job metrics API for resource analysis
- Build knowledge base of tool-specific error patterns
- Add ability to suggest alternative tools for failed operations
- Implement automated fix suggestions with code/parameter changes
- Add historical analysis ("this error happens often with this tool")
- Connect to tool documentation for version-specific issues
- Add predictive warnings before job submission
"""

import logging
from typing import (
    Any,
    Dict,
    List,
    Optional,
)

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.tools import RunContext

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    ConfidenceLevel,
    GalaxyAgentDependencies,
)

log = logging.getLogger(__name__)


class ErrorAnalysisResult(BaseModel):
    """Structured result from error analysis - simplified for local LLMs."""

    error_category: str  # e.g., "tool_configuration", "input_data", "parameters"
    error_severity: str  # "low", "medium", "high", "critical"
    likely_cause: str
    solution_steps: List[str]
    alternative_approaches: List[str] = []
    confidence: str  # "low", "medium", or "high"
    related_documentation: List[str] = []
    requires_admin: bool = False


class ErrorAnalysisAgent(BaseGalaxyAgent):
    """
    Enhanced error analysis agent for diagnosing tool failures and providing solutions.

    This agent specializes in analyzing Galaxy tool errors, job failures, and providing
    step-by-step solutions with high accuracy and detailed guidance.
    """

    def _create_agent(self) -> Agent:
        """Create the error analysis agent with conditional structured output."""
        if self._supports_structured_output():
            agent = Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                output_type=ErrorAnalysisResult,
                system_prompt=self.get_system_prompt(),
            )
        else:
            # DeepSeek and other models without structured output
            agent = Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                system_prompt=self._get_simple_system_prompt(),
            )

        # Add tools for error analysis
        @agent.tool
        async def get_alternative_tools(ctx: RunContext[GalaxyAgentDependencies], task_description: str) -> str:
            """Get alternative tool recommendations when current tools fail."""
            return await self._call_agent_from_tool(
                "tool_recommendation", f"Find alternative tools for this task: {task_description}", ctx
            )

        return agent

    def get_system_prompt(self) -> str:
        """Get the system prompt for error analysis."""
        return """
        You are a Galaxy Project expert specializing in diagnosing tool errors and job failures.
        Your goal is to help users understand errors and provide actionable solutions.
        
        When analyzing errors:
        1. Categorize the error type and severity
        2. Identify the most likely cause based on error messages, exit codes, and context
        3. Provide clear, step-by-step solutions
        4. Suggest alternative approaches when applicable
        5. Reference relevant documentation when helpful
        6. Indicate if admin intervention is required
        
        Error categories to consider:
        - tool_configuration: Missing dependencies, incorrect tool setup
        - input_data: Invalid formats, corrupted files, missing data
        - parameters: Incorrect parameter values or combinations
        - resources: Memory, disk space, or compute limitations
        - system: Galaxy server issues, permissions, infrastructure
        - network: Download failures, connectivity issues
        
        Always provide practical, actionable advice. Be confident in your analysis
        when you have clear indicators, but acknowledge uncertainty when appropriate.
        
        AVAILABLE TOOLS:
        - get_alternative_tools: When you identify a tool failure, use this to find alternative tools that can accomplish the same task
        
        Use these tools when they would provide value to the user's specific situation.
        """

    async def get_job_details(self, job_id: int) -> Dict[str, Any]:
        """
        Get comprehensive job information for error analysis.

        Args:
            job_id: Galaxy job ID

        Returns:
            Dictionary with job details
        """
        try:
            if not self.deps.job_manager:
                return {"error": "Job manager not available"}

            job = self.deps.job_manager.get_accessible_job(self.deps.trans, job_id)
            if not job:
                return {"error": f"Job {job_id} not found or not accessible"}

            return {
                "job_id": job.id,
                "tool_id": job.tool_id,
                "tool_version": job.tool_version,
                "state": job.state,
                "exit_code": job.exit_code,
                "stderr": job.stderr[:2000] if job.stderr else "",  # Limit output size
                "stdout": job.stdout[:1000] if job.stdout else "",
                "command_line": job.command_line,
                "parameters": job.get_param_values() if hasattr(job, "get_param_values") else {},
                "create_time": job.create_time.isoformat() if job.create_time else None,
                "update_time": job.update_time.isoformat() if job.update_time else None,
                "external_id": job.external_id,
                "destination_id": job.destination_id,
            }
        except Exception as e:
            log.error(f"Error getting job details for {job_id}: {e}")
            return {"error": f"Failed to retrieve job details: {str(e)}"}

    async def get_tool_info(self, tool_id: str) -> Dict[str, Any]:
        """
        Get tool metadata and documentation.

        Args:
            tool_id: Galaxy tool identifier

        Returns:
            Dictionary with tool information
        """
        try:
            if not self.deps.tool_cache:
                return {"error": "Tool cache not available"}

            # This would need to be implemented based on Galaxy's tool cache
            # For now, return basic structure
            return {
                "tool_id": tool_id,
                "name": tool_id,  # Placeholder
                "version": "unknown",
                "description": "Tool information not available",
                "requirements": [],
                "parameters": {},
                "help_text": "",
            }
        except Exception as e:
            log.error(f"Error getting tool info for {tool_id}: {e}")
            return {"error": f"Failed to retrieve tool info: {str(e)}"}

    async def search_error_patterns(self, error_text: str) -> List[Dict[str, Any]]:
        """
        Search for similar error patterns in knowledge base.

        Args:
            error_text: Error message to search for

        Returns:
            List of similar error patterns with solutions
        """
        try:
            # This would integrate with a real error pattern database
            # For now, return some common patterns based on keywords
            patterns = []

            error_lower = error_text.lower()

            if "memory" in error_lower or "out of memory" in error_lower:
                patterns.append(
                    {
                        "pattern": "Memory exhaustion",
                        "frequency": "common",
                        "solutions": [
                            "Reduce input data size",
                            "Request more memory in job configuration",
                            "Use tools designed for large datasets",
                        ],
                    }
                )

            if "permission denied" in error_lower:
                patterns.append(
                    {
                        "pattern": "Permission error",
                        "frequency": "common",
                        "solutions": [
                            "Check file permissions",
                            "Ensure proper data library access",
                            "Contact administrator if system files are involved",
                        ],
                    }
                )

            if "command not found" in error_lower:
                patterns.append(
                    {
                        "pattern": "Missing tool or dependency",
                        "frequency": "common",
                        "solutions": [
                            "Tool may not be installed correctly",
                            "Check tool dependencies",
                            "Contact administrator about tool installation",
                        ],
                    }
                )

            return patterns

        except Exception as e:
            log.error(f"Error searching patterns: {e}")
            return []

    async def process(self, query: str, context: Dict[str, Any] = None) -> AgentResponse:
        """
        Process an error analysis request.

        Args:
            query: User's error description or question
            context: Additional context like job_id

        Returns:
            Structured error analysis response
        """
        try:
            # Enhance query with context if available
            enhanced_query = query
            if context and context.get("job_id"):
                job_details = await self.get_job_details(context["job_id"])
                if "error" not in job_details:
                    enhanced_query += f"\n\nJob Details:\n{self._format_job_context(job_details)}"

            # Run the analysis with retry logic
            result = await self._run_with_retry(enhanced_query)

            # Handle different response formats based on model capabilities
            if self._supports_structured_output():
                # Handle structured output
                if hasattr(result, "data"):
                    analysis_result = result.data
                elif hasattr(result, "output"):
                    analysis_result = result.output
                else:
                    analysis_result = result

                content = self._format_analysis_response(analysis_result)
                suggestions = self._create_suggestions(analysis_result)

                return AgentResponse(
                    content=content,
                    confidence=analysis_result.confidence,
                    agent_type=self.agent_type,
                    suggestions=suggestions,
                    metadata={
                        "error_category": analysis_result.error_category,
                        "requires_admin": analysis_result.requires_admin,
                        "has_alternatives": bool(analysis_result.alternative_approaches),
                        "method": "structured",
                    },
                )
            else:
                # Handle simple text output from DeepSeek
                response_text = str(result.data) if hasattr(result, "data") else str(result)
                parsed_result = self._parse_simple_response(response_text)

                return AgentResponse(
                    content=parsed_result.get("content", response_text),
                    confidence=parsed_result.get("confidence", "medium"),
                    agent_type=self.agent_type,
                    suggestions=parsed_result.get("suggestions", []),
                    metadata={
                        "method": "simple_text",
                        "error_category": parsed_result.get("error_category", "unknown"),
                    },
                )

        except Exception as e:
            log.error(f"Error analysis failed: {e}")
            return self._get_fallback_response(query, str(e))

    def _format_job_context(self, job_details: Dict[str, Any]) -> str:
        """Format job details for context."""
        parts = []

        if job_details.get("tool_id"):
            parts.append(f"Tool: {job_details['tool_id']}")
        if job_details.get("state"):
            parts.append(f"State: {job_details['state']}")
        if job_details.get("exit_code") is not None:
            parts.append(f"Exit Code: {job_details['exit_code']}")
        if job_details.get("stderr"):
            parts.append(f"Error Output: {job_details['stderr'][:500]}...")

        return "\n".join(parts)

    def _format_analysis_response(self, analysis: ErrorAnalysisResult) -> str:
        """Format the analysis result into user-friendly content."""
        parts = []

        # Error classification
        parts.append(f"**Error Type**: {analysis.error_category.replace('_', ' ').title()}")
        parts.append(f"**Severity**: {analysis.error_severity.title()}")

        # Likely cause
        parts.append(f"\n**Likely Cause**: {analysis.likely_cause}")

        # Solution steps
        if analysis.solution_steps:
            parts.append("\n**Recommended Solution**:")
            for i, step in enumerate(analysis.solution_steps, 1):
                parts.append(f"{i}. {step}")

        # Alternative approaches
        if analysis.alternative_approaches:
            parts.append("\n**Alternative Approaches**:")
            for approach in analysis.alternative_approaches:
                parts.append(f"• {approach}")

        # Admin notice
        if analysis.requires_admin:
            parts.append("\n⚠️ **Note**: This issue may require administrator assistance.")

        # Documentation links
        if analysis.related_documentation:
            parts.append("\n**Related Documentation**:")
            for doc in analysis.related_documentation:
                parts.append(f"• {doc}")

        return "\n".join(parts)

    def _create_suggestions(self, analysis: ErrorAnalysisResult) -> List[ActionSuggestion]:
        """Create action suggestions from analysis result."""
        suggestions = []

        # Primary solution
        if analysis.solution_steps:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.TOOL_RUN,
                    description=f"Follow the {len(analysis.solution_steps)}-step solution",
                    confidence=analysis.confidence,
                    priority=1,
                )
            )

        # Alternative approaches
        for approach in analysis.alternative_approaches[:2]:  # Limit to 2 alternatives
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.TOOL_RUN,
                    description=f"Try alternative: {approach[:100]}...",
                    confidence="medium",
                    priority=2,
                )
            )

        # Documentation
        if analysis.related_documentation:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.DOCUMENTATION,
                    description="Check related documentation",
                    confidence="high",
                    priority=2,
                )
            )

        # Admin contact if needed
        if analysis.requires_admin:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.CONTACT_SUPPORT,
                    description="Contact Galaxy administrator",
                    confidence="high",
                    priority=1,
                )
            )

        return suggestions

    def _get_simple_system_prompt(self) -> str:
        """Simple system prompt for models without structured output."""
        return """
        You are a Galaxy platform error analysis expert. Analyze the error and provide a helpful response.
        
        Respond in this exact format:
        ERROR_TYPE: [category like tool_failure, permission_denied, resource_exhausted, etc.]
        CAUSE: [brief explanation of what went wrong]
        SOLUTION: [step-by-step fix]
        CONFIDENCE: [high/medium/low]
        
        Example:
        ERROR_TYPE: command_not_found
        CAUSE: Required tool or dependency is not installed
        SOLUTION: 1. Check tool installation 2. Verify PATH settings 3. Contact admin if needed
        CONFIDENCE: high
        """

    def _parse_simple_response(self, response_text: str) -> Dict[str, Any]:
        """Parse simple text response into structured format."""
        import re

        # Extract structured information from text
        error_type = re.search(r"ERROR_TYPE:\s*([^\n]+)", response_text, re.IGNORECASE)
        cause = re.search(r"CAUSE:\s*([^\n]+)", response_text, re.IGNORECASE)
        solution = re.search(r"SOLUTION:\s*([^\n]+(?:\n\s*\d+\..*)?)", response_text, re.IGNORECASE | re.DOTALL)
        confidence = re.search(r"CONFIDENCE:\s*(\w+)", response_text, re.IGNORECASE)

        # Build content
        content_parts = []
        if cause and cause.group(1).strip():
            content_parts.append(f"**Likely cause:** {cause.group(1).strip()}")

        if solution and solution.group(1).strip():
            content_parts.append(f"**Solution:**\n{solution.group(1).strip()}")

        if not content_parts:
            content_parts = [response_text]  # Fallback to full response

        return {
            "content": "\n\n".join(content_parts),
            "confidence": confidence.group(1).lower() if confidence else "medium",
            "error_category": error_type.group(1).strip() if error_type else "unknown",
            "suggestions": [
                {
                    "action_type": "CONTACT_SUPPORT",
                    "description": "Get additional help if this doesn't resolve the issue",
                    "confidence": "medium",
                    "priority": 2,
                }
            ],
        }

    def _get_fallback_content(self) -> str:
        """Get fallback content for error analysis failures."""
        return "Unable to complete error analysis at this time."

    def _get_model_name(self) -> str:
        """Get the model name for error analysis."""
        # Check for global AI model configuration first
        if hasattr(self.deps.config, "ai_model") and self.deps.config.ai_model:
            # Use the global AI model configuration
            return f"openai:{self.deps.config.ai_model}"

        # Fall back to agent-specific configuration
        agent_config = getattr(self.deps.config, "agents", {}).get(self.agent_type, {})
        return agent_config.get("model", "openai:gpt-3.5-turbo")
