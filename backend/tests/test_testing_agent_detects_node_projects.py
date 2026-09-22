"""A Node + Express service is a Jest project, and the Testing agent must see it.

THE LIVE FAILURE (16 Sep 2026). The tester picked QuickLink @ feature/116-117-link-management
— the Development agent's own scaffold: Express + EJS, `src/services/*.js`, Jest in
devDependencies — pressed Run Unit tests, and got "The agent could not analyze the
provided codebase. No tests could be generated." The runner registry's Jest runner is
named `react` and detected only a package.json naming react/next or loose .jsx/.tsx
files; the repo had neither, detection fell through to python, and python found no
.py files. Everything after detection already handled plain JavaScript.
"""
from __future__ import annotations

import json

from agents_orchestrator.testing_agent.tools.runners import detect_runner


def _node_project(tmp_path, deps: dict, files: dict):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": "quicklink", "scripts": {"test": "jest"}, "devDependencies": deps,
    }), encoding="utf-8")
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_an_express_service_with_jest_is_detected_as_the_jest_runner(tmp_path):
    work = _node_project(tmp_path, {"jest": "^29"}, {
        "src/services/LinkService.js": "function createLink(url) { return url; }\nmodule.exports = { createLink };\n",
        "src/db.js": "const sqlite = require('sqlite3');\n",
        "tests/link.test.js": "test('x', () => {});\n",
    })
    runner = detect_runner(work)
    assert runner is not None and runner.name == "react" and runner.framework == "jest"
    names = {f.function_name for f in runner.scan_files(work)}
    assert "createLink" in names, "the scanner reads plain .js modules"


def test_a_react_app_is_still_detected(tmp_path):
    work = _node_project(tmp_path, {"react": "^18"}, {"src/App.jsx": "export const App = () => null;\n"})
    assert detect_runner(work).name == "react"


def test_a_python_project_is_still_python(tmp_path):
    (tmp_path / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    assert detect_runner(str(tmp_path)).name == "python"


def test_a_dotnet_project_wins_over_a_stray_package_json(tmp_path):
    (tmp_path / "Api.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert detect_runner(str(tmp_path)).name == "dotnet"
