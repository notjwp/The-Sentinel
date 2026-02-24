from sentinel.domain.services.security_service import SecurityService
from sentinel.domain.value_objects.severity_level import SeverityLevel


def test_openai_key_detected_as_high():
    code = 'token = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.HIGH


def test_aws_key_detected_as_high():
    code = 'aws = "AKIA1234567890ABCDEF"'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.HIGH


def test_password_assignment_detected_as_high():
    code = 'password = "123"'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.HIGH


def test_api_key_assignment_detected_as_high():
    code = 'api_key = "abc"'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.HIGH


def test_eval_detected_as_medium():
    code = 'x = eval("2+2")'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_exec_detected_as_medium():
    code = 'exec("code")'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_os_system_detected_as_medium():
    code = 'import os\nos.system("ls")'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_subprocess_shell_true_detected_as_medium():
    code = 'import subprocess\nsubprocess.run("ls", shell=True)'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_sql_concat_detected_as_medium():
    code = 'query = "SELECT id FROM users WHERE id=" + user_id'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_clean_function_detected_as_low():
    code = 'def clean(x):\n    return x + 1'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.LOW
    assert result["findings"] == []


def test_extremely_large_code_string_is_deterministic_and_safe():
    code = "\n".join("def f(): return 1" for _ in range(10000))
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.LOW


def test_keyword_inside_string_literal_is_detected_deterministically():
    code = 'def f():\n    return "eval("'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.MEDIUM


def test_repeated_secret_pattern_counts_all_matches():
    code = 'password = "a"\npassword = "b"\npassword = "c"'
    result = SecurityService().analyze(code)
    assert result["severity"] == SeverityLevel.HIGH
    assert len(result["findings"]) == 3


def test_determinism_for_100_runs_same_output():
    code = 'api_key = "abc"\nquery = "SELECT id FROM users" + uid\npassword = "x"'
    service = SecurityService()
    first = service.analyze(code)
    for _ in range(99):
        assert service.analyze(code) == first


def test_findings_count_matches_number_of_matches():
    code = '\n'.join(
        [
            'api_key = "abc"',
            'password = "123"',
            'eval("2+2")',
            'exec("code")',
            'os.system("ls")',
            'subprocess.run("ls", shell=True)',
            'query = "SELECT id FROM users" + uid',
        ]
    )
    result = SecurityService().analyze(code)
    assert len(result["findings"]) == 7
