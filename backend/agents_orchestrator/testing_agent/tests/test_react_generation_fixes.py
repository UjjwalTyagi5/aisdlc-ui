"""Four things stood between "jest tests were generated" and "jest tests ran".

Each produced the same misleading headline — "No tests collected", zero coverage —
while the real cause sat in the runner stderr: a missing harness package, a wrong
import path, or the node test environment. Verified end to end against a real
React project (17 generated, 17 passed) once all four were fixed.
"""
import os
import types

from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import (
    _ensure_react_jsdom_docblock,
    _react_import_hint,
)
from agents_orchestrator.testing_agent.tools.runners.react import ReactRunner


def _fn(name, path):
    return types.SimpleNamespace(function_name=name, file_path=path)


# ── jsdom environment ──────────────────────────────────────────────────────
def test_docblock_is_added_so_component_tests_have_a_document():
    """Jest defaults to the node environment; render() then dies on
    'ReferenceError: document is not defined'."""
    out = _ensure_react_jsdom_docblock("import React from 'react';\n")

    assert out.startswith("/**\n * @jest-environment jsdom\n */\n")
    assert "import React" in out


def test_docblock_is_not_added_twice():
    once = _ensure_react_jsdom_docblock("code")
    assert _ensure_react_jsdom_docblock(once) == once


def test_a_model_supplied_environment_is_respected():
    """If the model asked for a specific environment, don't override it."""
    code = "/** @jest-environment node */\nconst x = 1;\n"
    assert _ensure_react_jsdom_docblock(code) == code


# ── import specifiers ──────────────────────────────────────────────────────
def test_import_hint_is_relative_to_the_test_file_not_the_repo_root():
    """The test file is written INTO src/, but code_analysis reports 'src/x.js'.
    Left to infer, the model writes './src/x.js' — one directory too high."""
    analysis = types.SimpleNamespace(functions=[
        _fn("applyDiscount", "src/pricing.js"),
        _fn("shippingCost", "src/pricing.js"),
    ])

    # A repo with no package.json and no babel config is a CommonJS project to jest;
    # the specifiers are `require()` lines. The path rule is the thing under test.
    hint = _react_import_hint(os.path.join("C:", os.sep, "repo"), analysis)

    assert "require('./pricing.js')" in hint
    assert "./src/pricing.js" not in hint
    # Both functions from one file collapse into a single statement (the rule text
    # mentions `require()` once more; count the specifier lines).
    assert hint.count("= require(") == 1
    assert "applyDiscount, shippingCost" in hint


def test_import_hint_reaches_up_out_of_src_for_a_file_elsewhere():
    analysis = types.SimpleNamespace(functions=[_fn("helper", "lib/util.js")])

    hint = _react_import_hint(os.path.join("C:", os.sep, "repo"), analysis)

    assert "require('../lib/util.js')" in hint


def test_import_hint_is_empty_when_there_is_nothing_to_import():
    assert _react_import_hint("/repo", types.SimpleNamespace(functions=[])) == ""
    assert _react_import_hint("/repo", None) == ""


# ── harness dependencies ───────────────────────────────────────────────────
def test_every_package_the_prompt_tells_the_model_to_use_is_installed():
    """skills/unit/SKILL.md and code_gen_prompt name these; a cloned repo has no
    reason to carry them, and the runner installed only jest-junit."""
    deps = set(ReactRunner._HARNESS_DEPS)

    assert "@testing-library/react" in deps
    assert "@testing-library/jest-dom" in deps
    assert "@testing-library/user-event" in deps
    assert "jest-environment-jsdom" in deps
    # Our own reporter — reports/results.xml comes from it.
    assert "jest-junit" in deps


def test_harness_deps_install_in_one_call(monkeypatch, tmp_path):
    """`npm install --no-save` prunes packages installed by an earlier --no-save
    call, so two calls silently removed jest-junit and the run then failed with
    'Could not resolve a module for a custom reporter'."""
    calls = []

    class FakeSandbox:
        def run(self, cmd, cwd=None, timeout_s=None):
            calls.append(cmd)
            return types.SimpleNamespace(ok=True, stdout="", stderr="", exit_code=0)

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    ReactRunner().install(str(tmp_path), FakeSandbox())

    installs = [c for c in calls if "--no-save" in c]
    assert len(installs) == 1, f"expected one --no-save install, got {installs}"
    for pkg in ReactRunner._HARNESS_DEPS:
        assert pkg in installs[0]


def test_present_packages_are_not_reinstalled(monkeypatch, tmp_path):
    calls = []

    class FakeSandbox:
        def run(self, cmd, cwd=None, timeout_s=None):
            calls.append(cmd)
            return types.SimpleNamespace(ok=True, stdout="", stderr="", exit_code=0)

    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    for pkg in ReactRunner._HARNESS_DEPS:
        os.makedirs(tmp_path / "node_modules" / pkg.replace("/", os.sep), exist_ok=True)

    ReactRunner().install(str(tmp_path), FakeSandbox())

    assert not [c for c in calls if "--no-save" in c]


# ── module system ───────────────────────────────────────────────────────────
def test_a_react_repo_keeps_esm_imports_and_a_plain_node_repo_gets_require(tmp_path):
    """The run that looked green: an Express service with no transform got an ESM
    test file, jest failed it with "unexpected token", and the repo's own two tests
    were reported as the result. The hint, the file name and the jsdom docblock all
    follow the repo's module system now."""
    import json as _j

    from agents_orchestrator.testing_agent.Nodes.dispatch_test_types import (
        _node_is_react,
        _node_module_style,
        _output_path_for,
    )

    node = tmp_path / "express"
    node.mkdir()
    (node / "package.json").write_text(_j.dumps({"devDependencies": {"jest": "^29"}}), encoding="utf-8")
    assert _node_module_style(str(node)) == "commonjs"
    assert not _node_is_react(str(node))
    assert _output_path_for(str(node), "unit", "react").endswith("generated_unit.test.js")
    analysis = types.SimpleNamespace(functions=[_fn("createLink", "src/services/LinkService.js")])
    hint = _react_import_hint(str(node), analysis)
    assert "require('./services/LinkService.js')" in hint
    assert "NO babel/ESM transform" in hint and "import" in hint.lower()

    react = tmp_path / "spa"
    react.mkdir()
    (react / "package.json").write_text(_j.dumps({"dependencies": {"react": "^18"}}), encoding="utf-8")
    assert _node_module_style(str(react)) == "esm"
    assert _node_is_react(str(react))
    assert _output_path_for(str(react), "unit", "react").endswith("generated_unit.test.jsx")
    assert "import { createLink } from './services/LinkService.js';" in _react_import_hint(str(react), analysis)

    esm = tmp_path / "esm"
    esm.mkdir()
    (esm / "package.json").write_text(_j.dumps({"type": "module"}), encoding="utf-8")
    assert _node_module_style(str(esm)) == "esm"
