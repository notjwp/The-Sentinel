import ast
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domain"


def _domain_python_files() -> list[Path]:
    return sorted(path for path in DOMAIN_DIR.rglob("*.py"))


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_domain_has_no_fastapi_or_infrastructure_imports():
    forbidden_prefixes = (
        "fastapi",
        "sentinel.api",
        "sentinel.workers",
        "sentinel.application",
        "logging",
    )

    for path in _domain_python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_prefixes)
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(forbidden_prefixes)


def test_domain_services_have_no_async_functions():
    services_dir = DOMAIN_DIR / "services"
    for path in services_dir.rglob("*.py"):
        tree = _parse(path)
        async_defs = [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
        assert async_defs == []


def test_domain_has_no_print_calls():
    for path in _domain_python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "print"


def test_domain_has_no_logging_imports():
    for path in _domain_python_files():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "logging"
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "") != "logging"
