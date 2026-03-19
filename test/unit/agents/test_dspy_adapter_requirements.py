import json
from types import SimpleNamespace

from galaxy.agents.base import GalaxyAgentDependencies
from galaxy.agents.dspy_adapter import (
    build_context_text,
    GalaxyDSPyPlanner,
)
from galaxy.util.pyodide import infer_requirements_from_python


def test_infer_requirements_from_imports_maps_common_packages():
    code = "\n".join(
        [
            "import os",
            "import numpy as np",
            "from sklearn.model_selection import train_test_split",
            "from PIL import Image",
            "import yaml",
        ]
    )
    requirements = infer_requirements_from_python(code)
    # stdlib is ignored; imports are mapped where needed.
    assert "os" not in requirements
    assert "numpy" in requirements
    assert "scikit-learn" in requirements
    assert "pillow" in requirements
    assert "pyyaml" in requirements


def test_infer_requirements_respects_explicit_marker():
    code = "\n".join(
        [
            "# requirements: pandas, matplotlib",
            "import json",
            "import numpy as np",
        ]
    )
    requirements = infer_requirements_from_python(code)
    assert "pandas" in requirements
    assert "matplotlib" in requirements
    # We still union in inferred imports.
    assert "numpy" in requirements
    assert "json" not in requirements


def test_dspy_initial_plan_limits_react_iterations(monkeypatch):
    captured: dict[str, int] = {}

    class _FakeModule:
        def __init__(self, tools, max_iters=5):
            captured["max_iters"] = max_iters

        def __call__(self, question, context):
            return SimpleNamespace(answer=json.dumps({"explanation": "ok", "plots": [], "files": []}), trajectory={})

    monkeypatch.setattr("galaxy.agents.dspy_adapter.GalaxyDataAnalysisModule", _FakeModule)
    monkeypatch.setattr(GalaxyDSPyPlanner, "_configure_lm", lambda self: None)

    deps = GalaxyAgentDependencies(
        trans=SimpleNamespace(),
        user=None,
        config=SimpleNamespace(ai_model=None, ai_api_key=None, ai_api_base_url=None),
    )
    planner = GalaxyDSPyPlanner(deps)

    planner.plan(question="run an EDA", context_text="DATASETS:\n- None")

    assert captured["max_iters"] == GalaxyDSPyPlanner._INITIAL_PLAN_MAX_ITERS


def test_build_context_text_deduplicates_history_and_limits_executions():
    context_text = build_context_text(
        question="run an EDA",
        datasets=[],
        conversation_history=[
            {"role": "user", "content": "run an EDA"},
            {"role": "assistant", "content": "EDA started"},
            {"role": "assistant", "content": "EDA started"},
            {"role": "execution_result", "content": ""},
            {"role": "assistant", "content": "EDA completed"},
        ],
        execution_messages=[
            {"success": True, "stdout": "csv round", "stderr": ""},
            {"success": True, "stdout": "plot round", "stderr": ""},
            {"success": False, "stdout": "", "stderr": "final retry"},
        ],
        examples_snippet="",
    )

    assert context_text.count("assistant: EDA started") == 1
    assert "execution_result:" not in context_text
    assert "csv round" not in context_text
    assert "plot round" in context_text
    assert "final retry" in context_text
