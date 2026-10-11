"""Stdlib-only test runner - the offline test tier for Phase 1.

`pytest` is the intended runner (`make test` uses this file; `pytest` also works), but
the pipeline must stay runnable on a bare Python install, so this runner discovers
``tests/test_*.py``, calls every module-level ``test_*`` function and reports.

Test functions must take no arguments (no fixtures), which keeps them valid under both
runners without a pytest dependency.

Usage::

    python tests/run_tests.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
WORKSPACE = HERE.parent


def load_module(path: pathlib.Path):
    """Import a module from an explicit file path (keeps tests dir off sys.path)."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    # The workspace root makes `tools.*` importable; the tests dir makes the shared
    # `_iso_fixture` helper importable.
    for entry in (str(WORKSPACE), str(HERE)):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    paths = sorted(p for p in HERE.glob("test_*.py"))
    if not paths:
        print("no tests found", file=sys.stderr)
        return 1

    passed = failed = 0
    failures: list[str] = []

    for path in paths:
        try:
            module = load_module(path)
        except Exception:
            failed += 1
            failures.append(f"{path.name} (import)")
            print(f"ERROR importing {path.name}")
            traceback.print_exc()
            continue

        names = [n for n in vars(module) if n.startswith("test_") and callable(getattr(module, n))]
        for name in sorted(names):
            fn = getattr(module, name)
            try:
                fn()
            except Exception:
                failed += 1
                failures.append(f"{path.name}::{name}")
                print(f"FAIL  {path.name}::{name}")
                traceback.print_exc()
            else:
                passed += 1
                print(f"pass  {path.name}::{name}")

    print(f"\n{passed} passed, {failed} failed")
    if failures:
        print("failed:")
        for item in failures:
            print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
