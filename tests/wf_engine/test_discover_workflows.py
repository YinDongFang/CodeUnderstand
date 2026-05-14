"""Tests for ``Engine.discover_workflows`` declarative module discovery."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from wf_engine import engine as engine_module
from wf_engine.engine import Engine


def _make_pkg(tmp_path: Path, pkg_name: str, files: dict[str, str]) -> None:
    """Create a Python package at ``tmp_path/pkg_name`` with the given files."""
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    for name, content in files.items():
        (pkg_dir / name).write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def discover_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Return a callable ``(files: dict) -> Engine`` that points discovery at a
    fresh per-test package on a temp dir."""

    pkg_name = f"wf_test_pkg_{request.node.name}".replace("[", "_").replace("]", "_")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(engine_module, "_WORKFLOW_PACKAGE", pkg_name)

    def _run(files: dict[str, str]) -> Engine:
        _make_pkg(tmp_path, pkg_name, files)
        eng = Engine()
        eng.discover_workflows()
        return eng

    yield _run

    # Drop any imported submodules so subsequent tests get a clean slate.
    for mod_name in list(sys.modules):
        if mod_name == pkg_name or mod_name.startswith(pkg_name + "."):
            del sys.modules[mod_name]


def test_default_key_falls_back_to_file_stem(discover_in):
    eng = discover_in({
        "alpha.py": """
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
    })
    assert [w["key"] for w in eng.list_workflows()] == ["alpha"]


def test_explicit_workflow_key_overrides_stem(discover_in):
    eng = discover_in({
        "alpha.py": """
            WORKFLOW_KEY = "explicit"
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
    })
    assert [w["key"] for w in eng.list_workflows()] == ["explicit"]


def test_module_without_get_nodes_is_skipped(discover_in):
    eng = discover_in({
        "alpha.py": """
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
        "not_a_workflow.py": """
            HELPER_CONSTANT = 1
        """,
    })
    assert [w["key"] for w in eng.list_workflows()] == ["alpha"]


def test_underscore_prefixed_module_is_skipped(discover_in):
    eng = discover_in({
        "alpha.py": """
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
        "_internal.py": """
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
    })
    assert [w["key"] for w in eng.list_workflows()] == ["alpha"]


def test_get_input_schema_is_optional(discover_in):
    eng = discover_in({
        "no_schema.py": """
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
        "with_schema.py": """
            def get_input_schema():
                return {"type": "object", "properties": {"x": {"type": "string"}}}
            def get_nodes():
                return [{"id": "n", "fn": lambda ctx: None}]
        """,
    })
    by_key = {w["key"]: w for w in eng.list_workflows()}
    assert by_key["no_schema"]["input_schema"] is None
    assert by_key["with_schema"]["input_schema"] == {
        "type": "object",
        "properties": {"x": {"type": "string"}},
    }


def test_duplicate_workflow_key_raises(discover_in):
    with pytest.raises(ValueError, match="workflow_key"):
        discover_in({
            "alpha.py": """
                WORKFLOW_KEY = "shared"
                def get_nodes():
                    return [{"id": "n", "fn": lambda ctx: None}]
            """,
            "beta.py": """
                WORKFLOW_KEY = "shared"
                def get_nodes():
                    return [{"id": "n", "fn": lambda ctx: None}]
            """,
        })


def test_import_error_propagates(discover_in):
    with pytest.raises(SyntaxError):
        discover_in({
            "broken.py": """
                this is not valid python (
            """,
        })


def test_node_workdir_and_whitelist_defaults(discover_in):
    eng = discover_in({
        "alpha.py": """
            def get_nodes():
                return [
                    {"id": "with_defaults", "fn": lambda ctx: None},
                    {"id": "with_overrides", "fn": lambda ctx: None,
                     "workdir": "sub", "whitelist": ["out.txt"]},
                ]
        """,
    })
    wf = eng.get_workflow("alpha")
    nodes = {n.id: n for n in wf.nodes}
    assert nodes["with_defaults"].workdir_relative == "."
    assert tuple(nodes["with_defaults"].whitelist_globs) == ()
    assert nodes["with_overrides"].workdir_relative == "sub"
    assert tuple(nodes["with_overrides"].whitelist_globs) == ("out.txt",)


def test_missing_package_returns_zero(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        engine_module, "_WORKFLOW_PACKAGE", "definitely_not_a_real_pkg_xyz",
    )
    eng = Engine()
    assert eng.discover_workflows() == 0
    assert eng.list_workflows() == []
