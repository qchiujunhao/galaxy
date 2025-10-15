"""Galaxy Data Analysis Agent.

This agent orchestrates the Galaxy data-analysis workflow using DSPy's CodeReact
planner when available and falls back to the standard ReAct module otherwise.
Code generation and execution control are delegated entirely to DSPy.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import tempfile
import traceback
import builtins as py_builtins
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    ActionSuggestion,
    ActionType,
    AgentResponse,
    BaseGalaxyAgent,
    GalaxyAgentDependencies,
)
from galaxy.model import HistoryDatasetAssociation

from .dspy_adapter import (
    DSPyPlanResult,
    GalaxyDSPyPlanner,
    build_context_text,
)

log = logging.getLogger(__name__)

class DataAnalysisAgent(BaseGalaxyAgent):
    """Agent orchestrating dataset analysis with generated code execution."""

    USE_PYDANTIC_AGENT = False
    DEBUG_LOG_PATH = Path(tempfile.gettempdir()) / "galaxy_data_analysis_debug.log"

    def __init__(self, deps: GalaxyAgentDependencies):
        super().__init__(deps)
        self.agent_type = "data_analysis"
        self._examples_path = Path(__file__).parent / "examples.json"
        self._example_snippets = self._load_example_snippets(self._examples_path)
        self._planner = GalaxyDSPyPlanner(deps)
        self._last_query: str = ""

    # BaseGalaxyAgent requires these abstract methods, but the DSPy variant does
    # not use the pydantic runtime.
    def _create_agent(self):  # type: ignore[override]
        raise RuntimeError("DataAnalysisAgent relies on DSPy CodeReact and does not expose a pydantic agent.")

    def get_system_prompt(self) -> str:  # type: ignore[override]
        return (
            "You are Galaxy's data analysis agent. Generate Python that runs inside Galaxy's sandboxed execution environment."
            " Use the helper functions load_dataset('<alias>') to obtain a pandas DataFrame or get_dataset_path('<alias>') for filesystem paths."
            " Dataset aliases include the dataset ID, original name, and dataset_<index> values listed in the context."
            " Save any artifacts to outputs_dir/generated_file/ and report concise, well-structured insights."
            " Always return a valid JSON object in your final answer; avoid Python reprs or non-JSON constructs."
        )


    async def process(self, query: str, context: Optional[Dict[str, Any]] = None) -> AgentResponse:
        context = context or {}
        datasets = context.get("dataset_ids", [])
        conversation_history = context.get("conversation_history", [])

        execution_messages = [entry for entry in conversation_history if entry.get("role") == "execution_result"]
        last_executed_task = self._extract_last_executed_task(execution_messages)

        context_text = build_context_text(
            query,
            datasets,
            conversation_history,
            execution_messages,
            self._example_snippets,
        )

        self._last_query = query
        self._last_context_text = context_text

        # Configure DSPy on the current (main) thread before offloading planning work.
        self._planner.ensure_lm_configured()

        try:
            plan: DSPyPlanResult = await asyncio.to_thread(self._planner.plan, query, context_text)
        except Exception as exc:  # pragma: no cover - defensive path
            log.exception("Data analysis planner failed")
            error_message = f"Planning failed: {exc}" if exc else "Planning failed"
            metadata: Dict[str, Any] = {
                "datasets_used": datasets,
                "planner": "dspy",
                "planning_error": str(exc),
            }
            suggestions = [
                ActionSuggestion(
                    action_type=ActionType.REFINE_QUERY,
                    description="Adjust the request and try again.",
                    parameters={},
                    confidence="low",
                    priority=1,
                )
            ]
            return AgentResponse(
                content=error_message,
                confidence="low",
                agent_type=self.agent_type,
                suggestions=suggestions,
                metadata=metadata,
            )

        return self._response_from_plan(plan, query, context_text, datasets, last_executed_task)

    def _response_from_plan(
        self,
        plan: DSPyPlanResult,
        question: str,
        context_text: str,
        datasets: List[str],
        last_executed_task: Optional[Dict[str, Any]],
    ) -> AgentResponse:
        active_plan = plan
        code = (active_plan.python_code or "").strip()
        requirements = active_plan.requirements or []
        normalized_requirements = self._normalize_requirements(requirements)

        execution_result: Optional[Dict[str, Any]] = None
        should_execute = self._should_enqueue_execution(code, normalized_requirements, last_executed_task)
        if should_execute and code:
            execution_result = self._execute_generated_code_locally(code, datasets, normalized_requirements)
            try:
                refined_plan = self._planner.augment_with_execution(question, context_text, active_plan, execution_result)
                active_plan = refined_plan
            except Exception as exc:  # pragma: no cover - defensive path
                log.debug('Planner refinement failed: %s', exc)
            requirements = active_plan.requirements or requirements
            normalized_requirements = self._normalize_requirements(requirements)

        if active_plan.analysis_steps:
            analysis_steps = [dict(step) for step in active_plan.analysis_steps]
        else:
            base_code = code if execution_result else ""
            analysis_steps = self._build_analysis_steps(active_plan.summary, base_code, normalized_requirements)

        if execution_result:
            analysis_steps = self._merge_execution_steps(analysis_steps, code, normalized_requirements, execution_result)

        metadata: Dict[str, Any] = {
            "datasets_used": datasets,
            "summary": active_plan.summary,
            "analysis_steps": analysis_steps,
            "plots": active_plan.plots,
            "files": active_plan.files,
            "examples_used": bool(self._example_snippets),
            "planner": "dspy",
            "completion_state": self._determine_completion_state(active_plan, execution_result),
            "raw_answer": active_plan.raw_answer,
            "requirements": normalized_requirements,
            "is_complete": execution_result.get("success") if execution_result is not None else active_plan.is_complete,
        }

        if execution_result:
            metadata["execution"] = execution_result
            metadata["executed_task"] = {
                "code": self._normalize_code(code),
                "requirements": normalized_requirements,
            }
            metadata["stdout"] = execution_result.get("stdout", "")
            metadata["stderr"] = execution_result.get("stderr", "")
            metadata["artifacts"] = execution_result.get("artifacts", [])

        suggestions: List[ActionSuggestion] = []
        if execution_result and not execution_result.get("success", False):
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.REFINE_QUERY,
                    description="Adjust the request and try a different analysis approach.",
                    parameters={},
                    confidence="medium",
                    priority=1,
                )
            )

        base_priority = len(suggestions) + 1
        for index, follow in enumerate(active_plan.follow_up):
            suggestions.append(
                ActionSuggestion(
                    action_type=ActionType.REFINE_QUERY,
                    description=follow,
                    parameters={},
                    confidence="medium",
                    priority=base_priority + index,
                )
            )

        if execution_result and execution_result.get("success"):
            content = self._build_content_from_execution(active_plan.summary, execution_result)
            confidence = "high"
        elif execution_result:
            content = self._build_content_from_execution(active_plan.summary, execution_result)
            confidence = "low"
        else:
            content = active_plan.summary or next(
                (step["content"] for step in analysis_steps if step.get("type") == "thought"),
                "Generated analysis plan.",
            )
            confidence = "high" if active_plan.is_complete else "medium"

        self._record_debug_steps(active_plan, analysis_steps, execution_result)

        return AgentResponse(
            content=content,
            confidence=confidence,
            agent_type=self.agent_type,
            suggestions=suggestions,
            metadata=metadata,
        )

    def _determine_completion_state(self, plan: DSPyPlanResult, execution_result: Optional[Dict[str, Any]]) -> str:
        if execution_result is not None:
            return "complete" if execution_result.get("success") else "error"
        return "complete" if plan.is_complete else "pending"

    def _execute_generated_code_locally(
        self, code: str, dataset_ids: List[str], requirements: List[str]
    ) -> Dict[str, Any]:
        alias_map, dataset_metadata = self._prepare_dataset_aliases(dataset_ids)
        temp_root = Path(tempfile.mkdtemp(prefix="galaxy-data-analysis-"))
        outputs_dir = temp_root / "outputs_dir"
        generated_dir = outputs_dir / "generated_file"
        generated_dir.mkdir(parents=True, exist_ok=True)

        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        success = False
        error_message: Optional[str] = None

        def get_dataset_path(alias: str) -> str:
            key = (alias or "").strip()
            if key in alias_map:
                return alias_map[key]
            raise KeyError(f"Unknown dataset alias: {alias}")

        def load_dataset(alias: str, **read_kwargs):
            try:
                import pandas as pd  # Local import to avoid mandatory dependency at module load time
            except ImportError as exc:  # pragma: no cover - environment-specific
                raise RuntimeError("pandas is required to load datasets in the data analysis agent") from exc

            path = get_dataset_path(alias)
            lower = path.lower()
            if not read_kwargs and (lower.endswith(".tsv") or lower.endswith(".tab")):
                read_kwargs["sep"] = "\t"
            return pd.read_csv(path, **read_kwargs)

        def _alias_aware_open(file, *args, **kwargs):
            candidate = str(file)
            if candidate in alias_map:
                file = alias_map[candidate]
            return py_builtins.open(file, *args, **kwargs)

        builtins_proxy = dict(py_builtins.__dict__)
        builtins_proxy["open"] = _alias_aware_open

        exec_globals: Dict[str, Any] = {
            "__name__": "__galaxy_data_analysis__",
            "inputs": {"dataset_aliases": alias_map},
            "load_dataset": load_dataset,
            "get_dataset_path": get_dataset_path,
            "outputs_dir": outputs_dir,
            "Path": Path,
            '__builtins__': builtins_proxy,
        }

        preexisting_keys = set(exec_globals.keys())

        self._materialize_dataset_entries(temp_root, dataset_metadata, alias_map)
        log.debug('workspace_contents', extra={'temp_root': str(temp_root), 'entries': os.listdir(temp_root)})

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_root)
            with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
                exec(code, exec_globals, exec_globals)  # noqa: S102 - deliberate execution of model-generated code
            success = True
        except Exception:  # pragma: no cover - execution paths depend on generated code
            error_message = traceback.format_exc()
        finally:
            os.chdir(original_cwd)

        stdout_value = stdout_buffer.getvalue().strip()
        stderr_value = stderr_buffer.getvalue().strip()
        if success:
            scalar_summary = self._capture_new_scalars(exec_globals, preexisting_keys)
            if scalar_summary:
                stdout_value = (stdout_value + ('\n' if stdout_value else '') + scalar_summary).strip()
        if error_message:
            stderr_value = (stderr_value + ("\n" if stderr_value else "") + error_message).strip()

        artifacts = self._collect_artifacts(outputs_dir)
        shutil.rmtree(temp_root, ignore_errors=True)

        result = {
            "success": success,
            "stdout": stdout_value,
            "stderr": stderr_value,
            "artifacts": artifacts,
            "dataset_aliases": alias_map,
            "datasets": dataset_metadata,
            "requirements": requirements,
        }
        if error_message:
            result["error"] = error_message.splitlines()[-1]
        return result

    def _prepare_dataset_aliases(self, dataset_ids: List[str]) -> tuple[Dict[str, str], List[Dict[str, Any]]]:
        trans = getattr(self.deps, "trans", None)
        if not dataset_ids or not trans or not getattr(trans, "security", None):
            return {}, []

        alias_map: Dict[str, str] = {}
        log.debug('Preparing dataset aliases', extra={'dataset_ids': dataset_ids})
        metadata: List[Dict[str, Any]] = []
        used_aliases: set[str] = set()

        for index, encoded_id in enumerate(dataset_ids, start=1):
            try:
                decoded_id = trans.security.decode_id(encoded_id)
            except Exception:  # pragma: no cover - dependent on encoded input
                log.warning("Unable to decode dataset id %s", encoded_id)
                continue

            hda = trans.sa_session.get(HistoryDatasetAssociation, decoded_id)  # type: ignore[attr-defined]
            if not hda:
                log.warning("No dataset found for decoded id %s", decoded_id)
                continue

            if self.deps.user and hda.history and hda.history.user_id != self.deps.user.id:
                log.warning("Dataset %s is not accessible to the current user", encoded_id)
                continue

            file_path = self._get_dataset_file_path(hda)
            if not file_path or not os.path.exists(file_path):
                log.warning("Dataset file is not available for %s", encoded_id)
                continue

            alias_candidates = [encoded_id, f"dataset_{index}"]
            if hda.name:
                alias_candidates.append(hda.name)
                alias_candidates.append(self._sanitize_alias(hda.name))

            file_basename = Path(file_path).name
            alias_candidates.append(file_basename)
            alias_candidates.append(self._sanitize_alias(file_basename))

            unique_aliases = []
            for candidate in alias_candidates:
                if not candidate:
                    continue
                alias_str = candidate.strip()
                if not alias_str or alias_str in used_aliases:
                    continue
                used_aliases.add(alias_str)
                unique_aliases.append(alias_str)
                alias_map[alias_str] = file_path

            alias_map[file_basename] = file_path
            alias_map[self._sanitize_alias(file_basename)] = file_path
            log.debug('prepared_dataset_entry', extra={'id': encoded_id, 'aliases': unique_aliases, 'path': file_path})
            metadata.append(
                {
                    "id": encoded_id,
                    "name": hda.name or f"dataset_{index}",
                    "path": file_path,
                    "size": os.path.getsize(file_path) if os.path.exists(file_path) else None,
                    "aliases": unique_aliases,
                }
            )

        return alias_map, metadata

    def _get_dataset_file_path(self, hda: HistoryDatasetAssociation) -> Optional[str]:
        dataset = getattr(hda, "dataset", None)
        candidates = [getattr(hda, "file_name", None)]
        if dataset is not None:
            candidates.append(getattr(dataset, "file_name", None))
            if hasattr(dataset, "get_file_name"):
                try:
                    candidates.append(dataset.get_file_name())
                except Exception:  # pragma: no cover - backend-specific
                    pass

        for candidate in candidates:
            if candidate and os.path.exists(candidate):
                return candidate

        app = getattr(getattr(self.deps, "trans", None), "app", None)
        if app and getattr(app, "object_store", None) and dataset is not None:
            try:
                filename = app.object_store.get_filename(dataset)
                if filename and os.path.exists(filename):
                    return filename
            except Exception:  # pragma: no cover - backend-specific
                pass

        return candidates[0] if candidates else None

    def _merge_execution_steps(
        self,
        analysis_steps: List[Dict[str, Any]],
        code: str,
        requirements: List[str],
        execution_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        action_added = False
        normalized_code = (code or "").strip()

        for step in analysis_steps:
            step_copy = dict(step)
            if step_copy.get("type") == "action" and not action_added:
                if normalized_code:
                    step_copy["content"] = normalized_code
                step_copy["requirements"] = requirements
                step_copy["status"] = "completed" if execution_result.get("success") else "error"
                steps.append(step_copy)
                steps.append(self._build_observation_step(execution_result))
                action_added = True
            else:
                steps.append(step_copy)

        if not action_added and normalized_code:
            steps.append(
                {
                    "type": "action",
                    "content": normalized_code,
                    "requirements": requirements,
                    "status": "completed" if execution_result.get("success") else "error",
                }
            )
            steps.append(self._build_observation_step(execution_result))

        return steps

    def _build_observation_step(self, execution_result: Dict[str, Any]) -> Dict[str, Any]:
        stdout_value = execution_result.get("stdout", "")
        stderr_value = execution_result.get("stderr", "")
        success = execution_result.get("success", False)
        parts: List[str] = []
        if stdout_value:
            parts.append(f"stdout:\n{self._truncate_output(stdout_value)}")
        if stderr_value:
            parts.append(f"stderr:\n{self._truncate_output(stderr_value)}")
        if not parts:
            parts.append("Execution completed successfully." if success else "Execution failed.")

        return {
            "type": "observation",
            "content": "\n\n".join(parts),
            "stdout": stdout_value,
            "stderr": stderr_value,
            "success": success,
            "status": "completed" if success else "error",
        }

    def _build_content_from_execution(
        self, summary: Optional[str], execution_result: Dict[str, Any]
    ) -> str:
        segments: List[str] = []
        summary_text = (summary or "").strip()
        if summary_text:
            segments.append(summary_text)

        stdout_value = execution_result.get("stdout", "").strip()
        stderr_value = execution_result.get("stderr", "").strip()

        if stdout_value:
            segments.append(f"STDOUT:\n{self._truncate_output(stdout_value)}")
        if stderr_value and not execution_result.get("success", False):
            segments.append(f"STDERR:\n{self._truncate_output(stderr_value)}")

        if not segments:
            segments.append("Generated analysis code executed successfully." if execution_result.get("success") else "Generated analysis code failed.")

        return "\n\n".join(segments).strip()

    def _materialize_dataset_entries(
        self,
        workspace: Path,
        dataset_metadata: List[Dict[str, Any]],
        alias_map: Dict[str, str],
    ) -> None:
        """Expose dataset files inside the temporary workspace for direct file-based access."""

        for entry in dataset_metadata:
            source_path = Path(entry.get("path") or "")
            if not source_path.exists():
                continue

            candidate_names = set()
            entry_name = entry.get("name")
            if entry_name:
                candidate_names.add(Path(str(entry_name)).name)
            candidate_names.add(source_path.name)
            for alias in entry.get("aliases") or []:
                alias_name = Path(str(alias)).name
                if "." in alias_name or alias_name == source_path.name:
                    candidate_names.add(alias_name)

            for candidate in candidate_names:
                if not candidate:
                    continue
                destination = workspace / candidate
                if destination.exists():
                    continue
                try:
                    os.symlink(source_path, destination)
                except OSError:
                    try:
                        shutil.copy(source_path, destination)
                    except Exception as exc:  # pragma: no cover - fallback logging only
                        log.debug("Unable to materialize dataset alias %s -> %s: %s", candidate, source_path, exc)

        for alias, source in alias_map.items():
            source_path = Path(source)
            if not source_path.exists():
                continue
            destination = workspace / Path(str(alias)).name
            if destination.exists():
                continue
            try:
                os.symlink(source_path, destination)
            except OSError:
                try:
                    shutil.copy(source_path, destination)
                except Exception as exc:  # pragma: no cover - fallback logging only
                    log.debug('Unable to materialize alias_map entry %s -> %s: %s', alias, source_path, exc)

    def _capture_new_scalars(self, exec_globals: Dict[str, Any], preexisting: set[str]) -> str:
        lines: List[str] = []
        for key in sorted(exec_globals.keys()):
            if key in preexisting or key.startswith('__'):
                continue
            value = exec_globals[key]
            if callable(value):
                continue
            if isinstance(value, (int, float, str, bool)):
                lines.append(f"{key} = {value!r}")
            elif isinstance(value, (list, tuple)) and len(value) <= 10:
                lines.append(f"{key} = {value!r}")
            elif isinstance(value, dict) and len(value) <= 10:
                try:
                    preview = {k: value[k] for k in list(value)[:5]}
                    lines.append(f"{key} = {preview!r}")
                except Exception:
                    continue
        return '\n'.join(lines)

    def _collect_artifacts(self, outputs_dir: Path) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        if not outputs_dir.exists():
            return artifacts

        for file in sorted(outputs_dir.rglob('*')):
            if not file.is_file():
                continue
            relative_name = str(file.relative_to(outputs_dir))
            size = file.stat().st_size
            mime_type, _ = mimetypes.guess_type(relative_name)
            artifact: Dict[str, Any] = {
                "name": relative_name,
                "size": size,
                "mime_type": mime_type or "application/octet-stream",
            }
            if size <= 512 * 1024:
                with file.open('rb') as handle:
                    artifact["content_base64"] = base64.b64encode(handle.read()).decode('ascii')
            artifacts.append(artifact)
        return artifacts

    def _sanitize_alias(self, value: str) -> str:
        if not value:
            return ""
        sanitized = re.sub(r"[^0-9A-Za-z_]+", "_", value.strip())
        sanitized = re.sub(r"_{2,}", "_", sanitized).strip('_')
        if sanitized and not sanitized[0].isalpha():
            sanitized = f"dataset_{sanitized}"
        return sanitized

    def _truncate_output(self, value: str, limit: int = 1200) -> str:
        text = (value or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "\n..."

    def _should_enqueue_execution(
        self,
        code: str,
        requirements: List[str],
        last_executed_task: Optional[Dict[str, Any]],
    ) -> bool:
        if not code.strip():
            return False
        if not last_executed_task:
            return True
        return not self._tasks_equivalent(code, requirements, last_executed_task)

    def _tasks_equivalent(
        self,
        code: str,
        requirements: List[str],
        last_task: Dict[str, Any],
    ) -> bool:
        normalized_code = self._normalize_code(code)
        last_code = last_task.get("code")
        if normalized_code != last_code:
            return False
        normalized_requirements = self._normalize_requirements(requirements)
        last_requirements = last_task.get("requirements", [])
        return normalized_requirements == last_requirements

    def _extract_last_executed_task(
        self, execution_messages: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        for entry in reversed(execution_messages):
            metadata = entry.get("metadata") or {}
            task = metadata.get("executed_task") or metadata.get("pyodide_task")
            if task and task.get("code"):
                return {
                    "code": self._normalize_code(str(task.get("code"))),
                    "requirements": self._normalize_requirements(task.get("requirements") or []),
                }
        return None

    def _normalize_code(self, code: str) -> str:
        return "\n".join(line.rstrip() for line in (code or "").strip().splitlines())

    def _record_debug_steps(
        self,
        plan: DSPyPlanResult,
        analysis_steps: List[Dict[str, Any]],
        execution_result: Optional[Dict[str, Any]],
    ) -> None:
        """Persist the most recent planning steps for temporary debugging."""

        timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        payload = {
            "timestamp": timestamp,
            "query": getattr(self, "_last_query", ""),
            "context_text": getattr(self, "_last_context_text", ""),
            "summary": plan.summary,
            "python_code": plan.python_code,
            "analysis_steps": analysis_steps,
            "execution_result": execution_result,
            "raw_answer": plan.raw_answer,
        }

        try:
            with self.DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except Exception as exc:  # pragma: no cover - best effort logging
            log.debug("Unable to write debug steps log: %s", exc)

    def _normalize_requirements(self, requirements: List[Any]) -> List[str]:
        return sorted({str(req).strip() for req in requirements if str(req).strip()})

    def _build_analysis_steps(self, summary: str, code: str, requirements: List[str]) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = []
        summary_clean = (summary or "").strip()
        code_clean = (code or "").strip()

        if summary_clean:
            steps.append(
                {
                    "type": "thought" if code_clean else "conclusion",
                    "content": summary_clean,
                }
            )

        if code_clean:
            steps.append(
                {
                    "type": "action",
                    "content": code_clean,
                    "requirements": requirements,
                }
            )

        return steps


    @staticmethod
    @lru_cache(maxsize=1)
    def _load_example_snippets(path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
                snippets = []
                for item in data[:2]:
                    question = item.get("question")
                    answer = item.get("answer") or item.get("final_answer") or item.get("finalAnswer")
                    if question and answer:
                        snippets.append(
                            f"\n### Example\nQuestion: {question}\nAnswer: {answer}"
                        )
                return "".join(snippets)
        except FileNotFoundError:
            log.debug("Examples file not found at %s", path)
        except Exception as exc:
            log.debug("Failed to load example snippets: %s", exc)
        return ""
