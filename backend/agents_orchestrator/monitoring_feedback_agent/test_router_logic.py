# test_router_logic.py

import asyncio
import json
from unittest.mock import MagicMock, AsyncMock

# Import the 'chat' function we want to test from our router file.
from monitoring_feedback_agent_api import chat

# --- Test Data and Mocks ---

# 1. Sample User Inputs
SAMPLE_SESSION_ID = "test-session-_logic_123"
SAMPLE_USER_ID = "test-user-logic-001"

# 2. A mock object to simulate FastAPI's UploadFile
# FastAPI expects an object with a 'filename' attribute and an async 'read' method.
# We create a simple mock that provides exactly that.
mock_file = MagicMock()
mock_file.filename = "sample_log.txt"
# The 'read' method must be an AsyncMock because the code calls 'await file.read()'
mock_file.read = AsyncMock()

# --- We will define two test scenarios ---

async def run_test_with_file():
    """
    Scenario 1: Tests the logic when a user uploads a file.
    """
    print("\n--- SCENARIO 1: TESTING WITH FILE UPLOAD ---")
    
    # Define the content our mock file will "contain"
    # Note: .encode('utf-8') is crucial because file reads return bytes, not strings.
    sample_file_content = b"2024-05-23 12:00:00,123 [ERROR] App: inventory-service, Env: prod, TraceID: file-err-1, Stock level cannot be negative."
    mock_file.read.return_value = sample_file_content
    
    # Call our chat function directly, passing the mock objects as arguments.
    response = await chat(
        session_id=SAMPLE_SESSION_ID,
        user_id=SAMPLE_USER_ID,
        user_message="Please analyze the error in the attached file.",
        uploaded_files=[mock_file]  # We pass a list containing our mock file
    )
    
    print("\n[TEST RESULT] Received response:")
    print(json.dumps(response, indent=2))
    print("--- SCENARIO 1 COMPLETE ---")


async def run_test_with_pasted_text():
    """
    Scenario 2: Tests the logic when a user pastes logs into the message.
    """
    print("\n--- SCENARIO 2: TESTING WITH PASTED TEXT ---")
    
    # In this scenario, the 'uploaded_files' argument is None.
    pasted_message = "what is this error? 2024-05-23 12:30:00,456 [ERROR] App: shipping-service, Env: uat, TraceID: text-err-2, Invalid address format for destination."
    
    response = await chat(
        session_id=SAMPLE_SESSION_ID,
        user_id=SAMPLE_USER_ID,
        user_message=pasted_message,
        uploaded_files=None  # Explicitly setting this to None
    )
    
    print("\n[TEST RESULT] Received response:")
    print(json.dumps(response, indent=2))
    print("--- SCENARIO 2 COMPLETE ---")


# --- Main Execution Block ---
# This part uses asyncio to run our async test functions.
async def main():
    await run_test_with_file()
    await run_test_with_pasted_text()

if __name__ == "__main__":
    print(">>> Starting Isolated Router Logic Test <<<")
    # asyncio.run() is the command to execute the main async function.
    asyncio.run(main())
    print("\n>>> Router Logic Test Finished <<<")