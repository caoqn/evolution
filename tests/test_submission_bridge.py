from types import SimpleNamespace

import pytest

from core.output_contract import output_contract_from_name
from core.submission_bridge import SubmissionBridge


class _Message:
    def __init__(self, content):
        self.content = content


class _Store:
    def __init__(self, content, recipient="planner", sender="answer_agent"):
        self.content = content
        self.recipient = recipient
        self.sender = sender

    def peek(self, recipient, sender=None):
        if recipient != self.recipient or sender != self.sender:
            return []
        return [_Message(self.content)]


@pytest.mark.parametrize(
    "contract_name,prefix,answer",
    [
        ("gaia", "FINAL ANSWER:", "42"),
        ("locobench_solution_summary", "FINAL OUTPUT:", "solution files ready"),
        ("locabench_completion_summary", "FINAL OUTPUT:", "actions completed"),
        ("beyondswe_patch_summary", "FINAL OUTPUT:", "patched and tested"),
    ],
)
def test_native_contract_answeragent_payload_is_extracted(contract_name, prefix, answer):
    contract = output_contract_from_name(contract_name)
    runner = SimpleNamespace(
        output_contract=contract,
        chairman_name="planner",
        message_store=_Store(f"{prefix} {answer}"),
    )
    bridge = SubmissionBridge(runner)

    assert bridge.extract_answer() == answer
    assert bridge.validate(answer) is None
    assert bridge.validate("wrong") is not None
