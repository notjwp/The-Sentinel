from pathlib import Path
import random

import pytest

from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.value_objects.severity_level import SeverityLevel

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _code_for_decisions(decision_points: int) -> str:
    lines = ["def boundary_fn(x):"]
    for index in range(decision_points):
        lines.append(f"    if x > {index}:")
        lines.append("        x += 1")
    lines.append("    return x")
    return "\n".join(lines)


def _assert_valid_result(result: dict) -> None:
    assert result["complexity"] >= 1
    assert 0 <= result["maintainability"] <= 100
    assert result["severity"] in {SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH}


def test_simple_function_low_risk():
    result = DebtService().evaluate_debt(_load_fixture("simple_function.py"))
    assert result["severity"] == SeverityLevel.LOW


def test_medium_complexity_function_medium_risk():
    result = DebtService().evaluate_debt(_load_fixture("medium_function.py"))
    assert result["severity"] == SeverityLevel.MEDIUM


def test_highly_nested_function_high_risk():
    result = DebtService().evaluate_debt(_load_fixture("complex_function.py"))
    assert result["severity"] == SeverityLevel.HIGH


def test_empty_string_input_is_safe():
    result = DebtService().evaluate_debt("")
    _assert_valid_result(result)


def test_whitespace_only_input_is_safe():
    result = DebtService().evaluate_debt("   \n \t\n  ")
    _assert_valid_result(result)


def test_comment_only_input_is_safe():
    result = DebtService().evaluate_debt("# header\n# details\n# summary")
    _assert_valid_result(result)


def test_very_long_code_string_10000_lines_is_bounded_and_safe():
    long_code = "\n".join("if" for _ in range(10000))
    result = DebtService().evaluate_debt(long_code)
    _assert_valid_result(result)
    assert result["severity"] == SeverityLevel.HIGH


def test_pathological_keyword_spam_is_safe():
    spam = ("if if if if " * 5000).strip()
    result = DebtService().evaluate_debt(spam)
    _assert_valid_result(result)
    assert result["complexity"] == 20001


def test_keywords_inside_quotes_are_counted_deterministically():
    code = 'def quoted():\n    return "if elif for while except and or case"'
    result = DebtService().evaluate_debt(code)
    _assert_valid_result(result)
    assert result["complexity"] == 9


def test_null_byte_content_is_safe():
    code = "def nul():\n    text = \"abc\\x00def\"\n    if text:\n        return 1"
    result = DebtService().evaluate_debt(code)
    _assert_valid_result(result)


def test_unicode_heavy_content_is_safe():
    code = "def unicode_payload():\n    value = \"λ🚀漢字✨\"\n    if value:\n        return value"
    result = DebtService().evaluate_debt(code)
    _assert_valid_result(result)


@pytest.mark.parametrize(
    ("decision_points", "expected_complexity", "expected_severity"),
    [
        (6, 7, SeverityLevel.LOW),
        (7, 8, SeverityLevel.MEDIUM),
        (14, 15, SeverityLevel.MEDIUM),
        (15, 16, SeverityLevel.HIGH),
    ],
)
def test_boundary_complexity_thresholds(
    decision_points: int,
    expected_complexity: int,
    expected_severity: SeverityLevel,
):
    code = _code_for_decisions(decision_points)
    result = DebtService().evaluate_debt(code)
    assert result["complexity"] == expected_complexity
    assert result["severity"] == expected_severity
    _assert_valid_result(result)


def test_maintainability_always_clamped_between_zero_and_hundred():
    service = DebtService()
    low_input = service.calculate_maintainability(complexity=1, loc=1)
    high_input = service.calculate_maintainability(complexity=1000, loc=5000)
    assert 0 <= low_input <= 100
    assert 0 <= high_input <= 100


def test_evaluate_debt_deterministic_for_100_runs():
    service = DebtService()
    code = _load_fixture("medium_function.py")
    first = service.evaluate_debt(code)
    for _ in range(99):
        assert service.evaluate_debt(code) == first


def test_fuzz_synthetic_strings_deterministic_no_crash():
    seed = 1337
    rng = random.Random(seed)
    tokens = ["if", "elif", "for", "while", "except", "and", "or", "case", "value", "(", ")", ":"]
    service = DebtService()

    for _ in range(100):
        line_count = rng.randint(1, 30)
        lines = []
        for _ in range(line_count):
            token_count = rng.randint(3, 15)
            line = " ".join(rng.choice(tokens) for _ in range(token_count))
            lines.append(line)
        code = "\n".join(lines)
        result = service.evaluate_debt(code)
        _assert_valid_result(result)
