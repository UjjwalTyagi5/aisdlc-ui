"""Running a functional suite in a real browser against the running application.

Each case is a user journey: open a page, type into a field, click a button, check what the
page shows. The browser is VISIBLE by default — a tester watches it work through the cases —
and every step is located the way a person would find it: a button or link by its text, a
field by its label, placeholder or name. `css:<selector>` is accepted where nothing is visible.

Outcomes, per case:
- Passed  — every step ran and every check held
- Failed  — a check did not hold, or a step could not find what it needed (the journey broke)
- Error   — the page could not be reached or the browser failed
A failed step stops that case; a screenshot of the page at that moment is kept as evidence.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urljoin

from agents_orchestrator.testing_agent.suites.reports import ResultRow

Report = Callable[[str], Awaitable[None]]
FIND_TIMEOUT_S = 8
CHECK_TIMEOUT_S = 8
STEP_PAUSE_S = 0.35          # visible runs: slow enough to follow, fast enough to finish


class StepFailure(Exception):
    """A check that did not hold, or an element that was not there."""


class PageError(Exception):
    """The application could not be reached."""


def _xpath_literal(s: str) -> str:
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in s.split("'")) + ")"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def make_driver(headless: bool):
    from selenium import webdriver  # noqa: PLC0415
    from selenium.webdriver.chrome.options import Options  # noqa: PLC0415

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,950")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--no-first-run")
    opts.add_argument("--disable-search-engine-choice-screen")
    try:
        return webdriver.Chrome(options=opts)  # Selenium Manager provides the matching driver
    except Exception as first:  # noqa: BLE001
        try:
            from selenium.webdriver.chrome.service import Service  # noqa: PLC0415
            from webdriver_manager.chrome import ChromeDriverManager  # noqa: PLC0415

            return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        except Exception as second:  # noqa: BLE001
            raise RuntimeError(f"Chrome could not be started for the run: {first}; {second}") from second


class Browser:
    """The step vocabulary over one Selenium driver."""

    def __init__(self, driver, base_url: str):
        self.d = driver
        self.base = base_url.rstrip("/") + "/"

    # ── finding things the way a person would ──
    def _visible(self, elements):
        return [e for e in elements if e.is_displayed()]

    def _wait_for(self, finder, what: str, timeout: float = FIND_TIMEOUT_S):
        end = time.monotonic() + timeout
        while True:
            found = finder()
            if found is not None:
                return found
            if time.monotonic() >= end:
                raise StepFailure(f"could not find {what}")
            time.sleep(0.25)

    def _clickable(self, target: str):
        from selenium.webdriver.common.by import By  # noqa: PLC0415

        if target.startswith("css:"):
            return lambda: next(iter(self._visible(self.d.find_elements(By.CSS_SELECTOR, target[4:].strip()))), None)
        lit = _xpath_literal(_norm(target))
        tr = "translate(normalize-space({x}), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
        exact = (f"//button[{tr.format(x='.')}={lit}] | //a[{tr.format(x='.')}={lit}] | "
                 f"//input[(@type='submit' or @type='button') and {tr.format(x='@value')}={lit}] | "
                 f"//*[@role='button' and {tr.format(x='.')}={lit}]")
        partial = (f"//button[contains({tr.format(x='.')}, {lit})] | //a[contains({tr.format(x='.')}, {lit})] | "
                   f"//input[(@type='submit' or @type='button') and contains({tr.format(x='@value')}, {lit})] | "
                   f"//*[(self::button or self::a) and (contains({tr.format(x='@aria-label')}, {lit}) or contains({tr.format(x='@title')}, {lit}))]")

        def find():
            for xp in (exact, partial):
                els = self._visible(self.d.find_elements(By.XPATH, xp))
                if els:
                    return els[0]
            return None
        return find

    def _field(self, target: str, kinds: str = "input|textarea|select"):
        from selenium.webdriver.common.by import By  # noqa: PLC0415

        if target.startswith("css:"):
            return lambda: next(iter(self._visible(self.d.find_elements(By.CSS_SELECTOR, target[4:].strip()))), None)
        want = _norm(target).rstrip(" *:")
        tags = kinds.split("|")

        def find():
            # 1. a <label> whose text matches → its `for` id, or the control inside it
            for label in self.d.find_elements(By.TAG_NAME, "label"):
                text = _norm(label.text).rstrip(" *:")
                if not text or not (text == want or want in text):
                    continue
                fid = label.get_attribute("for")
                if fid:
                    els = self._visible(self.d.find_elements(By.ID, fid))
                    if els:
                        return els[0]
                inner = self._visible([e for t in tags for e in label.find_elements(By.TAG_NAME, t)])
                if inner:
                    return inner[0]
                # A label beside its control, with no `for` — the common template shape
                # (`<label>Long URL</label><input name="longUrl">`): the next control after it.
                following = self._visible(label.find_elements(
                    By.XPATH, "following::*[" + " or ".join(f"self::{t}" for t in tags) + "][1]"))
                if following:
                    return following[0]
            # 2. placeholder, name, id, aria-label
            for tag in tags:
                for e in self._visible(self.d.find_elements(By.TAG_NAME, tag)):
                    for attr in ("placeholder", "aria-label", "name", "id"):
                        v = _norm(e.get_attribute(attr) or "")
                        # "Long URL" is `name="longUrl"`: compare letters and digits only.
                        if v and (v == want or want in v or _squash(v) == _squash(want)):
                            return e
            return None
        return find

    def _highlight(self, el) -> None:
        try:
            self.d.execute_script("arguments[0].scrollIntoView({block:'center'});"
                                  "arguments[0].style.outline='3px solid #D04A02';", el)
            time.sleep(STEP_PAUSE_S)
            self.d.execute_script("arguments[0].style.outline='';", el)
        except Exception:  # noqa: BLE001 — cosmetic only
            pass

    def body_text(self) -> str:
        from selenium.webdriver.common.by import By  # noqa: PLC0415

        try:
            return self.d.find_element(By.TAG_NAME, "body").text or ""
        except Exception:  # noqa: BLE001
            return ""

    # ── the steps ──
    def run_step(self, step) -> str:
        from selenium.common.exceptions import WebDriverException  # noqa: PLC0415
        from selenium.webdriver.support.ui import Select  # noqa: PLC0415

        a = step.action
        if a == "open":
            url = step.target if step.target.startswith(("http://", "https://")) else urljoin(self.base, (step.target or "/").lstrip("/"))
            try:
                self.d.get(url)
            except WebDriverException as exc:
                raise PageError(f"{url} could not be opened: {str(exc).splitlines()[0]}") from exc
            text = _norm(self.body_text())
            if "this site can" in text and ("reached" in text or "refused" in text):
                raise PageError(f"{url} could not be reached — is the application running?")
            return f"opened {self.d.current_url}"
        if a == "click":
            el = self._wait_for(self._clickable(step.target), f'a button or link "{step.target}"')
            self._highlight(el)
            try:
                el.click()
            except WebDriverException:
                self.d.execute_script("arguments[0].click();", el)
            return "clicked"
        if a == "type":
            el = self._wait_for(self._field(step.target, "input|textarea"), f'a field "{step.target}"')
            self._highlight(el)
            el.clear()
            el.send_keys(step.value)
            return "typed"
        if a == "select":
            el = self._wait_for(self._field(step.target, "select"), f'a list "{step.target}"')
            self._highlight(el)
            sel = Select(el)
            try:
                sel.select_by_visible_text(step.value)
            except Exception:  # noqa: BLE001
                match = next((o for o in sel.options if _norm(step.value) in _norm(o.text)), None)
                if match is None:
                    raise StepFailure(f'"{step.target}" has no option "{step.value}"')
                match.click()
            return "selected"
        if a == "check":
            el = self._wait_for(self._field(step.target, "input"), f'a checkbox "{step.target}"')
            self._highlight(el)
            if not el.is_selected():
                el.click()
            return "ticked"
        if a == "wait":
            try:
                seconds = min(float(step.value or 1), 30.0)
            except ValueError:
                seconds = 1.0
            time.sleep(seconds)
            return f"waited {seconds:g} s"
        if a == "assert_text":
            want = _norm(step.value)
            end = time.monotonic() + CHECK_TIMEOUT_S
            while want not in _norm(self.body_text()):
                if time.monotonic() >= end:
                    raise StepFailure(f'the page does not show "{step.value}"')
                time.sleep(0.3)
            return "shown"
        if a == "assert_not_text":
            time.sleep(1.0)
            if _norm(step.value) in _norm(self.body_text()):
                raise StepFailure(f'the page shows "{step.value}", which it should not')
            return "not shown"
        if a == "assert_url":
            end = time.monotonic() + CHECK_TIMEOUT_S
            while step.value not in self.d.current_url:
                if time.monotonic() >= end:
                    raise StepFailure(f'the address is {self.d.current_url}, which does not contain "{step.value}"')
                time.sleep(0.3)
            return "matched"
        raise StepFailure(f"unknown step {a!r}")


def run_case(browser: Browser, case, screenshots_dir: str) -> tuple[ResultRow, list[list[Any]]]:
    steps_out: list[list[Any]] = []
    started = time.monotonic()
    try:
        browser.d.delete_all_cookies()
    except Exception:  # noqa: BLE001
        pass
    for n, step in enumerate(case.steps, start=1):
        try:
            detail = browser.run_step(step)
            steps_out.append([case.id, n, step.describe(), "Passed", detail])
            time.sleep(STEP_PAUSE_S)
        except (StepFailure, PageError) as exc:
            shot = _screenshot(browser, screenshots_dir, f"{case.id}_step{n}.png")
            steps_out.append([case.id, n, step.describe(), "Failed", str(exc)])
            for m, rest in enumerate(case.steps[n:], start=n + 1):
                steps_out.append([case.id, m, rest.describe(), "Not run", "an earlier step failed"])
            status = "Error" if isinstance(exc, PageError) else "Failed"
            return (ResultRow(case.id, case.title, _subject(case), status, int((time.monotonic() - started) * 1000),
                              f"Step {n} ({step.describe()}): {exc}", shot), steps_out)
        except Exception as exc:  # noqa: BLE001 — the browser itself failed
            shot = _screenshot(browser, screenshots_dir, f"{case.id}_step{n}.png")
            steps_out.append([case.id, n, step.describe(), "Failed", f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"])
            return (ResultRow(case.id, case.title, _subject(case), "Error", int((time.monotonic() - started) * 1000),
                              f"Step {n} ({step.describe()}): the browser failed — {type(exc).__name__}", shot), steps_out)
    return ResultRow(case.id, case.title, _subject(case), "Passed", int((time.monotonic() - started) * 1000), "", ""), steps_out


def _subject(case) -> str:
    opens = [s.target for s in case.steps if s.action == "open"]
    return opens[0] if opens else ""


def _screenshot(browser: Browser, folder: str, name: str) -> str:
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        browser.d.save_screenshot(path)
        return name
    except Exception:  # noqa: BLE001
        return ""


async def execute(cases: list[Any], base_url: str, *, headless: bool, screenshots_dir: str,
                  report: Report) -> tuple[list[ResultRow], list[list[Any]]]:
    """(result rows, step rows for the report's Steps sheet). Raises RuntimeError when the
    browser cannot start or the application cannot be reached at all."""
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.get(base_url)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"The application at {base_url} did not respond ({type(exc).__name__}). Start it, "
                           "check the URL, and run again.") from exc

    await report("Starting Chrome" + (" (headless)" if headless else " — the browser window will open"))
    driver = await asyncio.to_thread(make_driver, headless)
    browser = Browser(driver, base_url)
    rows: list[ResultRow] = []
    steps: list[list[Any]] = []
    try:
        for case in cases:
            await report(f"{case.id}: {case.title}")
            row, case_steps = await asyncio.to_thread(run_case, browser, case, screenshots_dir)
            rows.append(row)
            steps.extend(case_steps)
            await report(f"{case.id}: {row.status}" + (f" — {row.message[:160]}" if row.message else ""))
            if row.status == "Error" and "could not be reached" in row.message:
                for later in cases[cases.index(case) + 1:]:
                    rows.append(ResultRow(later.id, later.title, _subject(later), "Not run", None,
                                          f"Not run: the application could not be reached during {case.id}."))
                break
    finally:
        await asyncio.to_thread(_quit, driver)
    return rows, steps


def _quit(driver) -> None:
    try:
        driver.quit()
    except Exception:  # noqa: BLE001
        pass
