import pytest

from sentinel.infrastructure.llm.base import LLMProvider


class _DummyProvider(LLMProvider):
    pass


def test_base_provider_methods_raise_not_implemented():
    provider = _DummyProvider()

    with pytest.raises(NotImplementedError):
        provider.generate_pr_audit("code", "summary")
