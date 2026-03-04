import string
import time

from sentinel.domain.services.security_service import SecurityService


def _service() -> SecurityService:
    return SecurityService()


# --- Extremely Long Base64 Strings ---


def test_long_base64_string_no_hang():
    svc = _service()
    base64_blob = "sk-" + "A" * 50000
    start = time.monotonic()
    result = svc.analyze(base64_blob)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
    assert result["severity"] is not None


def test_long_aws_key_pattern_no_hang():
    svc = _service()
    aws_blob = "AKIA" + "A" * 50000
    start = time.monotonic()
    result = svc.analyze(aws_blob)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


# --- Repeated Patterns That Could Cause Backtracking ---


def test_repeated_api_key_assignments_no_hang():
    svc = _service()
    code = "\n".join(
        [f'api_key = "value_{i}"' for i in range(5000)]
    )
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
    assert result["severity"] is not None


def test_repeated_password_assignments_no_hang():
    svc = _service()
    code = "\n".join(
        [f'password = "pass_{i}"' for i in range(5000)]
    )
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_repeated_eval_calls_no_hang():
    svc = _service()
    code = "\n".join([f'eval("expr_{i}")' for i in range(5000)])
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_repeated_exec_calls_no_hang():
    svc = _service()
    code = "\n".join([f'exec("code_{i}")' for i in range(5000)])
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_repeated_subprocess_shell_true_no_hang():
    svc = _service()
    code = "\n".join(
        [f'subprocess.run("cmd_{i}", shell=True)' for i in range(5000)]
    )
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_repeated_os_system_calls_no_hang():
    svc = _service()
    code = "\n".join(
        [f'os.system("cmd_{i}")' for i in range(5000)]
    )
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


# --- Random Entropy Blobs ---


def test_random_entropy_blob_no_hang():
    svc = _service()
    blob = (string.ascii_letters + string.digits) * 500
    start = time.monotonic()
    result = svc.analyze(blob)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
    assert result["severity"] is not None


def test_binary_like_blob_no_hang():
    svc = _service()
    blob = "".join(chr(i % 256) for i in range(50000) if chr(i % 256).isprintable())
    start = time.monotonic()
    result = svc.analyze(blob)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_nested_quotes_no_hang():
    svc = _service()
    code = 'password = "' + "a" * 50000 + '"'
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_sql_like_pattern_repeated_no_hang():
    svc = _service()
    code = "\n".join(
        [f'query = "SELECT * FROM table_{i} WHERE id=" + user_input' for i in range(5000)]
    )
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


# --- No Catastrophic Backtracking on Edge Patterns ---


def test_almost_matching_openai_key_no_backtrack():
    svc = _service()
    near_miss = "sk" + "-" * 50000 + "A" * 20
    start = time.monotonic()
    result = svc.analyze(near_miss)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_almost_matching_aws_key_no_backtrack():
    svc = _service()
    near_miss = "AKI" + "A" * 50000
    start = time.monotonic()
    result = svc.analyze(near_miss)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_whitespace_heavy_code_no_hang():
    svc = _service()
    code = " " * 100000 + 'api_key = "test"' + " " * 100000
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
    assert len(result["findings"]) >= 1


def test_newline_heavy_code_no_hang():
    svc = _service()
    code = "\n" * 50000 + 'eval("x")' + "\n" * 50000
    start = time.monotonic()
    result = svc.analyze(code)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1
    assert len(result["findings"]) >= 1
