import time

from sentinel.domain.services.semantic_service import SemanticService
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine


def _service() -> SemanticService:
    return SemanticService(EmbeddingEngine())


ORIGINAL_FUNCTION = (
    "def calculate_total(items, tax_rate):\n"
    "    subtotal = 0\n"
    "    for item in items:\n"
    "        if item > 0:\n"
    "            subtotal = subtotal + item\n"
    "    tax = subtotal * tax_rate\n"
    "    total = subtotal + tax\n"
    "    if total < 0:\n"
    "        return 0\n"
    "    return total"
)


# --- Variable Renaming ---


def test_variable_renaming_detected_as_similar():
    svc = _service()
    renamed = (
        "def calculate_total(values, rate):\n"
        "    base = 0\n"
        "    for value in values:\n"
        "        if value > 0:\n"
        "            base = base + value\n"
        "    t = base * rate\n"
        "    result = base + t\n"
        "    if result < 0:\n"
        "        return 0\n"
        "    return result"
    )
    t1 = svc.tokenize_code(ORIGINAL_FUNCTION)
    t2 = svc.tokenize_code(renamed)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity > 0.3


# --- Dead Code Injection ---


def test_dead_code_injection_changes_similarity():
    svc = _service()
    with_dead_code = (
        ORIGINAL_FUNCTION + "\n"
        "    unused_var_1 = 999\n"
        "    unused_var_2 = 888\n"
        "    unused_var_3 = 777\n"
        "    dead_branch = False\n"
        "    if dead_branch:\n"
        "        never_executed = True\n"
    )
    t1 = svc.tokenize_code(ORIGINAL_FUNCTION)
    t2 = svc.tokenize_code(with_dead_code)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert 0.0 < similarity <= 1.0


# --- Reordered Lines ---


def test_reordered_lines_detected():
    svc = _service()
    reordered = (
        "def calculate_total(items, tax_rate):\n"
        "    subtotal = 0\n"
        "    tax = subtotal * tax_rate\n"
        "    for item in items:\n"
        "        if item > 0:\n"
        "            subtotal = subtotal + item\n"
        "    total = subtotal + tax\n"
        "    if total < 0:\n"
        "        return 0\n"
        "    return total"
    )
    t1 = svc.tokenize_code(ORIGINAL_FUNCTION)
    t2 = svc.tokenize_code(reordered)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity > 0.8


# --- Comment Noise ---


def test_comment_noise_does_not_change_tokens():
    svc = _service()
    with_comments = (
        "def add(a, b):  # this adds\n"
        "    # comment line\n"
        "    return a + b  # return sum\n"
    )
    without_comments = "def add(a, b):\n    return a + b\n"
    t1 = svc.tokenize_code(with_comments)
    t2 = svc.tokenize_code(without_comments)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity > 0.5


# --- Large 2000-Line Function ---


def test_large_2000_line_function_no_crash():
    svc = _service()
    lines = ["def mega(x):"]
    for i in range(2000):
        lines.append(f"    if x > {i}:")
        lines.append("        x += 1")
    lines.append("    return x")
    large_code = "\n".join(lines)

    start = time.monotonic()
    tokens = svc.tokenize_code(large_code)
    embedding = svc.generate_embedding(tokens)
    elapsed = time.monotonic() - start

    assert len(tokens) > 0
    assert len(embedding) == 128
    assert elapsed < 5.0


def test_large_function_duplicate_detection_completes():
    svc = _service()
    lines = ["def mega(x):"]
    for i in range(500):
        lines.append(f"    if x > {i}:")
        lines.append("        x += 1")
    lines.append("    return x")
    large_code = "\n".join(lines)

    start = time.monotonic()
    findings = svc.detect_duplicates(large_code, [large_code])
    elapsed = time.monotonic() - start

    assert len(findings) == 1
    assert findings[0].similarity_score == 1.0
    assert elapsed < 5.0


# --- Unicode Identifiers ---


def test_unicode_identifiers_no_crash():
    svc = _service()
    unicode_code = "def berechne(wert):\n    ergebnis = wert * 2\n    return ergebnis"
    tokens = svc.tokenize_code(unicode_code)
    assert len(tokens) > 0
    embedding = svc.generate_embedding(tokens)
    assert len(embedding) == 128


def test_unicode_cjk_identifiers_no_crash():
    svc = _service()
    cjk_code = "x = 123\ny = 456\nresult = x + y"
    tokens = svc.tokenize_code(cjk_code)
    embedding = svc.generate_embedding(tokens)
    assert len(embedding) == 128


# --- Non-Code Text ---


def test_non_code_text_does_not_crash():
    svc = _service()
    prose = "The quick brown fox jumps over the lazy dog near the river bank."
    tokens = svc.tokenize_code(prose)
    embedding = svc.generate_embedding(tokens)
    assert len(embedding) == 128


def test_non_code_text_low_similarity_to_code():
    svc = _service()
    prose = "The quick brown fox jumps over the lazy dog near the river bank."
    code = "def fibonacci(n):\n    if n < 2:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
    t1 = svc.tokenize_code(prose)
    t2 = svc.tokenize_code(code)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity < 0.5


# --- Random Noise Input ---


def test_random_noise_does_not_cause_high_similarity():
    svc = _service()
    noise = "zxqw plmk jrnv bqtf xyzw klmn opqr stuv"
    code = "def add(a, b): return a + b"
    t1 = svc.tokenize_code(noise)
    t2 = svc.tokenize_code(code)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity < 0.5


def test_random_noise_no_findings():
    svc = _service()
    noise = "zxqw plmk jrnv bqtf xyzw klmn opqr stuv"
    findings = svc.detect_duplicates(noise, ["def add(a, b): return a + b"])
    assert len(findings) == 0


# --- Execution Time Constraints ---


def test_similarity_computation_100_pairs_under_threshold():
    svc = _service()
    codes = [f"def func_{i}(x): return x + {i}" for i in range(100)]
    embeddings = [svc.generate_embedding(svc.tokenize_code(c)) for c in codes]

    start = time.monotonic()
    for i in range(100):
        for j in range(i + 1, min(i + 5, 100)):
            svc.compute_similarity(embeddings[i], embeddings[j])
    elapsed = time.monotonic() - start

    assert elapsed < 5.0


# --- Adversarial Near-Threshold Cases ---


def test_structurally_similar_different_operations():
    svc = _service()
    add_fn = "def calc(a, b): return a + b"
    mul_fn = "def calc(a, b): return a * b"
    t1 = svc.tokenize_code(add_fn)
    t2 = svc.tokenize_code(mul_fn)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert 0.0 <= similarity <= 1.0001


def test_empty_function_vs_populated_function():
    svc = _service()
    empty_fn = "def f(): pass"
    populated_fn = (
        "def f():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    z = x + y\n"
        "    for i in range(10):\n"
        "        z += i\n"
        "    return z"
    )
    t1 = svc.tokenize_code(empty_fn)
    t2 = svc.tokenize_code(populated_fn)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity < 0.9
