"""Verify that all `from .const import ...` imports match the const.py contract.

Run from the integration folder (custom_components/ternopil_grid):

    python3 -m tools.verify_const_contract

Exit codes:
- 0: OK
- 2: Contract mismatch

Notes
-----
We intentionally do **not** import const.py because it may import Home Assistant
modules which won't be available in a bare Python environment.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _iter_py_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.glob("*.py"):
        if p.name.startswith("__"):
            continue
        files.append(p)
    return sorted(files)


def _extract_const_imports(py_path: Path) -> set[str]:
    """Return names imported via `from .const import ...` in a single file."""
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "const":
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            names.add(alias.name)
    return names


def _const_defined_names(const_path: Path) -> tuple[set[str], set[str]]:
    """Return (defined_names, exported_names) from const.py via AST parsing."""
    tree = ast.parse(const_path.read_text(encoding="utf-8"), filename=str(const_path))

    defined: set[str] = set()
    exported: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            else:
                targets = [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)

            # capture __all__ = ["A", "B", ...]
            if any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                value = node.value if isinstance(node, ast.Assign) else node.value
                if isinstance(value, (ast.List, ast.Tuple)):
                    for elt in value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported.add(elt.value)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)

    return defined, exported


def main() -> int:
    const_path = ROOT / "const.py"
    if not const_path.exists():
        print("ERROR: const.py not found. Run from integration folder.")
        return 2

    defined, exported = _const_defined_names(const_path)

    imported: set[str] = set()
    for py_path in _iter_py_files(ROOT):
        imported |= _extract_const_imports(py_path)

    missing_defs = sorted(n for n in imported if n not in defined)
    missing_exports = sorted(n for n in imported if exported and n not in exported)

    ok = True
    if missing_defs:
        ok = False
        print("\n[FAIL] Names imported from const.py but NOT defined in const.py:")
        for n in missing_defs:
            print(f"  - {n}")

    if exported and missing_exports:
        ok = False
        print("\n[FAIL] Names imported from const.py but NOT listed in const.__all__:")
        for n in missing_exports:
            print(f"  - {n}")

    if ok:
        print("OK: const.py contract matches imports.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
