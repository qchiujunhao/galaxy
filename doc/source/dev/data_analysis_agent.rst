Data Analysis Agent Integration
===============================

This document tracks the in-progress integration of the DSPy-based *Data Analysis
Agent* into Galaxy. The agent adapts the workflow from the external
`ChatAnalysis <https://github.com/goeckslab/ChatAnalysis>`_ project to work
within Galaxy's agent framework, dataset security model, and a browser-based
Pyodide execution loop.

Current Capabilities
--------------------
- Backend previously exposed a ``data_analysis_dspy`` agent that planned with DSPy and emitted
  browser-ready execution payloads, but this variant is temporarily disabled while we stabilize it.
- Chat UI surfaces a dataset multi-select control; selected dataset IDs are
  forwarded with every message, stored in chat history, and translated into
  downloadable dataset URLs for the Pyodide worker.
- Execution of generated Python happens automatically in the user's browser;
  results are posted back to Galaxy and rendered inline (stdout/stderr plus
  downloadable artifacts).
- Execution artifacts are uploaded to the user's history with signed download links and appear in the chat transcript.
- The chat UI maintains a WebSocket stream for automatic follow-ups (and falls back to HTTP when unavailable).


Near-Term Work
--------------
- Harden the artifact upload story (size limits, resumable uploads, streaming).
- Expand unit/integration tests covering dataset resolution, execution result
  persistence, and multi-step DSPy conversations.
- Provide granular UX around Pyodide errors (package install failures, dataset
  fetch problems, timeout messaging) and allow user-controlled reruns.

Refer to ``lib/galaxy/agents/plan_unused.md`` for the detailed implementation roadmap.
