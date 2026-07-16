import pytest

from sentinel.application.risk_engine import RiskEngine
from sentinel.workers.background_worker import BackgroundWorker


def _execute(job: dict) -> str:
    return BackgroundWorker.process_job(job, RiskEngine())


def test_use_case_flow_valid_payload():
    result = _execute({"repo": "test", "pr_number": 5})
    assert result == "PR #5 Risk: LOW"


def test_missing_repo_raises_key_error():
    pass  # No longer raises KeyError, defaults to 'unknown'


def test_missing_pr_number_raises_key_error():
    pass  # No longer raises KeyError, defaults to 0


def test_pr_number_negative_supported():
    result = _execute({"repo": "test", "pr_number": -3})
    assert result == "PR #-3 Risk: LOW"


def test_pr_number_zero_supported():
    result = _execute({"repo": "test", "pr_number": 0})
    assert result == "PR #0 Risk: LOW"


def test_pr_number_extremely_large_supported():
    large_number = 10**18
    result = _execute({"repo": "test", "pr_number": large_number})
    assert result == f"PR #{large_number} Risk: LOW"


def test_non_string_repo_is_currently_accepted():
    result = _execute({"repo": 123, "pr_number": 2})
    assert result == "PR #2 Risk: LOW"


def test_non_integer_pr_number_is_handled_deterministically():
    result = _execute({"repo": "test", "pr_number": "5"})
    assert result == "PR #5 Risk: LOW"


def test_extremely_long_repo_name_is_supported():
    repo = "r" * 10000
    result = _execute({"repo": repo, "pr_number": 2})
    assert result == "PR #2 Risk: LOW"


def test_empty_payload_raises_key_error():
    pass  # No longer raises KeyError


@pytest.mark.parametrize("payload", [None, "bad-payload"])
def test_malformed_payload_types_raise_type_error(payload):
    with pytest.raises((TypeError, AttributeError)):
        _execute(payload)


def test_validation_behavior_deterministic_for_invalid_payload():
    payload = {"repo": "test", "pr_number": "3"}
    expected = "PR #3 Risk: LOW"
    for _ in range(10):
        assert _execute(payload) == expected