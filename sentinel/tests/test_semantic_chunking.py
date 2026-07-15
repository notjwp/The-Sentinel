"""M5: SemanticService.chunk_code_units — corpus granularity for duplicate detection."""

from sentinel.domain.services.semantic_service import SemanticService

SAMPLE = '''"""Module docstring."""
import os
import sys

CONSTANT_VALUE = 42


def first_function(a, b):
    return a + b


@decorator_one
@decorator_two
def second_function(x):
    return x * 2


class Widget:
    def method(self):
        return self.value
'''


def test_chunks_split_on_top_level_defs_and_classes():
    chunks = SemanticService.chunk_code_units(SAMPLE)
    assert len(chunks) == 4  # preamble, first_function, decorated second, Widget
    assert chunks[0].startswith('"""Module docstring."""')
    assert chunks[1].startswith("def first_function")
    assert chunks[2].startswith("@decorator_one")
    assert "def second_function" in chunks[2]  # decorators stay with their unit
    assert chunks[3].startswith("class Widget")
    assert "def method" in chunks[3]  # nested defs stay inside their class


def test_tiny_fragments_are_dropped():
    assert SemanticService.chunk_code_units("import os") == []  # under MIN_CHUNK_TOKENS


def test_max_units_cap():
    many = "\n\n".join(
        f"def func_{i}(a, b, c):\n    return a + b + c + {i}" for i in range(60)
    )
    assert len(SemanticService.chunk_code_units(many, max_units=10)) == 10


def test_empty_and_non_string_inputs():
    assert SemanticService.chunk_code_units("") == []
    assert SemanticService.chunk_code_units("   \n  ") == []
    assert SemanticService.chunk_code_units(None) == []  # type: ignore[arg-type]


def test_single_function_file_is_one_chunk():
    code = "def only(a, b):\n    return a * b + a - b"
    assert SemanticService.chunk_code_units(code) == [code]
