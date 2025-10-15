# Data Analysis Agent Integration Plan

## Context
`chat_dspy.py` originates from the standalone ChatAnalysis project (NiceGUI-based interactive tool). In this effort we integrate its DSPy-driven workflow directly into the Galaxy codebase, adapting it to Galaxy's agent framework, security model, and UI. The original UI is not brought over; only its planning/execution logic (plus `examples.json`) informs our implementation.

## Objectives
- Embed a DSPy-powered `data_analysis` agent that can plan tasks, generate Python code, execute analyses, and iterate until completion.
- Provide robust dataset selection (UI multi-select + natural-language resolution) for the agent to operate on Galaxy datasets.
- Execute generated Python in a browser-based Pyodide sandbox, capture outputs, and feed results back into the agent loop automatically.
- Persist execution artifacts securely and expose telemetry for observability.

## Phase Breakdown

### Phase 1 — Reference Integration & Schema Support
1. **Vendor References**
   - Copy `chat_dspy.py` into `lib/galaxy/chat_dspy.py` (clearly comment that it originates from ChatAnalysis and may require periodic sync).
   - Copy `examples.json` into `lib/galaxy/agents/examples.json` for DSPy bootstrapping.
2. **Schema Extensions**
   - Update `lib/galaxy/agents/base.py` and `lib/galaxy/schema/agents.py` with `Artifact`, `ExecutionTask`, `ExecutionResult`, and `ActionType.PYODIDE_EXECUTE`.
   - Regenerate TypeScript API schema (if needed) so frontend has typed access to the new fields.
3. **Chat API Endpoint**
   - Implement `POST /api/chat/exchange/{exchange_id}/pyodide_result` in `lib/galaxy/webapps/galaxy/api/chat.py` to accept execution results, storing via `ChatManager`.
   - Include execution result messages when returning chat history.
4. **Logging & Telemetry Hooks**
   - Add logging scaffolding for execution result submissions (task ID, duration, outcome).

### Phase 2 — Dataset Selection & Context Plumbing
1. **Frontend Enhancements**
   - Add dataset multi-select control in ChatGXY allowing the user to pick datasets from the active history (powered by `/api/datasets`).
   - Display selected datasets as chips/tags; allow removal and persistence across messages within a chat exchange.
2. **Backend Context**
   - Extend chat request payloads to include `context.dataset_ids`.
   - Persist selected dataset IDs per chat exchange via `ChatManager`.
3. **Dataset Resolution Tool**
   - Implement `resolve_dataset_reference()` helper that searches user-accessible datasets by name/HID/tag.
   - Expose the helper via a `find_datasets` tool attached to the Data Analysis Agent (adapted from ChatAnalysis functionality).
4. **Context Propagation**
   - Ensure orchestrator passes dataset context to the data-analysis agent and includes dataset metadata in execution tasks (e.g., signed URLs for Pyodide downloads).

### Phase 3 — Pyodide Runtime & UI Execution Loop
1. **Runtime Infrastructure**
   - Create `client/src/workers/pyodideWorker.ts` to load Pyodide, enforce package allowlist, run code with timeouts, capture FS outputs.
   - Build `client/src/composables/usePyodideRunner.ts` to manage worker lifecycle, queue tasks, and provide a promise-based `runTask()` API.
2. **ChatGXY Integration**
   - Detect `agentResponse.metadata.pyodide_task`, trigger Pyodide execution automatically, and update message state with execution status.
   - Display stdout/stderr, artifacts, and errors in a structured panel beneath the message.
3. **Result Submission**
   - POST execution results (stdout, stderr, artifacts, metadata) back to `/api/chat/exchange/{id}/pyodide_result`.
   - Update chat transcript and dataset provenance accordingly.
4. **Artifact Handling Strategy**
   - For small artifacts, embed base64 inline; for large ones, store temporarily and reference via path (plan for promotion to Galaxy datasets later).

### Phase 4 — DataAnalysisAgent Implementation
1. **Agent Module**
   - Implement `lib/galaxy/agents/data_analysis.py`:
     - Configure DSPy with Galaxy AI settings; load examples from `examples.json`.
     - Maintain analysis state (steps completed, execution history) per request.
     - Coordinate queries to DSPy plan to determine next actions (generate code vs. summarize).
   - Provide planned execution tasks via metadata and parse returned execution results before continuing.
2. **Adaptations from ChatAnalysis**
   - Replace NiceGUI dependencies with Galaxy equivalents; rely on chat API/agents instead of interactive UI components.
   - Integrate dataset access via Galaxy managers, not direct file paths.
   - Ensure code generation respects Galaxy security (no direct DB access, sanitized imports).
3. **Registration & Prompts**
   - Register the new agent in `lib/galaxy/agents/__init__.py`.
   - Update router/orchestrator prompts, emphasizing when to choose `data_analysis`.
   - Add “Data Analysis” option to ChatGXY agent selector.
4. **Fallback Behavior**
   - If DSPy not installed, return a graceful error message with instructions for administrators.

### Phase 5 — Testing, Telemetry, Documentation
1. **Backend Tests**
   - Unit tests for dataset resolver, execution result endpoint, and orchestrator logic awaiting execution results.
   - Tests ensuring dataset IDs are stored and retrieved correctly in chat history.
2. **Frontend Tests**
   - Unit/e2e tests covering dataset multi-select UI, Pyodide runner interactions, and artifact rendering.
3. **Telemetry Validation**
   - Verify logs capture execution metrics; optionally integrate with monitoring (StatsD/Prometheus).
4. **Documentation**
   - Update developer docs and/or README with:
     - Reference sync instructions for ChatAnalysis files.
     - Pyodide setup requirements and package policy.
     - Dataset selection workflow and security model.
     - DSPy configuration and troubleshooting.

## Task Checklist
1. [x] Vendor `chat_dspy.py` & `examples.json`, document provenance.
2. [x] Extend base/ schema models for execution tasks & artifacts.
3. [x] Implement `/api/chat/exchange/{id}/pyodide_result` + persistence via ChatManager.
4. [x] Build dataset multi-select UI in ChatGXY.
5. [x] Plumb dataset IDs through chat payloads and storage.
6. [x] Implement dataset resolver tool & integrate with agent.
7. [ ] Scaffold Pyodide worker + composable and wire into ChatGXY.
8. [ ] Post execution results back to backend and display artifacts.
9. [x] Implement `DataAnalysisAgent` leveraging DSPy logic from chat_dspy.
10. [ ] Register agent, update prompts/UI selectors.
11. [ ] Add telemetry logging & metrics.
12. [ ] Implement backend/frontend tests.
13. [ ] Update documentation.

## Risks & Mitigations
- **Pyodide Load Times**: mitigate with lazy loading and caching instructions.
- **Security**: enforce dataset permissions and sandboxed execution; log artifacts for audit.
- **DSPy Dependency**: provide fallback messaging when not installed; document setup.
- **Large Artifacts**: limit inline size and plan for dataset promotion.

## Open Questions
- Do we need rate limits on execution tasks per chat to prevent abuse?
- Should users be able to opt out of auto-execution?
- Preferred distribution method for Pyodide assets (CDN vs local static files).

