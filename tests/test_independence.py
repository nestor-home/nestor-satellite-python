import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nestor_satellite"


def test_the_sdk_never_imports_the_nestor_server() -> None:
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            found += [f"{path.name}: {n}" for n in names if n == "nestor" or n.startswith("nestor.")]
    assert not found, found
