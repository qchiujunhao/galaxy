"""
Custom tool creation agent for Galaxy - simplified version using UserToolSource.
"""

import logging
import re
from pathlib import Path
from typing import (
    Any,
    Dict,
    Optional,
)

import yaml
from pydantic import (
    BaseModel,
    Field,
)
from pydantic_ai import Agent

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    GalaxyAgentDependencies,
)

log = logging.getLogger(__name__)


class SimpleTool(BaseModel):
    """Simplified tool model for basic Galaxy tools."""

    id: str = Field(description="Tool ID, lowercase with underscores")
    name: str = Field(description="Human-readable tool name")
    version: str = Field(description="Tool version, e.g. 1.0.0")
    description: str = Field(description="Brief tool description")
    command: str = Field(description="Shell command to execute")
    container: str = Field(description="Docker/Singularity container")
    inputs_description: str = Field(description="Description of input files/parameters")
    outputs_description: str = Field(description="Description of output files")


class CustomToolAgent(BaseGalaxyAgent):
    """
    Agent that creates custom Galaxy tools with fallback for models without structured output.
    """

    def _create_agent(self) -> Agent:
        """Create agent - tries structured output first."""
        return Agent(
            self._get_model(),
            deps_type=GalaxyAgentDependencies,
            output_type=SimpleTool,
            system_prompt=self.get_system_prompt(),
        )

    def _create_text_agent(self) -> Agent:
        """Create text-only agent for models without structured output."""
        return Agent(
            self._get_model(),
            deps_type=GalaxyAgentDependencies,
            output_type=str,  # Just return text
            system_prompt=self.get_text_prompt(),
        )

    def get_system_prompt(self) -> str:
        """System prompt for structured output."""
        prompt_path = Path(__file__).parent / "prompts" / "custom_tool_structured.md"
        return prompt_path.read_text()

    def get_text_prompt(self) -> str:
        """System prompt for text-only fallback."""
        prompt_path = Path(__file__).parent / "prompts" / "custom_tool_text.md"
        return prompt_path.read_text()

    def _parse_yaml_from_text(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract and parse YAML from text response."""
        # Find YAML block
        yaml_match = re.search(r"```yaml\n(.*?)\n```", text, re.DOTALL)
        if not yaml_match:
            # Try without markers
            yaml_match = re.search(r"(class:\s*GalaxyUserTool.*?)(?:\n\n|\Z)", text, re.DOTALL)

        if yaml_match:
            yaml_text = yaml_match.group(1)
            try:
                return yaml.safe_load(yaml_text)
            except yaml.YAMLError as e:
                log.error(f"Failed to parse YAML: {e}")
                return None
        return None

    async def process(self, query: str, context: Dict[str, Any] = None) -> AgentResponse:
        """Process tool creation request with fallback."""

        # Check if model supports structured output (use agent config, not global config)
        model_name = self._get_agent_config("model", "").lower()
        use_structured = "scout" in model_name or "gpt" in model_name or "claude" in model_name

        try:
            if use_structured:
                log.info(f"Using structured output for {model_name}")
                # Try structured output first
                try:
                    result = await self._run_with_retry(query)
                    tool = result.output if hasattr(result, "output") else result.data

                    # Create YAML from structured output
                    tool_yaml = f"""class: GalaxyUserTool
id: {tool.id}
name: {tool.name}
version: {tool.version}
description: {tool.description}
container: {tool.container}
shell_command: {tool.command}
inputs:
  - {tool.inputs_description}
outputs:
  - {tool.outputs_description}"""

                    metadata = {
                        "tool_id": tool.id,
                        "tool_name": tool.name,
                        "tool_yaml": tool_yaml,
                        "method": "structured",
                    }

                except Exception as e:
                    log.warning(f"Structured output failed for {model_name}, falling back to text: {e}")
                    use_structured = False

            if not use_structured:
                log.info(f"Using simple fallback for {model_name} - generating basic template")
                # For DeepSeek, just generate a basic template since it hangs on complex prompts
                # Extract key info from query
                tool_id = "custom_tool"
                tool_name = "Custom Tool"
                if "bwa" in query.lower():
                    tool_id = "bwa_mem_paired"
                    tool_name = "BWA-MEM Paired End"
                    container = "quay.io/biocontainers/bwa:0.7.17"
                    command = "bwa mem reference.fa reads1.fq reads2.fq > output.sam"
                    inputs = "- Reference genome (FASTA)\n  - Read 1 (FASTQ)\n  - Read 2 (FASTQ)"
                    outputs = "- Aligned reads (SAM)"
                else:
                    container = "ubuntu:latest"
                    command = "echo 'Tool command here'"
                    inputs = "- Input files"
                    outputs = "- Output files"

                tool_yaml = f"""class: GalaxyUserTool
id: {tool_id}
name: {tool_name}
version: 1.0.0
description: Tool created from user request
container: {container}
shell_command: {command}
inputs:
  {inputs}
outputs:
  {outputs}"""

                metadata = {
                    "tool_id": tool_id,
                    "tool_name": tool_name,
                    "tool_yaml": tool_yaml,
                    "method": "simple_template",
                }

            # Create response
            response_content = f"""I've created a custom Galaxy tool:

```yaml
{metadata['tool_yaml']}
```

**Tool ID**: {metadata['tool_id']}
**Name**: {metadata['tool_name']}

The tool is ready to use in Galaxy."""

            # Add action suggestions
            suggestions = [
                ActionSuggestion(
                    action_type=ActionType.SAVE_TOOL,
                    description="Save this tool to Galaxy",
                    parameters={"tool_yaml": metadata["tool_yaml"], "tool_id": metadata["tool_id"]},
                    confidence="high" if metadata["method"] == "structured" else "medium",
                    priority=1,
                ),
                ActionSuggestion(
                    action_type=ActionType.TEST_TOOL,
                    description="Test this tool",
                    parameters={"tool_id": metadata["tool_id"]},
                    confidence="medium",
                    priority=2,
                ),
            ]

            return AgentResponse(
                content=response_content,
                confidence="high" if metadata["method"] == "structured" else "medium",
                agent_type=self.agent_type,
                suggestions=suggestions,
                metadata=metadata,
            )

        except Exception as e:
            log.error(f"Tool creation failed: {e}")
            return AgentResponse(
                content=f"Failed to create tool: {str(e)}\n\nPlease provide clear requirements for your tool.",
                confidence="low",
                agent_type=self.agent_type,
                suggestions=[],
                metadata={"error": str(e)},
            )
