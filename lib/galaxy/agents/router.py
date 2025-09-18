"""
Query router agent for intelligent request routing.

WHAT THIS AGENT DOES:
- Serves as the central coordinator/dispatcher for all user queries
- Analyzes incoming queries to determine which specialist agent should handle them
- Can provide direct responses for simple meta-questions (citations, greetings, etc.)
- Maintains conversation context awareness across messages
- Routes to: error_analysis, custom_tool, dataset_analyzer, or tool_recommendation agents

CURRENT CAPABILITIES:
- Keyword-based routing with confidence scoring
- Direct response for Galaxy citations and meta-questions
- Fallback routing when AI analysis fails
- Context-aware routing using conversation history
- Structured output with routing decisions and reasoning

KNOWN ISSUES:
- Routing logic is primarily keyword-based, not semantic
- Sometimes routes tool questions to wrong specialist
- Conversation history context could be better utilized
- Confidence levels are somewhat arbitrary
- No learning from user feedback on routing quality

PLANNED IMPROVEMENTS:
- Implement semantic understanding for better routing accuracy
- Add dynamic agent discovery (register new agents automatically)
- Learn from user feedback to improve routing over time
- Better multi-agent coordination for complex queries
- Add caching for common routing patterns
- Implement priority-based routing for urgent issues
- Add ability to route to multiple agents in parallel
- Better handling of ambiguous queries with clarification
"""

import logging
from typing import (
    Any,
    Dict,
    List,
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


class RoutingDecision(BaseModel):
    """Structured decision from the router agent."""

    primary_agent: str
    secondary_agents: List[str] = []
    complexity: str  # "simple" or "complex"
    confidence: str  # Use str instead of enum: "low", "medium", or "high"
    reasoning: str
    direct_response: str = ""  # If router can answer directly


class QueryRouterAgent(BaseGalaxyAgent):
    """
    Router agent that analyzes queries and routes them to appropriate specialists.

    This agent serves as the central coordinator, determining which specialized
    agent(s) should handle a user's query based on the content and context.
    """

    def _create_agent(self) -> Agent:
        """Create the router agent with structured output."""
        model_name = self.deps.config.ai_model or ""

        # DeepSeek models don't support structured output, use fallback
        if "deepseek" in model_name.lower():
            return Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                system_prompt=self._get_simple_system_prompt(),
            )
        else:
            return Agent(
                self._get_model(),
                deps_type=GalaxyAgentDependencies,
                output_type=RoutingDecision,
                system_prompt=self.get_system_prompt(),
            )

    def get_system_prompt(self) -> str:
        """Get the system prompt for the router agent."""
        return """
        You are an expert Galaxy platform routing coordinator. Your role is to understand the user's intent 
        and route their query to the most appropriate specialist agent.
        
        CONTEXT AWARENESS:
        When conversation history is provided, carefully review it to understand the full context of the current query.
        Users often refer to previous messages implicitly.
        
        AVAILABLE SPECIALIST AGENTS:
        
        1. **error_analysis** - Specializes in:
           - Debugging job failures and understanding error messages
           - Analyzing stderr/stdout output and exit codes
           - Troubleshooting tool execution problems
           - Identifying resource limitations or configuration issues
           
        2. **custom_tool** - Specializes in:
           - Creating new Galaxy tools from descriptions
           - Generating tool wrappers and YAML definitions
           - Configuring tool parameters and requirements
           - Converting command-line tools into Galaxy tools
           
        3. **dataset_analyzer** - Specializes in:
           - Analyzing dataset content and structure
           - Data quality assessment and validation
           - Format conversion and preprocessing recommendations
           - Identifying data issues and anomalies
           
        4. **tool_recommendation** - Specializes in:
           - Finding the right Galaxy tool for any analysis task
           - Suggesting tool parameters and configurations
           - Recommending workflows for multi-step analyses
           - Explaining tool capabilities and usage
           
        5. **gtn_training** - Specializes in:
           - Finding relevant Galaxy training materials and tutorials
           - Creating learning paths for specific topics
           - Explaining how to use Galaxy tools with hands-on examples
           - Recommending tutorials based on skill level
           
        6. **orchestrator** - Coordinates multiple agents for complex tasks:
           - Multi-faceted problems requiring multiple specialist perspectives
           - Complex workflows involving error resolution + tool selection + learning
           - Queries that benefit from comprehensive, coordinated responses
           - Tasks requiring both diagnosis and solution with learning materials
        
        CLASSIFICATION APPROACH:
        Analyze the user's INTENT, not just keywords. Consider:
        - What is the user trying to accomplish?
        - What type of help do they need?
        - What would be most helpful given the context?
        
        ROUTING GUIDELINES:
        
        **Route to error_analysis when user:**
        - Reports something not working or failing
        - Shares error messages or problematic output
        - Asks why a job failed or crashed
        - Needs help debugging or troubleshooting
        
        **Route to custom_tool when user:**
        - Wants to create or build a new tool
        - Needs to wrap existing software for Galaxy
        - Asks about tool development or customization
        - Wants to convert commands into Galaxy tools
        
        **Route to dataset_analyzer when user:**
        - Asks about data quality or validation
        - Needs to understand dataset structure
        - Has questions about data formats or conversion
        - Wants to analyze or explore their data
        
        **Route to tool_recommendation when user:**
        - Asks how to perform any analysis task
        - Needs to find tools for specific operations
        - Wants recommendations for data processing
        - Asks "which tool" or "how to" questions about analysis
        
        **Route to gtn_training when user:**
        - Asks for tutorials or training materials
        - Wants to learn how to use Galaxy or specific tools  
        - Needs step-by-step guides or examples
        - Asks about learning paths or where to start
        - Mentions being new to Galaxy or bioinformatics
        - Expresses uncertainty about how to approach an analysis ("I don't know what to do")
        - Needs guidance on getting started with a specific analysis type (RNA-seq, ChIP-seq, etc.)
        - Asks for help understanding a scientific technique or workflow
        - Wants to see examples of how others have done similar analyses
        - **Has data and asks broad questions** like "What should I do?" or "How do I analyze this?"
        - **Mentions having specific scientific data** (climate, genomic, proteomic, etc.) without clear direction
        - **Shows uncertainty about analysis approach** for their data type or research domain
        - **Asks general analysis questions** that would benefit from structured tutorials rather than specific tools
        
        **Route to orchestrator when user:**
        - Has complex, multi-faceted problems requiring multiple types of expertise
        - Reports errors AND needs alternative solutions AND wants to learn
        - Asks comprehensive questions combining multiple domains (e.g., "My job failed, what tools should I use instead, and how do I learn to use them?")
        - Needs both technical diagnosis and educational support
        - Has queries that clearly benefit from coordinated specialist responses
        - Asks for complete workflow guidance from problem to solution to learning
        - **Examples that need orchestration**: "My RNA-seq analysis failed, help me fix it and show me how to do it properly"
        - **NOT for orchestration**: Simple single-purpose queries that fit one specialist domain
        
        DIRECT RESPONSE GUIDELINES:
        Only provide direct_response for:
        - Simple greetings or pleasantries
        - Questions about Galaxy platform itself (not analysis)
        - Citation/reference requests (use the citation below)
        - General help or documentation requests
        
        CITATION TEMPLATE:
        For citation queries, use this direct_response:
        "To cite Galaxy, please use: Nekrutenko, A., et al. (2024). The Galaxy platform for accessible, reproducible, and collaborative data analyses: 2024 update. Nucleic Acids Research. https://doi.org/10.1093/nar/gkae410
        
        For specific tools within Galaxy, please also cite the individual tool publications listed on their respective tool pages."
        
        OUTPUT REQUIREMENTS:
        - Set primary_agent based on the main intent
        - Add secondary_agents if multiple aspects are relevant
        - Set complexity to "complex" for multi-faceted queries
        - Provide clear reasoning explaining your classification
        - Set confidence based on clarity of intent (not keyword matches)
        
        Remember: Focus on understanding what the user needs, not pattern matching keywords.
        """

    async def route_query(self, query: str, context: Dict[str, Any] = None) -> RoutingDecision:
        """
        Route a query to appropriate agent(s).

        Args:
            query: The user's query
            context: Optional context (job info, etc.)

        Returns:
            RoutingDecision with agent selection and reasoning
        """
        try:
            # Build the full query with context if we have conversation history
            full_query = query
            if context and "conversation_history" in context:
                history = context["conversation_history"]
                if history and len(history) > 0:
                    # Format conversation history for the model
                    history_text = "Previous conversation:\n"
                    for msg in history[-6:]:  # Include last 6 messages for context
                        role = msg.get("role", "unknown")
                        content = msg.get("content", "")
                        history_text += f"{role}: {content}\n"
                    history_text += f"\nCurrent query: {query}"
                    full_query = history_text

            # Use pydantic-ai for all endpoints with retry logic
            result = await self._run_with_retry(full_query)

            model_name = self.deps.config.ai_model or ""

            # Handle DeepSeek simple text response
            if "deepseek" in model_name.lower():
                response_text = str(result.data) if hasattr(result, "data") else str(result)
                return self._parse_simple_response(response_text, query)

            # Handle structured output for other models
            if hasattr(result, "data"):
                return result.data
            elif hasattr(result, "output"):
                return result.output
            elif hasattr(result, "primary_agent"):
                # It's already a RoutingDecision
                return result
            else:
                # For pydantic-ai, the result might be wrapped
                return result
        except Exception as e:
            log.error(f"Router agent failed: {e}")
            # Fallback routing logic
            return self._fallback_routing(query, context)

    def _fallback_routing(self, query: str, context: Dict[str, Any] = None) -> RoutingDecision:
        """Fallback routing when AI router fails - uses intent-based heuristics."""
        query_lower = query.lower()

        # Priority 1: Direct responses for meta-queries
        if any(phrase in query_lower for phrase in ["cite galaxy", "citation", "reference", "paper about galaxy"]):
            return RoutingDecision(
                primary_agent="router",
                secondary_agents=[],
                complexity="simple",
                confidence="high",
                reasoning="User asking about Galaxy citations",
                direct_response="To cite Galaxy, please use: Nekrutenko, A., et al. (2024). The Galaxy platform for accessible, reproducible, and collaborative data analyses: 2024 update. Nucleic Acids Research. https://doi.org/10.1093/nar/gkae410\n\nFor specific tools within Galaxy, please also cite the individual tool publications listed on their respective tool pages.",
            )

        if any(word in query_lower for word in ["hello", "hi", "hey", "greetings"]):
            return RoutingDecision(
                primary_agent="router",
                secondary_agents=[],
                complexity="simple",
                confidence="high",
                reasoning="User greeting",
                direct_response="Hello! I'm here to help you with Galaxy. What would you like to do today?",
            )

        # Priority 2: Analyze for error/debugging intent
        error_indicators = ["error", "fail", "crash", "not work", "broken", "stderr", "exit code", "died", "killed"]
        error_score = sum(1 for indicator in error_indicators if indicator in query_lower)

        # Priority 3: Analyze for tool creation intent
        creation_indicators = ["create", "build", "make", "wrap", "custom tool", "new tool", "yaml", "xml definition"]
        creation_score = sum(1 for indicator in creation_indicators if indicator in query_lower)

        # Priority 4: Analyze for data analysis intent
        data_indicators = ["dataset", "data quality", "validate", "format", "analyze my", "check my", "examine"]
        data_score = sum(1 for indicator in data_indicators if indicator in query_lower)

        # Priority 5: Analyze for tool finding intent (most common)
        tool_indicators = [
            "which tool",
            "what tool",
            "how to",
            "how do i",
            "find tool",
            "need to",
            "want to",
            "select",
            "filter",
            "process",
            "convert",
            "align",
            "map",
            "call variants",
        ]
        tool_score = sum(1 for indicator in tool_indicators if indicator in query_lower)

        # Priority 6: Analyze for training/learning intent
        training_indicators = [
            "tutorial",
            "learn",
            "training",
            "guide",
            "example",
            "how to use",
            "teach",
            "course",
            "lesson",
            "hands-on",
            "hands on",
            "step by step",
            "walkthrough",
            "getting started",
            "beginner",
            "new to",
            "help me with",
            "help me understand",
            "show me",
            "explain",
            "don't know",
            "not sure",
            "confused",
            "need help",
        ]
        training_score = sum(1 for indicator in training_indicators if indicator in query_lower)

        # Special boost for "I don't know" type phrases indicating need for guidance
        uncertainty_phrases = ["don't know what", "not sure what", "help me with", "need help with", "confused about"]
        if any(phrase in query_lower for phrase in uncertainty_phrases):
            training_score += 2  # Strong signal for needing tutorials/guidance

        # Priority 6.5: Detect "I have data" scenarios that need tutorial guidance
        data_analysis_patterns = [
            "i have",
            "i've got",
            "my data",
            "some data",
            "data i need",
            "data and",
            "what should i do",
            "what do i do",
            "how do i analyze",
            "how to analyze",
            "where do i start",
            "getting started with",
            "first time",
            "new to analyzing",
        ]
        data_guidance_score = sum(1 for pattern in data_analysis_patterns if pattern in query_lower)

        # Scientific domain keywords that often need tutorial guidance
        scientific_domains = [
            "rna-seq",
            "rna seq",
            "rnaseq",
            "dna-seq",
            "dna seq",
            "dnaseq",
            "chip-seq",
            "chip seq",
            "chipseq",
            "climate",
            "environmental",
            "ecology",
            "genomic",
            "genome",
            "genetics",
            "proteomic",
            "protein",
            "proteome",
            "transcriptomic",
            "transcriptome",
            "metagenome",
            "microbiome",
            "variant",
            "mutation",
            "snp",
            "phylogen",
            "evolution",
            "bioinformatic",
            "computational biology",
        ]
        domain_score = sum(1 for domain in scientific_domains if domain in query_lower)

        # Strong signal: user has data + scientific domain + uncertainty = needs tutorial
        if data_guidance_score > 0 and (
            domain_score > 0 or any(word in query_lower for word in ["analyze", "analysis", "data"])
        ):
            training_score += 3  # Very strong signal for GTN tutorials
        elif data_guidance_score > 0:
            training_score += 2  # Moderate signal for tutorials
        elif domain_score > 0 and any(
            word in query_lower for word in ["new to", "getting started", "first time", "beginner"]
        ):
            training_score += 2  # Domain-specific learning needs

        # ORCHESTRATION DETECTION LOGIC
        # =============================
        # This section determines if a query is complex enough to require coordination
        # between multiple agents. The orchestration system was tuned to be conservative
        # after user feedback that it was "too proactive" and triggered unnecessarily.
        #
        # Key Design Principles:
        # 1. Conservative by default - only orchestrate truly complex queries
        # 2. Require explicit indicators of multi-part requests
        # 3. High confidence thresholds to avoid false positives
        # 4. Focus on user intent rather than just keyword matching

        orchestration_score = 0

        # STEP 1: Count domains with HIGH confidence (score >= 3)
        # Only consider domains where we have strong confidence, not just weak signals.
        # This prevents orchestration from triggering on queries that just happen to
        # mention multiple topics in passing.
        active_scores = [
            ("error", error_score),
            ("tool", tool_score),
            ("training", training_score),
            ("data", data_score),
            ("creation", creation_score),
        ]
        high_scoring_domains = sum(1 for _, score in active_scores if score >= 3)  # Conservative threshold

        # STEP 2: Look for EXPLICIT orchestration language
        # These patterns indicate the user explicitly wants a multi-part response.
        # We only orchestrate when the user clearly asks for it, not when we guess they might want it.
        orchestration_indicators = [
            # Conjunctive phrases that explicitly connect multiple requests
            # Example: "Help me fix this error and also show me how to prevent it"
            "and also",
            "and then",
            "plus also",
            "also help me",
            "also need to",
            "as well as",
            # Comprehensive request language that explicitly asks for complete solutions
            # Example: "I need a complete workflow for RNA-seq analysis"
            "complete workflow",
            "full solution",
            "entire process",
            "comprehensive help",
            # Problem-solving + learning patterns (explicit combination requests)
            # Example: "Fix this error and teach me why it happened"
            "fix.*and.*teach",
            "solve.*and.*learn",
            "help.*fix.*and.*show",
            # Explicit multi-step process requests
            # Example: "Walk me through the step by step workflow"
            "step by step workflow",
            "start to finish",
            "beginning to end",
        ]

        # STEP 3: Check if query contains explicit orchestration language
        import re

        has_explicit_orchestration = any(re.search(indicator, query_lower) for indicator in orchestration_indicators)

        # STEP 4: CONSERVATIVE ORCHESTRATION SCORING
        # After user feedback that orchestration was "too proactive", we implemented
        # much stricter criteria. Orchestration only triggers when we have high confidence
        # that the user genuinely needs multiple agents working together.

        # PRIMARY TRIGGER: 3+ high-confidence domains
        # This means the query strongly indicates needs across at least 3 different specialties
        # Example: Error troubleshooting + tool recommendation + training materials
        if high_scoring_domains >= 3:
            orchestration_score += high_scoring_domains  # Score scales with complexity

        # SECONDARY TRIGGER: 2 high-confidence domains + explicit request
        # User explicitly asks for multi-part help AND we detect 2+ strong domain signals
        # Example: "Fix this error and also help me find alternative tools"
        elif high_scoring_domains >= 2 and has_explicit_orchestration:
            orchestration_score += 3  # Fixed boost for explicit multi-part requests

        # BONUS: Additional points for explicit orchestration language
        # Even if we don't hit the primary triggers, explicit requests get some consideration
        if has_explicit_orchestration:
            orchestration_score += 2

        # STEP 5: SPECIALIZED ORCHESTRATION PATTERNS
        # Certain combinations of domains are particularly well-suited for orchestration

        # Pattern 1: Error + Learning + Tools (classic troubleshooting + education)
        # Example: "This tool failed, help me fix it and show me how to use it properly"
        if error_score >= 2 and training_score >= 2 and tool_score >= 1:
            orchestration_score += 3

        # Pattern 2: Error + Tools + Explicit request (problem-solving focus)
        # Example: "My analysis failed and I need alternative tools to complete my workflow"
        elif error_score >= 2 and tool_score >= 2 and has_explicit_orchestration:
            orchestration_score += 2

        # STEP 6: Query complexity bonus
        # Very long queries often indicate complex, multi-faceted needs
        # Threshold raised from 15 to 25 words to be more conservative
        if len(query.split()) > 25:
            orchestration_score += 1  # Small bonus for complex queries

        # STEP 7: AGENT SELECTION
        # All domain scores (including orchestration) compete to determine the best agent.
        # The orchestrator is treated as just another specialist agent that happens to coordinate others.
        scores = {
            "error_analysis": (error_score, "User appears to be reporting an issue or error"),
            "custom_tool": (creation_score, "User wants to create or customize a tool"),
            "dataset_analyzer": (data_score, "User needs help with data analysis or validation"),
            "tool_recommendation": (tool_score, "User needs help finding or using tools"),
            "gtn_training": (training_score, "User wants training materials or tutorials"),
            "orchestrator": (
                orchestration_score,
                "User has complex multi-faceted query requiring coordinated response",
            ),
        }

        # Find the highest-scoring agent
        # Default to tool_recommendation as it handles the most common case
        best_agent = "tool_recommendation"
        best_score = 0
        reasoning = "Default routing to tool recommendation"

        for agent, (score, reason) in scores.items():
            if score > best_score:
                best_score = score
                best_agent = agent
                reasoning = reason

        # STEP 8: CONFIDENCE DETERMINATION
        # Confidence reflects how certain we are about the routing decision
        # Higher scores indicate stronger signal-to-noise ratio in the query
        if best_score >= 2:
            confidence = "high"  # Strong indicators present
        elif best_score == 1:
            confidence = "medium"  # Some indicators present
        else:
            confidence = "low"  # No clear indicators, using default
            reasoning = "No clear intent indicators found, defaulting to tool recommendation"

        # Check for complexity (multiple intents)
        active_intents = sum(1 for _, (score, _) in scores.items() if score > 0)
        complexity = "complex" if active_intents > 1 else "simple"

        # Add secondary agents if multiple intents detected
        secondary_agents = []
        if active_intents > 1:
            for agent, (score, _) in scores.items():
                if score > 0 and agent != best_agent:
                    secondary_agents.append(agent)

        return RoutingDecision(
            primary_agent=best_agent,
            secondary_agents=secondary_agents[:2],  # Limit to 2 secondary agents
            complexity=complexity,
            confidence=confidence,
            reasoning=f"Fallback routing: {reasoning}",
            direct_response="",
        )

    async def process(self, query: str, context: Dict[str, Any] = None) -> AgentResponse:
        """
        Process a routing request and return guidance.

        For the router agent, processing means making routing decisions
        and potentially providing direct responses for simple queries.
        """
        routing_decision = await self.route_query(query, context)

        # If we have a direct response, return it
        if routing_decision.direct_response:
            return AgentResponse(
                content=routing_decision.direct_response,
                confidence=routing_decision.confidence,
                agent_type=self.agent_type,
                suggestions=[],
                metadata={"routing_decision": routing_decision.dict(), "handled_directly": True},
            )

        # Otherwise, provide routing guidance
        content = self._format_routing_response(routing_decision)

        suggestions = [
            ActionSuggestion(
                action_type=ActionType.TOOL_RUN,
                description=f"Route to {routing_decision.primary_agent} agent",
                parameters={"agent": routing_decision.primary_agent},
                confidence=routing_decision.confidence,
                priority=1,
            )
        ]

        # Add secondary agent suggestions
        for secondary_agent in routing_decision.secondary_agents:
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.TOOL_RUN,
                    description=f"Also consult {secondary_agent} agent",
                    parameters={"agent": secondary_agent},
                    confidence="medium",
                    priority=2,
                )
            )

        return AgentResponse(
            content=content,
            confidence=routing_decision.confidence,
            agent_type=self.agent_type,
            suggestions=suggestions,
            metadata={"routing_decision": routing_decision.dict(), "handled_directly": False},
            reasoning=routing_decision.reasoning,
        )

    def _format_routing_response(self, decision: RoutingDecision) -> str:
        """Format the routing decision into a user-friendly response."""
        content_parts = [f"I'll route your query to the {decision.primary_agent.replace('_', ' ')} specialist."]

        if decision.reasoning:
            content_parts.append(f"Reasoning: {decision.reasoning}")

        if decision.secondary_agents:
            secondary_list = ", ".join(agent.replace("_", " ") for agent in decision.secondary_agents)
            content_parts.append(f"I may also consult: {secondary_list}")

        if decision.complexity == "complex":
            content_parts.append("This appears to be a complex query that may require multiple steps to resolve.")

        return " ".join(content_parts)

    def _get_simple_system_prompt(self) -> str:
        """Simple system prompt for models that don't support structured output."""
        return """
        You are a Galaxy platform routing assistant. Analyze the user's query and respond with a simple routing decision.
        
        Available agents:
        - error_analysis: For debugging, troubleshooting, job failures
        - custom_tool: For creating new tools, tool development
        - tool_recommendation: For finding tools, "how to" questions, analysis guidance
        - gtn_training: For tutorials, learning materials, training
        
        Respond in this exact format:
        ROUTE_TO: [agent_name]
        REASONING: [brief explanation]
        
        Example:
        ROUTE_TO: tool_recommendation
        REASONING: User asking how to perform analysis task
        """

    def _parse_simple_response(self, response_text: str, query: str) -> RoutingDecision:
        """Parse simple text response from DeepSeek into RoutingDecision."""
        import re

        # Extract ROUTE_TO and REASONING from response
        route_match = re.search(r"ROUTE_TO:\s*(\w+)", response_text, re.IGNORECASE)
        reasoning_match = re.search(r"REASONING:\s*(.+?)(?:\n|$)", response_text, re.IGNORECASE | re.DOTALL)

        if route_match:
            agent = route_match.group(1).lower()
            reasoning = reasoning_match.group(1).strip() if reasoning_match else "DeepSeek routing"

            # Validate agent exists
            valid_agents = ["error_analysis", "custom_tool", "tool_recommendation", "gtn_training"]
            if agent not in valid_agents:
                agent = "tool_recommendation"  # Default fallback
                reasoning = f"Fallback to tool_recommendation. Original: {reasoning}"

            return RoutingDecision(
                primary_agent=agent,
                secondary_agents=[],
                complexity="simple",
                confidence="medium",
                reasoning=reasoning,
            )
        else:
            # Couldn't parse, use fallback
            return self._fallback_routing(query)

    def _get_fallback_content(self) -> str:
        """Get fallback content for router failures."""
        return "Unable to determine the best routing for your query."
