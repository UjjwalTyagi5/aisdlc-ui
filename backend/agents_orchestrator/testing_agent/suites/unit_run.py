"""Running a unit suite: write one test per case, run them in the repository, account for every case.

THE CASE ID IS THE LINK. Each generated test is named after its case ("UT-001: …" in Jest,
`test_UT_001_…` in pytest), so a result maps back to exactly one row of the approved
workbook. A case with no test is "Not run" and says so — never silently dropped.

A TEST THAT FAILS IS A RESULT, NOT A BUG TO FIX. The only repair round is for test FILES that
did not load (a wrong import path, a syntax error): those are rewritten once with the error
shown. A test that ran and failed is reported as failed, with its message.

Supported: Node.js repositories with Jest (added for the run when the repository does not
have it) and Python repositories with pytest. Anything else is refused with that reason.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from agents_orchestrator.testing_agent.suites.reports import ResultRow

logger = logging.getLogger(__name__)
Report = Callable[[str], Awaitable[None]]

GEN_DIR = "__qa_generated__"
MAX_MODULE_CHARS = 14_000
INSTALL_TIMEOUT = 900
RUN_TIMEOUT = 900
# Not \b: in `test_UT_007_disables` the id sits between underscores, which are word characters.
_ID = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,4})[-_](\d{3,})(?!\d)")


class UnitRunError(RuntimeError):
    """The run could not happen — the message is for the tester."""


def canonical_id(text: str) -> Optional[str]:
    m = _ID.search(text or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", os.path.splitext(path)[0]).strip("_").lower() or "module"


def detect_stack(work_dir: str) -> str:
    if os.path.isfile(os.path.join(work_dir, "package.json")):
        return "node"
    if any(os.path.isfile(os.path.join(work_dir, f)) for f in ("requirements.txt", "pyproject.toml", "setup.py", "Pipfile")):
        return "python"
    raise UnitRunError("Unit runs support Node.js (Jest) and Python (pytest) repositories; this branch has no "
                       "package.json and no Python project file.")


# ── writing the tests ─────────────────────────────────────────────────────────


def _read(work_dir: str, rel: str, limit: int = MAX_MODULE_CHARS) -> str:
    try:
        with open(os.path.join(work_dir, rel), encoding="utf-8", errors="ignore") as fh:
            text = fh.read(limit + 1)
    except OSError:
        return ""
    return text if len(text) <= limit else text[:limit] + "\n… [file continues]"


def _local_imports(work_dir: str, rel: str, source: str) -> list[str]:
    """Repo files the module requires/imports by relative path — shown so mocks match reality."""
    found = []
    base = os.path.dirname(rel)
    for spec in re.findall(r"""(?:require\(\s*|from\s+|import\s+)['"](\.{1,2}/[^'"]+)['"]""", source):
        for ext in ("", ".js", ".ts", ".mjs", ".cjs", "/index.js"):
            cand = os.path.normpath(os.path.join(base, spec + ext)).replace("\\", "/")
            if os.path.isfile(os.path.join(work_dir, cand)):
                found.append(cand)
                break
    return found[:6]


def _is_esm(work_dir: str, source: str) -> bool:
    try:
        with open(os.path.join(work_dir, "package.json"), encoding="utf-8") as fh:
            if json.load(fh).get("type") == "module":
                return True
    except (OSError, ValueError):
        pass
    return bool(re.search(r"^\s*(import\s.+\sfrom\s|export\s)", source, re.MULTILINE)) and "require(" not in source


def test_file_path(stack: str, module: str) -> str:
    return f"{GEN_DIR}/{slug(module)}.test.js" if stack == "node" else f"{GEN_DIR}/test_{slug(module)}.py"


def build_test_prompt(stack: str, module: str, cases: list[Any], work_dir: str) -> str:
    source = _read(work_dir, module)
    deps = "\n\n".join(f"=== {p} ===\n{_read(work_dir, p, 5000)}" for p in _local_imports(work_dir, module, source))
    file_path = test_file_path(stack, module)
    rel_import = os.path.relpath(os.path.join(work_dir, module), os.path.join(work_dir, GEN_DIR)).replace("\\", "/")
    listed = json.dumps([{k: getattr(c, k) for k in ("id", "title", "function", "scenario", "input", "expected")} for c in cases], indent=1)
    if stack == "node":
        esm = _is_esm(work_dir, source)
        rules = f"""Write a Jest test file saved at {file_path}.
- ONE test per case, named EXACTLY "<ID>: <title>" — e.g. test("{cases[0].id}: {cases[0].title}", …).
- Import the module under test with the relative path "{rel_import if rel_import.startswith('.') else './' + rel_import}"
  using {'ES module `import`' if esm else 'CommonJS `require`'}.
- Isolate what the module talks to: mock databases, network calls and the filesystem with jest.mock
  (mock by the same relative path the module uses, resolved from THIS test file). Never start a server
  or open a real database file.
- For an Express route handler or controller, call it with mocked req/res objects
  (res.status, res.json, res.send, res.render, res.redirect as jest.fn() returning res).
- Assert the case's expected result exactly. Do not weaken an assertion to make a test pass.
- Only use functions the module really exports."""
    else:
        rules = f"""Write a pytest test file saved at {file_path}.
- ONE test function per case, named test_<ID with - replaced by _>_<short snake name>, e.g. test_{cases[0].id.replace('-', '_')}_…;
  its docstring is "<ID>: <title>".
- The repository root is on sys.path: import the module under test by its package path.
- Isolate databases, network calls and the filesystem with unittest.mock / monkeypatch.
- Assert the case's expected result exactly. Do not weaken an assertion to make a test pass."""
    return "\n\n".join(p for p in (
        rules,
        "Return ONLY the complete file in one fenced code block.",
        f"TEST CASES:\n{listed}",
        f"MODULE UNDER TEST — {module}:\n{source or '[the file could not be read]'}",
        f"FILES IT IMPORTS:\n{deps}" if deps else "",
    ) if p)


def _code_of(answer: str) -> str:
    m = re.search(r"```[a-zA-Z]*\s*\n(.*?)```", answer or "", re.DOTALL)
    return (m.group(1) if m else answer or "").strip() + "\n"


async def _ask(llm, prompt: str, system: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

    reply = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=prompt)])
    content = getattr(reply, "content", reply)
    if isinstance(content, list):
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content or "")


_SYSTEM = ("You are a senior software engineer in test. You write correct, isolated unit tests that "
           "verify exactly the behaviour a test case specifies, and you never invent APIs the code does not have.")


async def write_tests(stack: str, cases: list[Any], work_dir: str, llm, report: Report) -> dict[str, str]:
    """{test file path: code}, one file per module under test, written concurrently."""
    by_module: dict[str, list[Any]] = {}
    for c in cases:
        by_module.setdefault(c.module, []).append(c)
    os.makedirs(os.path.join(work_dir, GEN_DIR), exist_ok=True)

    async def one(module: str, group: list[Any]) -> tuple[str, str]:
        path = test_file_path(stack, module)
        code = _code_of(await _ask(llm, build_test_prompt(stack, module, group, work_dir), _SYSTEM))
        with open(os.path.join(work_dir, path), "w", encoding="utf-8") as fh:
            fh.write(code)
        await report(f"Wrote {path} ({len(group)} case{'s' if len(group) != 1 else ''})")
        return path, code

    await report(f"Writing tests for {len(cases)} case(s) across {len(by_module)} module(s)")
    return dict(await asyncio.gather(*(one(m, g) for m, g in by_module.items())))


# ── running them ──────────────────────────────────────────────────────────────


@dataclass
class FileOutcome:
    loaded: bool
    message: str = ""


async def _run(cmd: list[str], cwd: str, timeout: int, env: Optional[dict] = None):
    from agents_orchestrator.testing_agent.tools.sandbox.base import get_default_sandbox  # noqa: PLC0415

    return await asyncio.to_thread(get_default_sandbox().run, cmd, cwd, timeout, env)


async def prepare_node(work_dir: str, report: Report) -> tuple[list[str], list[str]]:
    """Install dependencies; returns (jest command prefix, notes)."""
    notes: list[str] = []
    lock = os.path.isfile(os.path.join(work_dir, "package-lock.json"))
    await report("Installing dependencies (npm " + ("ci" if lock else "install") + ")")
    res = await _run(["npm", "ci" if lock else "install", "--no-audit", "--no-fund"], work_dir, INSTALL_TIMEOUT)
    if not res.ok:
        raise UnitRunError(f"npm could not install the dependencies (exit {res.exit_code}): {(res.stderr or res.stdout)[-800:]}")
    blocked = sorted(set(re.findall(r"allow-scripts\s+(\S+?)@\S+\s+\(install:", (res.stdout or "") + (res.stderr or ""))))
    if blocked:
        # npm 11 no longer runs dependencies' install scripts unless approved, so native modules
        # (sqlite3, bcrypt…) are not built. The generated tests mock them; say so either way.
        notes.append("npm did not run install scripts for " + ", ".join(blocked) + " (npm's default); their native "
                     "modules were not built, so tests that load them without a mock cannot pass.")
    if not os.path.isfile(os.path.join(work_dir, "node_modules", "jest", "bin", "jest.js")):
        await report("Jest is not a dependency of this repository — adding it for the run only")
        res = await _run(["npm", "install", "--no-save", "--no-audit", "--no-fund", "jest@29"], work_dir, INSTALL_TIMEOUT)
        if not res.ok:
            raise UnitRunError(f"Jest could not be installed for the run: {(res.stderr or res.stdout)[-600:]}")
        notes.append("Jest was not a dependency of the repository; it was installed for this run only.")
    return ["node", os.path.join("node_modules", "jest", "bin", "jest.js")], notes


async def run_node(work_dir: str, jest: list[str], files: list[str]) -> tuple[dict[str, FileOutcome], dict[str, dict]]:
    """(per file: loaded or not, per case id: {status, duration_ms, message, file})."""
    out_file = os.path.join(work_dir, GEN_DIR, "results.json")
    if os.path.exists(out_file):
        os.remove(out_file)
    cmd = [*jest, "--json", f"--outputFile={out_file}", "--runInBand", "--forceExit", "--testTimeout=20000",
           "--rootDir", work_dir, "--roots", os.path.join(work_dir, GEN_DIR), "--testMatch", "**/*.test.js",
           "--coverage=false", "--watchman=false"]
    esm = any(re.search(r"^\s*import\s", _read(work_dir, f, 4000), re.MULTILINE) for f in files)
    env = {"NODE_OPTIONS": "--experimental-vm-modules"} if esm else None
    res = await _run(cmd, work_dir, RUN_TIMEOUT, env)
    if res.timed_out:
        raise UnitRunError(f"the unit tests did not finish within {RUN_TIMEOUT // 60} minutes")
    try:
        with open(out_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise UnitRunError(f"Jest produced no results ({type(exc).__name__}): {(res.stderr or res.stdout)[-1200:]}") from exc
    files_out: dict[str, FileOutcome] = {}
    cases: dict[str, dict] = {}
    for tr in data.get("testResults") or []:
        rel = os.path.relpath(tr.get("name", ""), work_dir).replace("\\", "/")
        assertions = tr.get("assertionResults") or []
        loaded = bool(assertions) or tr.get("status") == "passed"
        files_out[rel] = FileOutcome(loaded=loaded, message=_strip_ansi(tr.get("message") or "")[:3000])
        for a in assertions:
            cid = canonical_id(a.get("title") or a.get("fullName") or "")
            if not cid:
                continue
            status = {"passed": "Passed", "failed": "Failed"}.get(a.get("status"), "Not run")
            cases[cid] = {"status": status, "duration_ms": a.get("duration"), "file": rel,
                          "message": _strip_ansi("\n".join(a.get("failureMessages") or []))[:3000]}
    return files_out, cases


async def prepare_python(work_dir: str, report: Report) -> tuple[list[str], list[str]]:
    venv = os.path.join(work_dir, ".qa_venv")
    await report("Creating a virtual environment and installing dependencies")
    res = await _run([sys.executable, "-m", "venv", venv], work_dir, 300)
    if not res.ok:
        raise UnitRunError(f"a virtual environment could not be created: {res.stderr[-600:]}")
    py = os.path.join(venv, "Scripts" if os.name == "nt" else "bin", "python")
    installs = [[py, "-m", "pip", "install", "-q", "pytest"]]
    if os.path.isfile(os.path.join(work_dir, "requirements.txt")):
        installs.insert(0, [py, "-m", "pip", "install", "-q", "-r", "requirements.txt"])
    elif os.path.isfile(os.path.join(work_dir, "pyproject.toml")):
        installs.insert(0, [py, "-m", "pip", "install", "-q", "."])
    for cmd in installs:
        res = await _run(cmd, work_dir, INSTALL_TIMEOUT)
        if not res.ok:
            raise UnitRunError(f"pip could not install the dependencies: {(res.stderr or res.stdout)[-800:]}")
    return [py, "-m", "pytest"], []


async def run_python(work_dir: str, pytest: list[str], files: list[str]) -> tuple[dict[str, FileOutcome], dict[str, dict]]:
    xml_path = os.path.join(work_dir, GEN_DIR, "results.xml")
    if os.path.exists(xml_path):
        os.remove(xml_path)
    res = await _run([*pytest, GEN_DIR, f"--junitxml={xml_path}", "-p", "no:cacheprovider", "-q"], work_dir, RUN_TIMEOUT,
                     {"PYTHONPATH": work_dir})
    if res.timed_out:
        raise UnitRunError(f"the unit tests did not finish within {RUN_TIMEOUT // 60} minutes")
    try:
        root = ET.parse(xml_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise UnitRunError(f"pytest produced no results: {(res.stdout or res.stderr)[-1200:]}") from exc
    files_out: dict[str, FileOutcome] = {f: FileOutcome(loaded=True) for f in files}
    cases: dict[str, dict] = {}
    for tc in root.iter("testcase"):
        name, classname = tc.get("name", ""), tc.get("classname", "")
        failure = tc.find("failure")
        error = tc.find("error")
        if name in ("", None) or (error is not None and "collection" in (error.get("message") or "").lower()):
            continue
        cid = canonical_id(name)
        module_file = f"{GEN_DIR}/{classname.split('.')[-1]}.py" if classname else ""
        if not cid:
            continue
        if failure is not None:
            status, msg = "Failed", (failure.get("message") or "") + "\n" + (failure.text or "")
        elif error is not None:
            status, msg = "Error", (error.get("message") or "") + "\n" + (error.text or "")
        elif tc.find("skipped") is not None:
            status, msg = "Not run", tc.find("skipped").get("message") or "skipped"
        else:
            status, msg = "Passed", ""
        cases[cid] = {"status": status, "duration_ms": int(float(tc.get("time") or 0) * 1000), "file": module_file, "message": msg.strip()[:3000]}
    # A file that failed to import produces an <error> on a collector testcase with no case id.
    for tc in root.iter("testcase"):
        if canonical_id(tc.get("name", "")) is None and tc.find("error") is not None:
            f = (tc.get("classname") or "").replace(".", "/") + ".py"
            files_out[f] = FileOutcome(loaded=False, message=(tc.find("error").text or "")[:3000])
    return files_out, cases


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


# ── the whole run ─────────────────────────────────────────────────────────────


async def execute(cases: list[Any], work_dir: str, llm, report: Report) -> tuple[list[ResultRow], dict[str, str], str, list[str]]:
    """(result rows in case order, generated files {path: code}, runner description, notes)."""
    stack = detect_stack(work_dir)
    if stack == "node":
        prefix, notes = await prepare_node(work_dir, report)
        runner, run = "Jest (Node.js)", run_node
    else:
        prefix, notes = await prepare_python(work_dir, report)
        runner, run = "pytest (Python)", run_python

    files = await write_tests(stack, cases, work_dir, llm, report)
    await report("Running the unit tests")
    outcomes, results = await run(work_dir, prefix, list(files))

    broken = [f for f in files if f in outcomes and not outcomes[f].loaded]
    if broken:
        await report(f"{len(broken)} test file(s) did not load — rewriting them once with the error")
        by_file = {test_file_path(stack, c.module): [] for c in cases}
        for c in cases:
            by_file[test_file_path(stack, c.module)].append(c)

        async def fix(path: str) -> None:
            prompt = (build_test_prompt(stack, by_file[path][0].module, by_file[path], work_dir)
                      + f"\n\nYOUR PREVIOUS FILE DID NOT LOAD. The runner said:\n{outcomes[path].message[:2500]}\n\n"
                        f"PREVIOUS FILE:\n{files[path][:12000]}\n\nFix the cause (import paths, mocks, syntax) and "
                        "return the complete file. Do not change what the tests assert.")
            code = _code_of(await _ask(llm, prompt, _SYSTEM))
            with open(os.path.join(work_dir, path), "w", encoding="utf-8") as fh:
                fh.write(code)
            files[path] = code

        await asyncio.gather(*(fix(p) for p in broken))
        await report("Running the unit tests again")
        outcomes, results = await run(work_dir, prefix, list(files))

    rows: list[ResultRow] = []
    for c in cases:
        path = test_file_path(stack, c.module)
        subject = f"{c.module}" + (f" · {c.function}" if c.function else "")
        got = results.get(c.id)
        if got:
            rows.append(ResultRow(c.id, c.title, subject, got["status"], got.get("duration_ms"), got.get("message", ""), got.get("file") or path))
        elif path in outcomes and not outcomes[path].loaded:
            rows.append(ResultRow(c.id, c.title, subject, "Error", None,
                                  "The test file for this module could not be loaded: " + outcomes[path].message[:1500], path))
        else:
            rows.append(ResultRow(c.id, c.title, subject, "Not run", None, "No test for this case ran — the generated file has no test named with its ID.", path))
    return rows, files, runner, notes
