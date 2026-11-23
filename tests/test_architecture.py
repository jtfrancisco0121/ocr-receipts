"""AD-1: dependency direction between modules."""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "resibo"
CORE = [
    "models",
    "schema",
    "prompts",
    "config",
    "pdf_utils",
    "validation",
    "exporters",
    "extractor",
]
FORBIDDEN_FOR_CORE = {"fastapi", "starlette", "anthropic", "uvicorn"}
SHELL_MODULES = {"api", "store", "ratelimit", "sample"}


def imports(module: str) -> set[str]:
    path = PACKAGE / f"{module}.py"
    if not path.exists():
        return set()
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "resibo" and len(parts) > 1:
                found.add(parts[1])
            elif parts[0] == "resibo":
                found.update(alias.name for alias in node.names)
            else:
                found.add(parts[0])
    return found


def test_core_modules_import_no_web_or_sdk_or_shell():
    for module in CORE:
        found = imports(module)
        assert not (found & FORBIDDEN_FOR_CORE), (module, found & FORBIDDEN_FOR_CORE)
        assert not (found & SHELL_MODULES), (module, found & SHELL_MODULES)


def test_only_llm_imports_anthropic():
    for path in PACKAGE.glob("*.py"):
        if path.stem == "llm":
            continue
        assert "anthropic" not in imports(path.stem), path.name


def test_only_config_reads_environment():
    for path in PACKAGE.glob("*.py"):
        if path.stem == "config":
            continue
        assert "os.environ" not in path.read_text(), path.name
        assert "os.getenv" not in path.read_text(), path.name
