---
name: functional_ui
description: Functional UI tests via the existing Selenium sub-agent. This skill does NOT generate code — it produces a metadata stub indicating that invoke_ui_testing_agent should be triggered for {{target_url}}. The aggregator picks up state["ui_test_results"] from the existing UI flow.
when_to_use: target_url starts with http:// or https://
inputs:
  - target_url
outputs:
  test_file_path: string
  test_framework: string
  scenario_count: int
---

# System

This skill is a passthrough placeholder. Output exactly:

UI_TEST_TRIGGER:{{target_url}}

(The dispatch node detects this prefix and skips writing the file to disk; instead it sets state["target_url"] so the existing UI test path runs after fan-in.)

# Variables
- target_url: {{target_url}}

# Output rules
- Output ONLY the line `UI_TEST_TRIGGER:{{target_url}}`, nothing else.
