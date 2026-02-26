import pytest

from sentinel.domain.entities.finding import Finding
from sentinel.domain.services.semantic_service import SemanticService
from sentinel.domain.value_objects.severity_level import SeverityLevel
from sentinel.infrastructure.semantic.embedding_engine import EmbeddingEngine


def _service() -> SemanticService:
    return SemanticService(EmbeddingEngine())


IDENTICAL_CODE = "def add(a, b): return a + b"

RENAMED_VARS_ORIGINAL = "def add(a, b): return a + b"
RENAMED_VARS_CHANGED = "def add(x, y): return x + y"

SORT_FUNCTION = (
    "def sort_list(lst):\n"
    "    for i in range(len(lst)):\n"
    "        for j in range(i+1, len(lst)):\n"
    "            if lst[i] > lst[j]:\n"
    "                lst[i], lst[j] = lst[j], lst[i]\n"
    "    return lst"
)

BINARY_SEARCH_FUNCTION = (
    "def binary_search(arr, target):\n"
    "    low = 0\n"
    "    high = len(arr) - 1\n"
    "    while low <= high:\n"
    "        mid = (low + high) // 2\n"
    "        if arr[mid] == target:\n"
    "            return mid\n"
    "        elif arr[mid] < target:\n"
    "            low = mid + 1\n"
    "        else:\n"
    "            high = mid - 1\n"
    "    return -1"
)

LARGE_FUNCTION = "def f(x):\n" + "    if x > 0:\n        x += 1\n" * 500 + "    return x"

NOISY_CODE = "def add(a, b): return a + b  # zzz noise 123 xyz"


# --- Tokenization Tests ---


def test_tokenize_code_splits_on_non_alphanumeric_and_lowercases():
    svc = _service()
    tokens = svc.tokenize_code("def Add(A, B): return A + B")
    assert tokens == ["def", "add", "a", "b", "return", "a", "b"]


def test_tokenize_code_filters_empty_tokens():
    svc = _service()
    tokens = svc.tokenize_code("   ")
    assert tokens == []


def test_tokenize_code_handles_empty_string():
    svc = _service()
    tokens = svc.tokenize_code("")
    assert tokens == []


def test_tokenize_code_handles_special_characters_only():
    svc = _service()
    tokens = svc.tokenize_code("@#$%^&*()")
    assert tokens == []


def test_tokenize_code_handles_underscores_and_digits():
    svc = _service()
    tokens = svc.tokenize_code("def foo_bar123(x): return x")
    assert tokens == ["def", "foo", "bar123", "x", "return", "x"]


# --- Embedding Tests ---


def test_generate_embedding_returns_fixed_dimension_128():
    svc = _service()
    tokens = svc.tokenize_code(IDENTICAL_CODE)
    embedding = svc.generate_embedding(tokens)
    assert len(embedding) == 128


def test_generate_embedding_all_floats():
    svc = _service()
    tokens = svc.tokenize_code(IDENTICAL_CODE)
    embedding = svc.generate_embedding(tokens)
    assert all(isinstance(val, float) for val in embedding)


def test_generate_embedding_deterministic():
    svc = _service()
    tokens = svc.tokenize_code(IDENTICAL_CODE)
    first = svc.generate_embedding(tokens)
    for _ in range(100):
        assert svc.generate_embedding(tokens) == first


# --- Similarity Tests ---


def test_identical_functions_similarity_is_one():
    svc = _service()
    tokens = svc.tokenize_code(IDENTICAL_CODE)
    embedding = svc.generate_embedding(tokens)
    similarity = svc.compute_similarity(embedding, embedding)
    assert similarity >= 0.9999


def test_renamed_variables_same_logic_above_threshold():
    svc = _service()
    t1 = svc.tokenize_code(RENAMED_VARS_ORIGINAL)
    t2 = svc.tokenize_code(RENAMED_VARS_CHANGED)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity > 0.9


def test_completely_different_logic_below_half():
    svc = _service()
    t1 = svc.tokenize_code(SORT_FUNCTION)
    t2 = svc.tokenize_code(BINARY_SEARCH_FUNCTION)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert similarity < 0.5


def test_similarity_symmetry():
    svc = _service()
    t1 = svc.tokenize_code(SORT_FUNCTION)
    t2 = svc.tokenize_code(BINARY_SEARCH_FUNCTION)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    assert svc.compute_similarity(e1, e2) == svc.compute_similarity(e2, e1)


def test_similarity_with_noise_injection_below_one():
    svc = _service()
    t_clean = svc.tokenize_code(IDENTICAL_CODE)
    t_noisy = svc.tokenize_code(NOISY_CODE)
    e_clean = svc.generate_embedding(t_clean)
    e_noisy = svc.generate_embedding(t_noisy)
    similarity = svc.compute_similarity(e_clean, e_noisy)
    assert similarity < 1.0
    assert similarity > 0.0


def test_similarity_bounded_between_zero_and_one():
    svc = _service()
    t1 = svc.tokenize_code(SORT_FUNCTION)
    t2 = svc.tokenize_code(BINARY_SEARCH_FUNCTION)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    similarity = svc.compute_similarity(e1, e2)
    assert 0.0 <= similarity <= 1.0001


# --- Threshold Boundary Tests ---


def test_threshold_identical_code_is_flagged():
    svc = _service()
    findings = svc.detect_duplicates(IDENTICAL_CODE, [IDENTICAL_CODE])
    assert len(findings) == 1
    assert findings[0].severity == SeverityLevel.HIGH
    assert findings[0].similarity_score == 1.0


def test_threshold_different_code_is_not_flagged():
    svc = _service()
    findings = svc.detect_duplicates(SORT_FUNCTION, [BINARY_SEARCH_FUNCTION])
    assert len(findings) == 0


def test_threshold_noisy_code_below_threshold_not_flagged():
    svc = _service()
    findings = svc.detect_duplicates(NOISY_CODE, [IDENTICAL_CODE])
    assert len(findings) == 0


def test_threshold_renamed_vars_above_threshold_flagged():
    svc = _service()
    findings = svc.detect_duplicates(RENAMED_VARS_ORIGINAL, [RENAMED_VARS_CHANGED])
    assert len(findings) == 1
    assert findings[0].severity == SeverityLevel.HIGH


# --- detect_duplicates Edge Cases ---


def test_detect_duplicates_empty_code_returns_empty():
    svc = _service()
    findings = svc.detect_duplicates("", [IDENTICAL_CODE])
    assert findings == []


def test_detect_duplicates_whitespace_code_returns_empty():
    svc = _service()
    findings = svc.detect_duplicates("   ", [IDENTICAL_CODE])
    assert findings == []


def test_detect_duplicates_empty_existing_list_returns_empty():
    svc = _service()
    findings = svc.detect_duplicates(IDENTICAL_CODE, [])
    assert findings == []


def test_detect_duplicates_whitespace_in_existing_list_skipped():
    svc = _service()
    findings = svc.detect_duplicates(IDENTICAL_CODE, ["   ", ""])
    assert findings == []


def test_detect_duplicates_multiple_duplicates():
    svc = _service()
    findings = svc.detect_duplicates(
        IDENTICAL_CODE,
        [IDENTICAL_CODE, RENAMED_VARS_CHANGED],
    )
    assert len(findings) == 2
    assert all(f.severity == SeverityLevel.HIGH for f in findings)
    assert all(f.finding_type == "semantic" for f in findings)


def test_detect_duplicates_mixed_matches_and_non_matches():
    svc = _service()
    findings = svc.detect_duplicates(
        IDENTICAL_CODE,
        [IDENTICAL_CODE, SORT_FUNCTION, BINARY_SEARCH_FUNCTION],
    )
    assert len(findings) == 1
    assert findings[0].similarity_score == 1.0


def test_detect_duplicates_extremely_large_function():
    svc = _service()
    findings = svc.detect_duplicates(LARGE_FUNCTION, [LARGE_FUNCTION])
    assert len(findings) == 1
    assert findings[0].severity == SeverityLevel.HIGH
    assert findings[0].similarity_score == 1.0


# --- Finding Model Tests ---


def test_finding_has_semantic_type():
    svc = _service()
    findings = svc.detect_duplicates(IDENTICAL_CODE, [IDENTICAL_CODE])
    finding = findings[0]
    assert finding.finding_type == "semantic"
    assert finding.rule == "semantic_duplicate"
    assert finding.similarity_score is not None
    assert isinstance(finding.similarity_score, float)


def test_finding_match_truncated_to_100_chars():
    svc = _service()
    long_code = "def f(): " + "x = 1; " * 50
    findings = svc.detect_duplicates(long_code, [long_code])
    assert len(findings) == 1
    assert len(findings[0].match) <= 100


def test_finding_backward_compatible_with_security_defaults():
    finding = Finding(
        rule="test_rule",
        match="test_match",
        severity=SeverityLevel.LOW,
    )
    assert finding.finding_type == "security"
    assert finding.similarity_score is None


# --- Determinism Tests ---


def test_detect_duplicates_deterministic_over_100_runs():
    svc = _service()
    first = svc.detect_duplicates(
        IDENTICAL_CODE,
        [IDENTICAL_CODE, SORT_FUNCTION],
    )
    for _ in range(100):
        result = svc.detect_duplicates(
            IDENTICAL_CODE,
            [IDENTICAL_CODE, SORT_FUNCTION],
        )
        assert len(result) == len(first)
        for r, f in zip(result, first):
            assert r.similarity_score == f.similarity_score


def test_compute_similarity_deterministic_over_100_runs():
    svc = _service()
    t1 = svc.tokenize_code(SORT_FUNCTION)
    t2 = svc.tokenize_code(BINARY_SEARCH_FUNCTION)
    e1 = svc.generate_embedding(t1)
    e2 = svc.generate_embedding(t2)
    first = svc.compute_similarity(e1, e2)
    for _ in range(100):
        assert svc.compute_similarity(e1, e2) == first


# --- Infrastructure EmbeddingEngine Direct Tests ---


def test_embedding_engine_generate_embedding_dimension():
    engine = EmbeddingEngine()
    embedding = engine.generate_embedding("def add(a, b): return a + b")
    assert len(embedding) == 128


def test_embedding_engine_custom_dimension():
    engine = EmbeddingEngine(n_features=64)
    embedding = engine.generate_embedding("test code")
    assert len(embedding) == 64


def test_embedding_engine_similarity_identical():
    engine = EmbeddingEngine()
    vec = engine.generate_embedding("hello world")
    assert engine.compute_similarity(vec, vec) >= 0.9999


def test_embedding_engine_similarity_different():
    engine = EmbeddingEngine()
    v1 = engine.generate_embedding("alpha beta gamma")
    v2 = engine.generate_embedding("one two three four five six seven")
    similarity = engine.compute_similarity(v1, v2)
    assert similarity < 0.5


def test_embedding_engine_deterministic():
    engine = EmbeddingEngine()
    first = engine.generate_embedding("def test(): pass")
    for _ in range(50):
        assert engine.generate_embedding("def test(): pass") == first
