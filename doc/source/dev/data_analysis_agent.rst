Data Analysis Agent Integration
===============================

This document tracks the in-progress integration of the DSPy-based *Data Analysis
Agent* into Galaxy. The agent adapts the workflow from the external
`ChatAnalysis <https://github.com/goeckslab/ChatAnalysis>`_ project to work
within Galaxy's agent framework, dataset security model, and future Pyodide-based
execution loop.

Current Capabilities
--------------------
- Backend provides a ``data_analysis_dspy`` agent that plans with DSPy, generates
  Python snippets, executes them in Galaxy's local sandbox for now, and feeds a
  summarized execution transcript back into the planner for iterative reasoning.
- Chat UI surfaces a dataset multi-select control; selected dataset IDs are
  forwarded with every message and stored in chat history for reconciliation.
- Execution summaries (stdout/stderr/artifact metadata) are persisted in chat
  history so subsequent turns and agents can incorporate prior code runs.

Near-Term Work
--------------
- Generate presigned dataset URLs so browser sandboxes can read history datasets
  without exposing raw file paths.
- Replace local execution with a browser-based Pyodide worker that streams
  stdout/stderr and returns structured artifacts to Galaxy.
- Promote large artifacts to Galaxy history items and surface download links.
- Expand unit/integration tests covering dataset resolution, execution result
  persistence, and multi-step DSPy conversations.

Refer to ``lib/galaxy/agents/plan.md`` for the detailed implementation roadmap.
