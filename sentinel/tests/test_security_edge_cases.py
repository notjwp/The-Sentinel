import time

from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.value_objects.severity_level import SeverityLevel


def test_empty_input_returns_no_findings():
    result = SecurityService().analyze("")

    assert result["severity"] == SeverityLevel.LOW
    assert result["findings"] == []


def test_very_large_input_is_handled_without_crash():
    code = "\n".join("def f(): return 1" for _ in range(20000))
    result = SecurityService().analyze(code)

    assert result["severity"] == SeverityLevel.LOW
    assert result["findings"] == []


def test_multiple_vulnerabilities_in_one_code_block_are_all_reported():
    code = "\n".join(
        [
            'api_key = "abc"',
            'password = "123"',
            'x = eval("2+2")',
            'query = "SELECT * FROM users WHERE id=" + uid',
        ]
    )
    result = SecurityService().analyze(code)

    rules = {finding.rule for finding in result["findings"]}
    assert {"api_key_assignment", "password_assignment", "eval_call", "naive_sql_concat"}.issubset(
        rules
    )


def test_duplicate_vulnerabilities_are_counted_not_collapsed():
    code = "\n".join(
        [
            'password = "a"',
            'password = "b"',
            'password = "c"',
        ]
    )
    result = SecurityService().analyze(code)

    password_findings = [f for f in result["findings"] if f.rule == "password_assignment"]
    assert len(password_findings) == 3


def test_safe_code_has_no_false_positives():
    code = "\n".join(
        [
            "def add(a, b):",
            "    return a + b",
            "",
            "def greet(name):",
            "    return f'hello {name}'",
        ]
    )
    result = SecurityService().analyze(code)

    assert result["severity"] == SeverityLevel.LOW
    assert result["findings"] == []


def test_large_scan_completes_under_half_second():
    service = SecurityService()
    code = "\n".join(
        [f'query = "SELECT * FROM table_{i} WHERE id=" + user_input' for i in range(2000)]
    )

    start = time.monotonic()
    result = service.analyze(code)
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert result["severity"] in {
        SeverityLevel.LOW,
        SeverityLevel.MEDIUM,
        SeverityLevel.HIGH,
        SeverityLevel.CRITICAL,
    }


def test_same_input_is_deterministic_for_edge_case_payloads():
    code = "\n".join(
        [
            'query = f"SELECT * FROM users WHERE id={uid}" + suffix',
            'password = "x"',
            'x = eval("2+2")',
        ]
    )
    service = SecurityService()
    expected = service.analyze(code)

    for _ in range(20):
        assert service.analyze(code) == expected
