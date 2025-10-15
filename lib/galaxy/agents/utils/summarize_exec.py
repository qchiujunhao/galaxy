"""Helpers for summarising execution results before feeding them back to LLMs."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict

TAIL_DEFAULT = 2048

def tail(text: str, limit: int = TAIL_DEFAULT) -> str:
    """Return the final ``limit`` characters from ``text``."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[-limit:]

def summarize_table(csv_path: Path, max_rows: int = 5) -> Dict[str, Any]:
    """Produce a lightweight summary of a CSV file."""
    summary: Dict[str, Any] = {
        "path": str(csv_path),
        "row_count": 0,
        "columns": [],
        "preview": [],
    }
    if not csv_path.exists():
        return summary

    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return summary

        summary["columns"] = header
        for idx, row in enumerate(reader, start=1):
            if idx <= max_rows:
                summary["preview"].append(row)
            summary["row_count"] = idx

    return summary

def summarize_result(exec_result: Dict[str, Any], *, tail_limit: int = TAIL_DEFAULT) -> Dict[str, Any]:
    """Summarize stdout/stderr and list artifact information."""
    stdout = exec_result.get("stdout") or ""
    stderr = exec_result.get("stderr") or ""
    artifacts = exec_result.get("artifacts") or []

    summary = {
        "ok": bool(exec_result.get("success", False)),
        "stdout_tail": tail(stdout, tail_limit),
        "stderr_tail": tail(stderr, tail_limit),
        "artifact_count": len(artifacts),
        "stats": exec_result.get("stats") or {},
        "artifacts": [],
    }

    for artifact in artifacts:
        path = artifact.get("temp_path") or artifact.get("path")
        if not path:
            continue
        table_summary = None
        try:
            table_summary = summarize_table(Path(path))
        except Exception:
            table_summary = None
        summary["artifacts"].append(
            {
                "name": artifact.get("name"),
                "path": path,
                "mime_type": artifact.get("mime_type"),
                "size": artifact.get("size"),
                "table": table_summary,
            }
        )

    return summary
