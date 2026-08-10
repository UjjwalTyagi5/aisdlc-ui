# test_pipeline.py

import json

# --- UPDATED ---
# We now import the new, more efficient function from our controller.
from pipeline_controller import process_latest_issue

# --- SAMPLE DATA (Unchanged) ---
# This simulates the text from a user's uploaded file.
SAMPLE_LOG_DATA = """
2024-05-22 10:25:00,100 [INFO] App: auth-service, Env: prod, TraceID: jkl-456, User 'testuser' logged in successfully.
2024-05-22 10:30:01,123 [ERROR] App: payment-service, Env: prod, TraceID: abc-123, Database connection timed out after 3000ms
This is an unstructured error message that appeared just before the main crash.
The system is feeling a bit tired today.
2024-05-22 10:30:02,456 [WARN] App: payment-service, Env: prod, TraceID: abc-123, Retrying connection to database...
2024-05-22 10:45:10,789 [ERROR] App: user-service, Env: prod, TraceID: xyz-789, User profile not found for user_id: 999
User '999' does not exist in the legacy authentication system.
"""

# --- NEW: SAMPLE USER MESSAGE ---
# This simulates the question the user typed into the chat box.
SAMPLE_USER_MESSAGE = "I'm seeing failures in production. Can you tell me about the latest critical error?"


# This is the main execution block that runs when you type `python test_pipeline.py`
if __name__ == "__main__":
    print("--- Starting Context-Aware Pipeline Test ---")
    
    # --- UPDATED ---
    # We now call the new function and pass BOTH the log data and the user's message.
    final_analysis_results = process_latest_issue(SAMPLE_LOG_DATA, SAMPLE_USER_MESSAGE)
    
    print("\n--- Pipeline Test Complete ---")
    
    # --- The output printing logic is the same ---
    if final_analysis_results:
        print(f"✅ Success! The pipeline produced {len(final_analysis_results)} focused analysis result(s).")
        print("Final Output:")
        # The json.dumps function with indent=2 makes the output pretty.
        print(json.dumps(final_analysis_results, indent=2))
    else:
        print("❌ Failure! The pipeline did not produce any results.")