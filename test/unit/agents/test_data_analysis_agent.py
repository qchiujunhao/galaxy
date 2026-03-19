from pathlib import Path
from types import SimpleNamespace

from galaxy.agents.base import GalaxyAgentDependencies
from galaxy.agents.data_analysis import DataAnalysisAgent
from galaxy.agents.dspy_adapter import DSPyPlanResult


class _DummyPlanner:
    def __init__(self, deps):
        self.deps = deps


def test_pyodide_task_metadata_stays_pending(monkeypatch, tmp_path):
    monkeypatch.setattr("galaxy.agents.data_analysis.GalaxyDSPyPlanner", _DummyPlanner)

    deps = GalaxyAgentDependencies(
        trans=SimpleNamespace(),
        user=None,
        config=SimpleNamespace(id_secret=None, pyodide_dataset_token_ttl=600),
    )
    agent = DataAnalysisAgent(deps)
    agent.DEBUG_LOG_PATH = Path(tmp_path) / "data_analysis_debug.log"

    plan = DSPyPlanResult(
        summary="Initial summary",
        python_code="print('hello')",
        requirements=["pandas"],
        follow_up=[],
        plots=[],
        files=[],
        analysis_steps=[],
        is_complete=True,
        raw_answer={},
        trajectory={},
    )

    response = agent._response_from_plan(
        plan=plan,
        question="run an EDA",
        context_text="DATASETS:\n- None",
        datasets=[],
        last_executed_task=None,
        latest_execution_message=None,
    )

    assert response.metadata.pyodide_task is not None
    assert response.metadata.pyodide_status == "pending"
    assert response.metadata.is_complete is False
    assert response.metadata.completion_state == "pending"
