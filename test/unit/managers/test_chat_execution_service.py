import json
from types import SimpleNamespace

from galaxy.managers.chat_execution import ChatExecutionService


def _message(payload):
    return SimpleNamespace(message=json.dumps(payload))


def test_find_existing_task_response_returns_existing_followup_payload():
    service = ChatExecutionService(chat_manager=None, agent_service=None)
    exchange = SimpleNamespace(
        messages=[
            _message({"response": "initial", "agent_response": {"metadata": {"pyodide_task": {"task_id": "t1"}}}}),
            _message({"role": "execution_result", "task_id": "t1", "metadata": {"selected_dataset_ids": ["ds1"]}}),
            _message(
                {
                    "response": "followup",
                    "agent_response": {"metadata": {"artifacts": [{"name": "plot.png"}]}},
                    "dataset_ids": ["ds1"],
                }
            ),
        ]
    )

    response = service._find_existing_task_response(exchange, "t1")

    assert response == {
        "response": "followup",
        "agent_response": {"metadata": {"artifacts": [{"name": "plot.png"}]}},
        "dataset_ids": ["ds1"],
    }


def test_find_existing_task_response_falls_back_to_execution_metadata_without_followup():
    service = ChatExecutionService(chat_manager=None, agent_service=None)
    exchange = SimpleNamespace(
        messages=[
            _message({"role": "execution_result", "task_id": "t2", "metadata": {"selected_dataset_ids": ["ds2"]}}),
        ]
    )

    response = service._find_existing_task_response(exchange, "t2")

    assert response == {"dataset_ids": ["ds2"]}
