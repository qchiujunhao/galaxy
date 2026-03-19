# Temporary Branch TODO

Last updated: 2026-03-19

## Scope

This file tracks the remaining work for this branch only.

Explicitly out of scope for this branch:

- Dev-server-only worker failures outside the branch runtime path
- The `isAwaitingExecution` alert / `pyodide_status` promise theory, which was traced to environment behavior rather than this branch's execution flow

## Status Summary

### Done

- Simplified `client/src/components/ChatGXY/pyodide.worker.ts` so it is now a minimal worker entrypoint
- Moved Pyodide execution/runtime logic out of the worker entrypoint into `client/src/components/ChatGXY/pyodideRuntime.ts`
- Updated `client/src/composables/usePyodideRunner.ts` to create the worker with `{ type: "module" }`
- Enabled `corepack` and activated `pnpm@10.26.1` so client verification commands can run in this environment
- Verified the three Pyodide client files with Prettier; ESLint now reports warnings only and no errors

### Remaining

- Replace `AlreadyEncodedDatabaseIdField` with reuse of existing validation logic
- Remove stale DSPy code paths and dead files
- Confirm whether "subsequent generated files require refresh" is still a real issue
- Run branch-relevant verification commands

## Detailed TODO

### 0. Address latest review findings

Status: `completed`

Files:

- `lib/galaxy/webapps/galaxy/api/chat.py`
- `client/src/composables/agents/dataAnalysis.ts`
- `client/src/components/ChatGXY.vue`

Review findings to fix:

1. `lib/galaxy/webapps/galaxy/api/chat.py:705`
   - `[P1] Check chat-stream ownership before registering the websocket`
   - `chat_exchange_stream()` currently accepts/registers a websocket without verifying the user owns the exchange.

2. `lib/galaxy/webapps/galaxy/api/chat.py:331`
   - `[P2] Preserve selected dataset ids on the first turn of a new chat`
   - The first persisted message of a newly created exchange drops `dataset_ids`, so dataset context is lost on reload/recovery.

3. `client/src/composables/agents/dataAnalysis.ts:581`
   - `[P2] Build the websocket URL without a double slash at root`
   - When `getAppRoot()` is `/`, the current URL builder produces `//api/.../stream` and the websocket path does not match the backend route.

4. `client/src/components/ChatGXY.vue:221`
   - `[P2] Normalize the initial assistant response before rendering it`
   - The initial `/api/chat` success path bypasses the shared normalization path, so analysis metadata/artifacts/collapse state are not reflected immediately in the UI.

Validation note:

- `test/integration/test_agents.py::TestAgentsApiMocked::test_chat_with_dataset_context_records_execution_metadata`
  now passes after updating the test to use the current API test interactor style (`json=True`) and a real dataset id from
  `DatasetPopulator`, while asserting persisted dataset context and execution metadata rather than a brittle serialized-id format.

Completed:

1. Locked down websocket auth/ownership in the backend
2. Persisted `dataset_ids` on first-message exchange creation
3. Fixed the root-path websocket URL builder
4. Routed initial assistant rendering through the shared normalization path

### 1. Replace `AlreadyEncodedDatabaseIdField`

Status: `pending`

Files:

- `lib/galaxy/schema/agents.py`

Plan:

- Remove the local `AlreadyEncodedDatabaseIdField` alias
- Change `UploadedArtifact.dataset_id` and `UploadedArtifact.history_id` away from that custom alias
- Reuse existing `ensure_valid_id` validation inside the model instead of introducing another field abstraction

Reason:

- Fixes the already-encoded ID use case without adding a new shared schema concept
- Avoids reviewer pushback about reimplementing existing ID helpers

### 2. Clean up DSPy leftovers

Status: `pending`

Files:

- `lib/galaxy/managers/agents.py`
- `lib/galaxy/agents/__init__.py`

Plan:

- Remove commented-out DSPy imports, disabled registration lines, and related dummy compatibility code that is no longer used

Reason:

- Current DSPy remnants appear to be dead code and create review noise

### 3. Remove `lib/galaxy/chat_dspy.py` if unused

Status: `pending`

Files:

- `lib/galaxy/chat_dspy.py`

Plan:

- Confirm there are no remaining runtime references or test dependencies
- Delete the file if still unused

Reason:

- It currently appears to be an unused vendored/reference artifact rather than part of the active code path

### 4. Confirm the generated-files refresh behavior

Status: `pending`

Files:

- `client/src/composables/agents/dataAnalysis.ts`
- potentially related ChatGXY rendering files if a bug is confirmed

Plan:

- Verify whether subsequent generated files appear without a full page refresh
- If the issue is real, fix the message/artifact update flow
- If not reproducible, mark this item complete with no code change

Reason:

- This is still the only remaining Pyodide UX concern that may belong to this branch

### 5. Verification

Status: `pending`

Backend:

- `make format`
- `tox -e mypy`

Client:

- `cd client && yarn run -- prettier check`
- `cd client && yarn run eslint`

Notes:

- Only fix errors related to this branch's changes
- In this environment, `pnpm` is now available and should be used for local verification
- Current status for the Pyodide client files touched so far:
  - `pnpm exec prettier --check` passes
  - `pnpm exec eslint` reports warnings only (`no-explicit-any`), with no blocking errors

## Junhao Task Coverage

Covered and still relevant:

- Why `AlreadyEncodedDatabaseIdField` was added
- DSPy commented-out code in `lib/galaxy/managers/agents.py`
- DSPy commented-out code in `lib/galaxy/agents/__init__.py`
- Whether `lib/galaxy/chat_dspy.py` is still relevant
- Whether subsequent generated files require refresh

Covered and now considered done for branch purposes:

- `pyodide.worker.ts` simplification
- Moving Pyodide worker methods out of the worker entrypoint and into reusable runtime code
- The worker `export` syntax issue addressed by module-worker creation plus minimal entrypoint structure

Covered but out of scope for this branch:

- `isAwaitingExecution` alert theory
- `pyodide_status` promise theory
- Dev-server-only worker failure investigation beyond the branch runtime path
