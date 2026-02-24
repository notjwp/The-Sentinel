import pytest

from sentinel.application.risk_engine import RiskEngine
from sentinel.application.report_service import ReportService
from sentinel.application.use_cases.process_pull_request import ProcessPullRequestUseCase


def _use_case() -> ProcessPullRequestUseCase:
    return ProcessPullRequestUseCase(RiskEngine(), ReportService())


def test_use_case_flow_valid_payload():
    result = _use_case().execute({"repo": "test", "pr_number": 5})
    assert result == "PR #5 Risk: LOW"


def test_missing_repo_raises_key_error():
    with pytest.raises(KeyError):
        _use_case().execute({"pr_number": 1})


def test_missing_pr_number_raises_key_error():
    with pytest.raises(KeyError):
        _use_case().execute({"repo": "test"})


def test_pr_number_negative_supported():
    result = _use_case().execute({"repo": "test", "pr_number": -3})
    assert result == "PR #-3 Risk: LOW"


def test_pr_number_zero_supported():
    result = _use_case().execute({"repo": "test", "pr_number": 0})
    assert result == "PR #0 Risk: LOW"


def test_pr_number_extremely_large_supported():
    large_number = 10**18
    result = _use_case().execute({"repo": "test", "pr_number": large_number})
    assert result == f"PR #{large_number} Risk: LOW"


def test_non_string_repo_is_currently_accepted():
    result = _use_case().execute({"repo": 123, "pr_number": 2})
    assert result == "PR #2 Risk: LOW"


def test_non_integer_pr_number_is_handled_deterministically():
    result = _use_case().execute({"repo": "test", "pr_number": "5"})
    assert result == "PR #5 Risk: LOW"


def test_extremely_long_repo_name_is_supported():
    repo = "r" * 10000
    result = _use_case().execute({"repo": repo, "pr_number": 2})
    assert result == "PR #2 Risk: LOW"


def test_empty_payload_raises_key_error():
    with pytest.raises(KeyError):
        _use_case().execute({})


@pytest.mark.parametrize("payload", [None, [], "bad-payload"])
def test_malformed_payload_types_raise_type_error(payload):
    with pytest.raises(TypeError):
        _use_case().execute(payload)


def test_validation_behavior_deterministic_for_invalid_payload():
    payload = {"repo": "test", "pr_number": "3"}
    use_case = _use_case()
    expected = "PR #3 Risk: LOW"
    for _ in range(10):
        assert use_case.execute(payload) == expected