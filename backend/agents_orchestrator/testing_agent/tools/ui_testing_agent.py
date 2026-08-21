# PEP 563: makes `-> ChatLiteLLM` below a lazy string, so the import can be
# deferred to the one place that actually constructs a model.
from __future__ import annotations

from config.env import AGENTIC_BASE_URL, TESTING_AGENT_HEADLESS
# FILE: ui_testing_agent.py

import os
import re
import time
import json
import asyncio
import aiofiles
import logging
from typing import List, Dict, Optional
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from config.ws_helper import set_session_id, broadcast_log, set_user_id, get_session_id, get_user_id
from config.env import LITELLM_BASE_URL
from functools import partial
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import openpyxl
import io
import base64
from config import sdlcSettings

esett = sdlcSettings()


try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# Import broadcasting utilities with fallback
try:
    from config.ws_helper import set_session_id, broadcast_log, get_user_id, get_session_id
    from config.connection_manager import manager
    BROADCASTING_AVAILABLE = True
except ImportError:
    # Fallback implementations if broadcasting modules not available
    BROADCASTING_AVAILABLE = False
    def broadcast_log(manager, message, level="INFO"):
        print(f"[{level}] {message}")
    def get_user_id():
        return "default_user"
    def get_session_id():
        return "default_session"

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ui_testing_agent")

# --- Configuration ---
load_dotenv()

def _build_ui_llm() -> ChatLiteLLM:
    """Build the Selenium agent's LLM from the run's BYOK-resolved model.

    P3.6 (B2): the UI testing agent no longer uses the platform LiteLLM key.
    This MUST be called from the async helper body (not inside an executor
    thread) — `get_resolved_model()` reads the run's contextvar, which the
    testing graph set before entering the graph executor. The built client
    (carrying the resolved key) is then safe to pass into run_in_executor.
    Fails CLOSED when no model has been resolved.
    """
    from shared.services.model_resolver import get_resolved_model

    resolved = get_resolved_model()
    if resolved is None:
        raise RuntimeError(
            "No BYOK model resolved for this UI testing run. An administrator must "
            "configure and verify a model provider in Org Settings → Model Providers."
        )
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    from langchain_litellm import ChatLiteLLM
    return ChatLiteLLM(
        model=resolved.model,
        custom_llm_provider=resolved.litellm_provider,
        api_base=resolved.base_url or LITELLM_BASE_URL,
        api_key=resolved.api_key,
    )



DEFAULT_WAIT_TIME = 5

# --- Broadcasting Helper ---
async def broadcast_log(message: str, level: str = "INFO"):
    """Enhanced broadcast log function"""
    if not BROADCASTING_AVAILABLE:
        print(f"[{level}] {message}")
        return
    
    try:
        await manager.broadcast({
            "type": "log",
            "level": level,
            "message": message,
            "timestamp": time.time()
        })
        logger.log(getattr(logging, level, logging.INFO), message)
    except Exception as e:
        logger.error(f"Failed to broadcast log: {str(e)}")

async def broadcast_file_generated(session_id: str, filename: str, file_path: str):
    """Enhanced function to broadcast file generation with file size"""
    if not BROADCASTING_AVAILABLE:
        print(f"File generated: {filename} at {file_path}")
        return
    
    try:
        user_id = get_user_id()
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        file_url = f"{AGENTIC_BASE_URL}/generated/{user_id}/orchestrator/{session_id}/output/{filename}"
        await manager.broadcast({
            "type": "file_generated",
            "session_id": session_id,
            "filename": filename,
            "url": file_url,
            "file_size": file_size,
            "message": f"Generated file: {filename}"
        })
        
        logger.info(f"Broadcasted file generation: {filename} ({file_size} bytes)")
        
    except Exception as e:
        logger.error(f"Failed to broadcast file generation: {str(e)}")

# --- Utilities ---
async def ensure_dirs():
    """Async directory creation"""
    loop = asyncio.get_running_loop()
    user_id = get_user_id()
    session_id = get_session_id()
    print(user_id,"====================",session_id)
    
    SCREENSHOT_DIR = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/ui_test_screenshots"
    mkdir_func = partial(os.makedirs, SCREENSHOT_DIR, exist_ok=True)
    await loop.run_in_executor(None, mkdir_func)

async def save_screenshot(driver, tc_id):
    """Async screenshot saving"""
    await ensure_dirs()
    user_id = get_user_id()
    session_id = get_session_id()
    print(user_id,"====================",session_id)
    SCREENSHOT_DIR = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/ui_test_screenshots"   
    path = os.path.join(SCREENSHOT_DIR, f"{tc_id}.png")
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, driver.save_screenshot, path)
    except Exception:
        path = ""
    return path

def highlight_element(driver, element, delay=0.9):
    try:
        original_style = driver.execute_script("return arguments[0].getAttribute('style');", element)
        driver.execute_script("arguments[0].setAttribute('style', arguments[1]);",
                              element, "border: 3px solid yellow; background: #ffffe0;")
        time.sleep(delay)
        driver.execute_script("arguments[0].setAttribute('style', arguments[1]);",
                              element, original_style or "")
    except Exception:
        pass

def show_on_screen_message(driver, message, duration_ms=2500):
    script = """
        var element = document.createElement('div');
        element.id = 'ai-agent-message-overlay-' + Date.now();
        element.innerHTML = arguments[0];
        element.style.position = 'fixed';
        element.style.top = '20px';
        element.style.left = '50%';
        element.style.transform = 'translateX(-50%)';
        element.style.padding = '12px 20px';
        element.style.backgroundColor = 'rgba(0, 0, 0, 0.75)';
        element.style.color = 'white';
        element.style.borderRadius = '8px';
        element.style.zIndex = '99999';
        element.style.fontFamily = 'Arial, sans-serif';
        element.style.fontSize = '16px';
        element.style.textAlign = 'center';
        element.style.fontWeight = 'bold';
        element.style.boxShadow = '0 4px 8px rgba(0,0,0,0.3)';
        element.style.transition = 'opacity 0.5s';
        document.body.appendChild(element);
        setTimeout(function() {
            element.style.opacity = '0';
            setTimeout(function() {
                if (element.parentNode) {
                    element.parentNode.removeChild(element);
                }
            }, 500);
        }, arguments[1] - 500);
    """
    try:
        driver.execute_script(script, message, duration_ms)
    except Exception:
        pass

# --- Heuristic Selectors ---
HEURISTIC_SELECTORS = {
    "username": [(By.ID, "username"), (By.ID, "user-name"), (By.ID, "email"), (By.ID, "login"),(By.NAME, "username"), (By.NAME, "email"), (By.NAME, "user"), (By.NAME, "login_id"),(By.CSS_SELECTOR, "input[type='email']"), (By.CSS_SELECTOR, "input[autocomplete='username']"),(By.CSS_SELECTOR, "input[aria-label*='Username'], input[aria-label*='Email']"),(By.CSS_SELECTOR, "input[placeholder*='Username'], input[placeholder*='Email'], input[placeholder*='Login']")],
    "password": [(By.ID, "password"), (By.ID, "pass"), (By.ID, "pwd"),(By.NAME, "password"), (By.NAME, "pass"),(By.CSS_SELECTOR, "input[type='password']"), (By.CSS_SELECTOR, "input[autocomplete='current-password']"),(By.CSS_SELECTOR, "input[aria-label*='Password']"), (By.CSS_SELECTOR, "input[placeholder*='Password']")],
    "login_button": [(By.ID, "login-button"), (By.ID, "signin-button"), (By.CSS_SELECTOR, "button[type='submit']"),(By.CSS_SELECTOR, "input[type='submit']"), (By.CSS_SELECTOR, "[data-testid='login-button']"),(By.NAME, "login"), (By.NAME, "commit"),(By.XPATH, "//button[normalize-space()='Log In' or normalize-space()='Sign In' or normalize-space()='Login']"),(By.XPATH, "//input[@value='Log In' or @value='Sign In' or @value='Login']")],
    "forgot_password": [(By.LINK_TEXT, "Forgotten password?"), (By.PARTIAL_LINK_TEXT, "Forgot"),(By.XPATH, "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'forgot')]")],
    "create_account": [(By.LINK_TEXT, "Create new account"), (By.PARTIAL_LINK_TEXT, "Create"), (By.PARTIAL_LINK_TEXT, "Sign up"),(By.XPATH, "//a[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'sign up')]")],
}
LLM_SELECTOR_CACHE = {}


def _normalize_target_url(url: str) -> str:
    return (url or "").strip().rstrip(".,;:!?)]}'\"")


def _is_form_flow_goal(user_goal: str) -> bool:
    goal = (user_goal or "").lower()
    return any(term in goal for term in ("create", "add", "new", "submit", "register", "apply", "request", "case", "form"))


def _goal_keywords(user_goal: str) -> list[str]:
    stop = {
        "a", "an", "the", "to", "of", "and", "or", "for", "end", "e2e", "test",
        "testing", "functional", "feature", "flow", "do", "new", "create", "add",
    }
    words = []
    for token in re.findall(r"[a-zA-Z0-9]+", user_goal or ""):
        lowered = token.lower()
        if len(lowered) > 2 and lowered not in stop:
            words.append(lowered)
    return words[:8]

# --- LLM Helpers ---
def extract_json_from_text(text):
    try:
        text = text.strip()
        if text.startswith("```json"): text = text[7:]
        elif text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text)
    except Exception:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1: return json.loads(text[start:end+1])
        except Exception: pass
    return None

async def ask_gemini_for_testcases(page_html, url, n=10, user_goal: str = "", planned_cases=None):
    """Generate browser test cases.

    When *planned_cases* is supplied (list of {id, description, goal} dicts
    from the approval step), Claude generates concrete Selenium action steps
    for each of those pre-approved cases using the live page HTML — so the
    cases shown at approval and the cases executed are identical.

    When *planned_cases* is None the legacy single-goal path is used.
    """
    loop = asyncio.get_running_loop()
    # Build the BYOK client in the async body (contextvar is visible here) so the
    # resolved key crosses safely into the executor thread below.
    llm = _build_ui_llm()

    if planned_cases:
        cases_block = "\n".join(
            f'- {c["id"]}: {c["description"]} — Goal: {c.get("goal", c["description"])}'
            for c in planned_cases
        )
        prompt = (
            f"You are a senior QA automation engineer.\n\n"
            f"The following test cases have been approved by the user. "
            f"Generate concrete Selenium action steps for EACH of them based "
            f"on the live page HTML below.\n\n"
            f"Pre-approved test cases:\n{cases_block}\n\n"
            f"Current page URL: {url}\n"
            f"Current page HTML:\n{page_html[:14000]}\n\n"
            "Return a JSON array where each item corresponds to one pre-approved "
            "test case (same id and description) and adds:\n"
            "  \"steps\": array of action objects, each with \"action\", \"target\", \"value\"\n"
            "  \"expected\": the expected outcome string\n\n"
            "Action types: \"click\", \"enter_text\", \"verify_text_present\", "
            "\"complete_requested_form_flow\".\n"
            "Rules:\n"
            "- Preserve the exact \"id\" and \"description\" from the pre-approved list.\n"
            "- Use visible labels, button text, ids, names, placeholders from the HTML.\n"
            "- For form-create flows use \"complete_requested_form_flow\" as the action "
            "  with the goal text as \"value\".\n"
            "- Output ONLY a valid JSON array, no markdown fences, no explanations."
        )
        try:
            await broadcast_log(f"Generating execution steps for {len(planned_cases)} pre-approved test case(s)...")
            resp = await loop.run_in_executor(None, llm.invoke, prompt)
            data = extract_json_from_text(resp.content)
            if isinstance(data, list) and len(data) > 0:
                await broadcast_log(f"Execution steps generated for {len(data)} test case(s).")
                return data
        except Exception as e:
            await broadcast_log(f"Step generation failed: {e}", level="ERROR")

        # Fallback: build minimal executable cases from the planned list
        fallback = []
        for c in planned_cases:
            goal = c.get("goal") or c["description"]
            if _is_form_flow_goal(goal):
                steps = [{"action": "complete_requested_form_flow", "target": goal, "value": goal}]
            else:
                steps = [{"action": "verify_text_present", "target": goal, "value": goal[:80]}]
            fallback.append({
                "id": c["id"],
                "description": c["description"],
                "steps": steps,
                "expected": f"The {c['description']} flow completes successfully.",
            })
        return fallback

    # ── Legacy path: no pre-approved cases ───────────────────────────────────
    requested_flow = (user_goal or "").strip() or "Explore and test the most important visible user flow on this page."
    if _is_form_flow_goal(requested_flow):
        return [{
            "id": "TC_FORM_FLOW_E2E_001",
            "description": "Complete the requested form-based feature flow end to end",
            "steps": [
                {"action": "complete_requested_form_flow", "target": requested_flow, "value": requested_flow}
            ],
            "expected": "The requested form flow should submit successfully or report a concrete blocker."
        }]

    prompt = (
        f"You are a senior QA automation engineer. Generate up to {n} meaningful "
        f"browser UI test cases for the user's requested flow.\n\n"
        f"User requested flow:\n{requested_flow}\n\n"
        f"Current page URL:\n{url}\n\n"
        f"Current page HTML:\n{page_html[:14000]}\n\n"
        "Return strictly a JSON array. Each object must have: \"id\", \"description\", "
        "\"steps\" (array of action objects), and \"expected\". Each action must have: "
        "\"action\", \"target\", and \"value\".\n\n"
        "Rules:\n"
        "- Output ONLY a valid JSON array. No explanations.\n"
        "- Prioritize the requested flow over generic login/smoke checks.\n"
        "- If the requested flow is blocked by login, create a test case that records "
        "the blocker by navigating toward the requested feature.\n"
        "- Use visible labels, button text, ids, names, placeholders from the HTML.\n"
        "- Include negative/validation cases only when part of the requested flow."
    )
    try:
        await broadcast_log("Generating test cases from page HTML...")
        resp = await loop.run_in_executor(None, llm.invoke, prompt)
        data = extract_json_from_text(resp.content)
        if isinstance(data, list) and len(data) > 0:
            await broadcast_log(f"Generated {len(data)} test cases.")
            return data
    except Exception as e:
        await broadcast_log(f"Test case generation failed: {e}", level="ERROR")
    return [{
        "id": "TC_FB_001",
        "description": "Verify requested flow content is reachable",
        "steps": [{"action": "verify_text_present", "target": "requested flow", "value": requested_flow[:80]}],
        "expected": "The page should expose content related to the requested flow.",
    }]

async def ask_gemini_for_locator(page_html, url, intent_text):
    """Async version of Gemini locator generation"""
    cache_key = (url, intent_text)
    if cache_key in LLM_SELECTOR_CACHE: return LLM_SELECTOR_CACHE[cache_key]
    prompt = f"""You are an expert QA automation engineer. Intent: "{intent_text}". Given this HTML, return ONE robust locator. Prefer `data-testid`, `id`, `name`, or a stable CSS selector. Use XPath as a last resort. Return ONLY a compact JSON object like: {{"strategy":"css","locator":"[data-testid='login-button']"}} HTML: {page_html[:12000]} URL: {url}"""
    try:
        await broadcast_log(f"Gemini: asking for locator for intent: '{intent_text}'")
        loop = asyncio.get_running_loop()
        llm = _build_ui_llm()
        resp = await loop.run_in_executor(None, llm.invoke, prompt)
        suggestion = extract_json_from_text(resp.content)
        if isinstance(suggestion, dict) and "strategy" in suggestion and "locator" in suggestion:
            LLM_SELECTOR_CACHE[cache_key] = suggestion
            return suggestion
    except Exception as e: 
        await broadcast_log(f"Gemini locator request failed: {e}", level="ERROR")
    return None

# --- Execution Helpers ---
def find_element_by_heuristics(driver, selectors):
    for by, sel in selectors:
        try:
            el = WebDriverWait(driver, 2).until(EC.presence_of_element_located((by, sel)))
            if el.is_displayed(): return el, f"{by}={sel}"
        except Exception: continue
    return None, None


def _click_first_visible(driver, locators):
    last_error = None
    for by, selector in locators:
        try:
            el = WebDriverWait(driver, DEFAULT_WAIT_TIME).until(
                EC.element_to_be_clickable((by, selector))
            )
            if el.is_displayed():
                highlight_element(driver, el, delay=0.2)
                el.click()
                return True, f"{by}={selector}"
        except Exception as exc:
            last_error = exc
    return False, str(last_error or "not found")


def _select_first_real_option(driver, locators, label):
    last_error = None
    for by, selector in locators:
        try:
            el = WebDriverWait(driver, DEFAULT_WAIT_TIME).until(
                EC.presence_of_element_located((by, selector))
            )
            if not el.is_displayed():
                continue
            select = Select(el)
            for option in select.options:
                value = (option.get_attribute("value") or "").strip()
                text = (option.text or "").strip()
                if value and not text.lower().startswith("select"):
                    highlight_element(driver, el, delay=0.2)
                    select.select_by_value(value)
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                        el,
                    )
                    return True, f"{label}={value}"
        except Exception as exc:
            last_error = exc
    return False, f"{label} selectable option not found ({last_error})"


def _set_date_field(driver):
    locators = [
        (By.NAME, "ServiceDate"),
        (By.ID, "ServiceDate"),
        (By.CSS_SELECTOR, "input[type='date']"),
    ]
    today = time.strftime("%Y-%m-%d")
    last_error = None
    for by, selector in locators:
        try:
            el = WebDriverWait(driver, DEFAULT_WAIT_TIME).until(
                EC.presence_of_element_located((by, selector))
            )
            highlight_element(driver, el, delay=0.2)
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                el,
                today,
            )
            return True, f"ServiceDate={today}"
        except Exception as exc:
            last_error = exc
    return False, f"ServiceDate field not found ({last_error})"


def complete_requested_form_flow(driver, user_goal):
    messages = []
    keywords = _goal_keywords(user_goal)

    if not driver.find_elements(By.CSS_SELECTOR, "form"):
        keyword_predicates = " or ".join([
            f"contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{kw}')"
            for kw in keywords
        ])
        text_predicate = keyword_predicates or "contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'create')"
        clicked, detail = _click_first_visible(driver, [
            (By.XPATH, f"//a[{text_predicate}]"),
            (By.XPATH, f"//button[{text_predicate}]"),
            (By.CSS_SELECTOR, "a[href*='Create'], a[href*='create'], a[href*='Add'], a[href*='add'], a[href*='New'], a[href*='new']"),
        ])
        if not clicked:
            return False, "Requested feature entry point was not clickable; current page may be login/auth blocked or the feature is not discoverable from visible links.", {"method": "generic_form_flow", "detail": detail}
        messages.append(f"Clicked feature entry point via {detail}")
        WebDriverWait(driver, DEFAULT_WAIT_TIME).until(lambda d: d.find_elements(By.CSS_SELECTOR, "form"))
    else:
        messages.append("Form already visible")

    ok, fill_detail = _fill_visible_form_fields(driver)
    messages.extend(fill_detail)
    if not ok:
        return False, " | ".join(messages), {"method": "generic_form_flow"}

    submitted, submit_detail = _click_first_visible(driver, [
        (By.XPATH, "//button[@type='submit']"),
        (By.XPATH, "//button[contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'submit') or contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'save') or contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'create') or contains(translate(normalize-space(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'add')]"),
        (By.CSS_SELECTOR, "button[type='submit'], input[type='submit']"),
    ])
    messages.append(f"Submitted form via {submit_detail}")
    if not submitted:
        return False, " | ".join(messages), {"method": "generic_form_flow"}

    try:
        WebDriverWait(driver, 10).until(
            lambda d: (
                not _page_has_validation_errors(d)
                and (not d.find_elements(By.CSS_SELECTOR, "form") or _page_has_success_signal(d))
            )
        )
    except Exception:
        if _page_has_validation_errors(driver):
            return False, "Validation errors shown after submit: " + " | ".join(messages), {"method": "generic_form_flow", "url": driver.current_url}
        return False, "Submit did not reach a recognizable success state: " + " | ".join(messages), {"method": "generic_form_flow", "url": driver.current_url}

    messages.append(f"Reached success page: {driver.current_url}")
    return True, " | ".join(messages), {"method": "generic_form_flow", "url": driver.current_url}


def _page_has_validation_errors(driver) -> bool:
    page = driver.page_source.lower()
    return any(marker in page for marker in (
        "alert-danger", "field-validation-error", "validation-summary-errors",
        "is-invalid", "required field", "please select", "please enter",
    ))


def _page_has_success_signal(driver) -> bool:
    page = driver.page_source.lower()
    url = driver.current_url.lower()
    return any(marker in page or marker in url for marker in (
        "success", "created", "details", "saved", "submitted", "thank", "list", "index",
    ))


def _fill_visible_form_fields(driver):
    messages = []
    filled_any = False
    for select_el in driver.find_elements(By.CSS_SELECTOR, "form select"):
        try:
            if not select_el.is_displayed() or not select_el.is_enabled():
                continue
            select = Select(select_el)
            for option in select.options:
                value = (option.get_attribute("value") or "").strip()
                text = (option.text or "").strip()
                if value and not text.lower().startswith(("select", "-- select")):
                    select.select_by_value(value)
                    driver.execute_script("arguments[0].dispatchEvent(new Event('change', { bubbles: true }));", select_el)
                    messages.append(f"Selected {select_el.get_attribute('name') or select_el.get_attribute('id') or 'select'}={value}")
                    filled_any = True
                    break
        except Exception as exc:
            messages.append(f"Could not fill select: {str(exc).splitlines()[0]}")

    today = time.strftime("%Y-%m-%d")
    samples = {
        "email": "qa@example.com",
        "tel": "5551234567",
        "number": "1",
        "date": today,
        "text": "Automated QA Test",
        "search": "Automated QA Test",
        "url": "https://example.com",
    }
    for input_el in driver.find_elements(By.CSS_SELECTOR, "form input"):
        try:
            if not input_el.is_displayed() or not input_el.is_enabled():
                continue
            input_type = (input_el.get_attribute("type") or "text").lower()
            if input_type in {"hidden", "submit", "button", "reset", "checkbox", "radio", "file"}:
                continue
            value = samples.get(input_type, "Automated QA Test")
            input_el.clear()
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('input', { bubbles: true })); arguments[0].dispatchEvent(new Event('change', { bubbles: true }));",
                input_el,
                value,
            )
            messages.append(f"Filled {input_el.get_attribute('name') or input_el.get_attribute('id') or input_type}")
            filled_any = True
        except Exception as exc:
            messages.append(f"Could not fill input: {str(exc).splitlines()[0]}")

    for textarea in driver.find_elements(By.CSS_SELECTOR, "form textarea"):
        try:
            if not textarea.is_displayed() or not textarea.is_enabled():
                continue
            textarea.clear()
            textarea.send_keys("Automated QA Test")
            messages.append(f"Filled {textarea.get_attribute('name') or textarea.get_attribute('id') or 'textarea'}")
            filled_any = True
        except Exception as exc:
            messages.append(f"Could not fill textarea: {str(exc).splitlines()[0]}")

    return filled_any, messages or ["No fillable visible form fields found"]

def try_heuristic_find_and_execute(driver, action):
    act = action.get("action")
    target_str = action.get("target", "").lower()
    value = action.get("value")
    if act == "complete_requested_form_flow":
        return complete_requested_form_flow(driver, value or target_str)
    if act == "verify_text_present":
        page_src = driver.page_source.lower()
        if (value or "").lower() in page_src: return True, f"Verified text present: '{value}'"
        else: return False, f"Text not found: '{value}'"
    heuristic_keys = []
    if "user" in target_str or "email" in target_str: heuristic_keys.append("username")
    if "pass" in target_str: heuristic_keys.append("password")
    if "login" in target_str or "sign in" in target_str: heuristic_keys.append("login_button")
    if "forgot" in target_str: heuristic_keys.append("forgot_password")
    if "create" in target_str or "sign up" in target_str: heuristic_keys.append("create_account")
    for key in heuristic_keys:
        el, used_locator = find_element_by_heuristics(driver, HEURISTIC_SELECTORS.get(key, []))
        if el:
            try:
                highlight_element(driver, el)
                if act == "enter_text":
                    el.clear()
                    el.send_keys(value or "")
                    return True, f"Entered text into '{key}' via {used_locator}"
                elif act == "click":
                    WebDriverWait(driver, DEFAULT_WAIT_TIME).until(EC.element_to_be_clickable(el)).click()
                    return True, f"Clicked '{key}' via {used_locator}"
            except Exception as e: return False, f"Error interacting with '{key}' via {used_locator}: {e}"
    if target_str:
        try:
            generic_xpath = f"//*[normalize-space()='{action.get('target')}' or contains(text(), '{action.get('target')}') or @value='{action.get('target')}']"
            el = WebDriverWait(driver, 2).until(EC.presence_of_element_located((By.XPATH, generic_xpath)))
            if el and el.is_displayed():
                highlight_element(driver, el)
                if act == "click":
                    WebDriverWait(driver, DEFAULT_WAIT_TIME).until(EC.element_to_be_clickable(el)).click()
                    return True, f"Clicked generic text element via XPath for '{action.get('target')}'"
        except Exception: pass
    return False, f"All heuristics failed for target: '{target_str}'"

def _normalize_heuristic_result(result):
    if isinstance(result, tuple):
        if len(result) == 3:
            return result
        if len(result) == 2:
            success, message = result
            return success, message, {"method": "heuristic"}
    return False, f"Heuristic returned an unexpected result shape: {type(result).__name__}", {"method": "heuristic"}

async def execute_action_with_auto_heal(driver, url, action):
    """Async version of action execution with auto-healing"""
    action_text = action.get("action", "N/A").replace("_", " ").title()
    target_text = action.get("target", "N/A")
    display_message = f"Action: {action_text}: '{target_text}'"
    show_on_screen_message(driver, display_message)
    await asyncio.sleep(0.5)
    
    # Run heuristic execution in executor
    loop = asyncio.get_running_loop()
    heuristic_result = await loop.run_in_executor(None, try_heuristic_find_and_execute, driver, action)
    success, message, used_locator = _normalize_heuristic_result(heuristic_result)
    if success: return True, message, used_locator
    
    intent_text = f"Action: '{action.get('action')}', Target: '{target_text}', Value: '{action.get('value')}'"
    suggestion = await ask_gemini_for_locator(driver.page_source, url, intent_text)
    if suggestion and "strategy" in suggestion and "locator" in suggestion:
        strategy_str = suggestion["strategy"].lower()
        locator_val = suggestion["locator"]
        strategy_map = {"xpath": By.XPATH, "css": By.CSS_SELECTOR, "id": By.ID, "name": By.NAME}
        if strategy_str not in strategy_map: return False, f"LLM suggested unknown strategy: {strategy_str}", None
        by = strategy_map[strategy_str]
        try:
            el = WebDriverWait(driver, DEFAULT_WAIT_TIME).until(EC.presence_of_element_located((by, locator_val)))
            highlight_element(driver, el)
            act = action.get("action")
            if act == "click":
                WebDriverWait(driver, DEFAULT_WAIT_TIME).until(EC.element_to_be_clickable((by, locator_val))).click()
                msg = f"Clicked via LLM {strategy_str}: {locator_val}"
            elif act == "enter_text":
                el.clear()
                el.send_keys(action.get("value", ""))
                msg = f"Entered text via LLM {strategy_str}: {locator_val}"
            else: return True, "Action verified (LLM not needed for execution)", {"method": "llm_assist"}
            return True, msg, {"method": "llm", "strategy": strategy_str, "locator": locator_val}
        except Exception as e:
            error_msg = f"LLM locator failed: {str(e).splitlines()[0]}"
            return False, error_msg, {"method": "llm", "strategy": strategy_str, "locator": locator_val}
    return False, "Heuristics failed and no valid LLM suggestion was returned.", None

# --- Async Report Helpers ---
async def save_results_excel(results, filename="test_results.xlsx"):
    """Async Excel report generation"""
    def _create_excel():
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "TestResults"
        ws.append(["TestCaseID", "Description", "Status", "Detail", "UsedLocator"])
        for r in results: 
            ws.append([r["id"], r["description"], r["status"], r["detail"], json.dumps(r.get("used_locator") or {})])
        
        # Save to bytes
        output_buffer = io.BytesIO()
        wb.save(output_buffer)
        return output_buffer.getvalue()
    
    loop = asyncio.get_running_loop()
    excel_bytes = await loop.run_in_executor(None, _create_excel)
    
    async with aiofiles.open(filename, "wb") as f:
        await f.write(excel_bytes)
    
    await broadcast_log(f"Excel report saved: {filename}")
    return excel_bytes

async def save_results_html(results, url, filename="test_results.html"):
    """Async HTML report generation"""
    rows = ""
    for r in results:
        status_color = 'green' if r['status'] == 'Pass' else 'red'
        user_id = get_user_id()
        session_id = get_session_id()
        print(user_id,"====================",session_id)
        SCREENSHOT_DIR = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output/ui_test_screenshots" 
        shot_path = os.path.join(SCREENSHOT_DIR, f"{r['id']}.png")
        shot_link = f"<a href='{shot_path}' target='_blank'>view</a>" if os.path.exists(shot_path) else "N/A"
        locator_info = json.dumps(r.get("used_locator") or {})
        rows += f"""<tr><td>{r['id']}</td><td>{r['description']}</td><td style='color:{status_color}; font-weight:bold;'>{r['status']}</td><td>{r['detail']}</td><td>{locator_info}</td><td>{shot_link}</td></tr>"""
    
    html_content = f"""<!DOCTYPE html><html><head><title>Test Results</title><style>body{{font-family: Arial, sans-serif; margin: 20px;}} table{{width: 100%; border-collapse: collapse;}}th, td{{border: 1px solid #ccc; padding: 10px; text-align: left;}} th{{background-color: #f2f2f2;}}</style></head><body><h2>UI Agent Test Results for: <a href="{url}">{url}</a></h2><table><tr><th>ID</th><th>Description</th><th>Status</th><th>Details</th><th>Locator Method</th><th>Screenshot</th></tr>{rows}</table></body></html>"""
    
    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.write(html_content)
    
    await broadcast_log(f"HTML report saved: {filename}")
    return html_content

async def save_results_pdf(results, url, filename="test_results.pdf"):
    """Async PDF report generation"""
    if not REPORTLAB_AVAILABLE: 
        return None
    
    def _create_pdf():
        c = canvas.Canvas(filename, pagesize=A4)
        w, h = A4
        y = h - 50; c.setFont("Helvetica-Bold", 14)
        c.drawString(40, y, "UI Agent Test Results"); y -= 20
        c.setFont("Helvetica", 9)
        c.drawString(40, y, f"Target URL: {url}"); y -= 24
        for r in results:
            if y < 80: c.showPage(); y = h - 50
            line = f"{r['id']} | {r['status']} | {r['description']}"
            c.drawString(40, y, line[:120]); y -= 14
        c.save()
    
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _create_pdf)
    await broadcast_log(f"PDF report saved: {filename}")

def _build_chrome_options() -> Options:
    """Build the Chrome Options object used by run_agent_async.

    Extracted so tests can assert on the args without launching a browser, and so the
    TESTING_AGENT_HEADLESS flag has a single place to toggle --headless=new for
    server/enterprise deploys where no display is available.
    """
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')

    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--enable-logging")
    chrome_options.add_argument("--v=1")

    # Set logging preferences within the Options object
    chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    if TESTING_AGENT_HEADLESS:
        chrome_options.add_argument("--headless=new")

    return chrome_options


# --- Main Async Agent Flow ---
async def run_agent_async(url: str, max_cases: int = 12, session_id: Optional[str] = None, user_id: Optional[str] = None, user_goal: str = "", planned_cases: Optional[List[Dict]] = None) -> Optional[List[Dict]]:
    """
    Async version of the UI testing agent with broadcasting support
    """
    url = _normalize_target_url(url)
    if session_id:
        set_session_id(session_id)
    if user_id:
        set_user_id(user_id)
    await broadcast_log("Starting UI testing agent...", level="INFO")
    
    # SSL Handling for Corporate Networks
    os.environ['WDM_SSL_VERIFY'] = '0'
    await broadcast_log("SSL verification disabled for webdriver-manager to support corporate proxies.")
    chrome_options = _build_chrome_options()


    try:
        # Initialize WebDriver in executor
        loop = asyncio.get_running_loop()
        
        driver = await loop.run_in_executor(None, lambda: webdriver.Chrome(options=chrome_options))
    except Exception as e:
        await broadcast_log(f"Failed to initialize WebDriver: {e}", level="ERROR")
        return None
    
    try:
        await broadcast_log(f"Loading URL: {url}")
        await loop.run_in_executor(None, driver.get, url)
        await loop.run_in_executor(None, lambda: WebDriverWait(driver, 10).until(lambda d: d.execute_script('return document.readyState') == 'complete'))
    except Exception as e:
        await broadcast_log(f"Failed to load URL: {url}. Error: {e}", level="ERROR")
        await loop.run_in_executor(None, driver.quit)
        return None
    
    # Process page HTML
    page_source = await loop.run_in_executor(None, lambda: driver.page_source)
    soup = BeautifulSoup(page_source, "html.parser")
    for tag in soup(["script", "style", "link", "meta"]): tag.decompose()
    page_html = str(soup.body)[:15000] if soup.body else str(soup)[:15000]
    
    # Use pre-approved cases when available; fall back to live-HTML generation.
    testcases = await ask_gemini_for_testcases(
        page_html, url, n=max_cases, user_goal=user_goal, planned_cases=planned_cases
    )
    await broadcast_log(f"Running {len(testcases)} test case(s)")
    
    results = []
    for tc in testcases:
        tc_id = tc.get("id", f"TC_UNKNOWN_{len(results)+1}")
        desc = tc.get("description", "N/A")
        await broadcast_log(f"Running {tc_id}: {desc}")
        
        tc_display_message = f"Test Case: {tc_id}<br><small>{desc}</small>"
        show_on_screen_message(driver, tc_display_message, duration_ms=3500)
        await asyncio.sleep(1)
        
        final_status = "Pass"
        step_messages = []
        final_locator_info = None
        
        try:
            await loop.run_in_executor(None, driver.get, url)
            await asyncio.sleep(1.5)
        except Exception as e:
            await broadcast_log(f"Could not reset URL for {tc_id}: {e}", level="ERROR")

        for i, step in enumerate(tc.get("steps", [])):
            try:
                success, msg, used_locator = await execute_action_with_auto_heal(driver, url, step)
            except Exception as step_exc:
                success, msg, used_locator = False, f"Unhandled exception: {step_exc}", None
            step_messages.append(f"Step {i+1}: {msg}")
            if used_locator:
                final_locator_info = used_locator
            if not success:
                final_status = "Fail"
                await broadcast_log(f"  Step {i+1} failed: {msg}", level="WARNING")
                # Skip remaining steps for THIS case, then continue with the next case
                break
            else:
                await broadcast_log(f"  Step {i+1} passed: {msg}")
        
        shot_path = await save_screenshot(driver, tc_id)
        results.append({
            "id": tc_id, "description": desc, "status": final_status,
            "detail": " | ".join(step_messages), "used_locator": final_locator_info, "screenshot": shot_path
        })
    
    # Generate and save reports
    session_id = session_id or get_session_id()
    if BROADCASTING_AVAILABLE:
        user_id = get_user_id()
        output_dir = f"{esett.FILES}/{user_id}/orchestrator/{session_id}/output"
        await loop.run_in_executor(None, lambda: os.makedirs(output_dir, exist_ok=True))
        
        # Save Excel report
        excel_path = os.path.join(output_dir, "ui_test_results.xlsx")
        excel_bytes = await save_results_excel(results, excel_path)
        await broadcast_file_generated(session_id, "ui_test_results.xlsx", excel_path)
        
        # Save HTML report  
        html_path = os.path.join(output_dir, "ui_test_results.html")
        html_content = await save_results_html(results, url, html_path)
        await broadcast_file_generated(session_id, "ui_test_results.html", html_path)
        
        # Save PDF report if available
        if REPORTLAB_AVAILABLE:
            pdf_path = os.path.join(output_dir, "ui_test_results.pdf")
            await save_results_pdf(results, url, pdf_path)
            await broadcast_file_generated(session_id, "ui_test_results.pdf", pdf_path)
    
    await broadcast_log("Agent finished. Browser will close in 5 seconds.")
    await asyncio.sleep(5)
    await loop.run_in_executor(None, driver.quit)
    
    passed_count = sum(1 for r in results if r['status'] == 'Pass')
    failed_count = len(results) - passed_count
    await broadcast_log(f"UI testing completed: {passed_count} passed, {failed_count} failed", level="INFO")
    
    return results

# --- Sync Wrapper for Backward Compatibility ---
def run_agent(url: str, max_cases: int = 12, session_id: Optional[str] = None, user_id: Optional[str] = None, user_goal: str = "", planned_cases: Optional[List[Dict]] = None) -> Optional[List[Dict]]:
    """Synchronous wrapper for backward compatibility"""
    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        return asyncio.run(run_agent_async(url, max_cases, session_id, user_id, user_goal, planned_cases))
    except RuntimeError:
        return asyncio.run(run_agent_async(url, max_cases, session_id, user_id, user_goal, planned_cases))

async def main():
    """Async main function"""
    TARGET_URL = "https://www.linkedin.com/login"
    await run_agent_async(TARGET_URL, max_cases=10)

if __name__ == "__main__":
    asyncio.run(main())
