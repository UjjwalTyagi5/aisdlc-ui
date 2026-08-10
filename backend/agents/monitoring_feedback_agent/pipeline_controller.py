# pipeline_controller.py

from agents.monitoring_feedback_agent.log_processor import parse_logs_from_text
from agents.monitoring_feedback_agent.log_chunker import chunk_logs
from agents.monitoring_feedback_agent.llm_analysis import analyze_logs
from agents.monitoring_feedback_agent.action_decision import generate_issue_description_and_suggestions
# pipeline_controller.py

import json
import re
import time
from tenacity import RetryError
from google.api_core.exceptions import ResourceExhausted

# from log_processor import parse_logs_from_text
# from log_chunker import chunk_logs
import google.generativeai as genai

# --- User-Friendly Error Message for Rate Limits ---
RATE_LIMIT_ERROR_RESPONSE = [{
    "issue_description": "The analysis could not be completed because the AI service is currently busy or unavailable.",
    "suggestions": ["This is often due to rate limits on the free tier.", "Please wait a few minutes and try again.", "If the problem persists, the daily quota may have been reached."]
}]


# --- FUNCTION 1: FOR LARGE FILE UPLOADS (Efficient) ---
def process_latest_issue(input_data: str, user_message: str) -> list:
    """
    Finds and analyzes only the latest error from a large log file. This is the
    most efficient method for large, noisy inputs where the user wants the most
    recent problem.
    """
    print("[Pipeline] Executing 'process_latest_issue' for a large file...")
    # Lazy import to prevent circular dependency issues if they arise later
    # from llm_analysis import analyze_logs
    # from action_decision import generate_issue_description_and_suggestions

    all_logs = parse_logs_from_text(input_data)
    if not all_logs:
        return [{"issue_description": "No parsable log lines were found in the uploaded file.", "suggestions": []}]
        
    latest_error_trace_id = None
    # Iterate backwards through the logs to find the first ERROR from the end
    for log in reversed(all_logs):
        level = log.get('level', '').upper()
        if level in ['ERROR', 'FATAL', 'CRITICAL']:
            latest_error_trace_id = log.get('trace_id')
            break
            
    if not latest_error_trace_id:
        return [{"issue_description": "No ERROR level logs were found in the file.", "suggestions": ["The file appears to be clean or contains only INFO/WARN level messages."]}]

    # Filter down to only the logs relevant to the latest issue
    relevant_logs = [log for log in all_logs if log.get('trace_id') == latest_error_trace_id]
    log_chunks = chunk_logs(relevant_logs)
    if not log_chunks: return []

    try:
        # This pipeline makes multiple, smaller calls, which is fine for a single issue.
        analysis_result = analyze_logs(log_chunks[0])
        
        output_dict = {
            "rca": analysis_result.get('rca').dict() if analysis_result.get('rca') else None,
            "summary": analysis_result.get('summary').dict() if analysis_result.get('summary') else None,
        }
        user_facing_info = generate_issue_description_and_suggestions(output_dict, user_message)
        output_dict.update(user_facing_info)
        return [output_dict]

    except (ResourceExhausted, RetryError) as e:
        print(f"🔴 CAUGHT RATE LIMIT ERROR in 'process_latest_issue': {e}")
        return RATE_LIMIT_ERROR_RESPONSE
    except Exception as e:
        print(f"🔴 An unexpected error occurred in 'process_latest_issue': {e}")
        return [{"issue_description": "An unexpected internal error occurred during analysis.", "suggestions": ["Please report this issue to the administrator."]}]


# --- FUNCTION 2: FOR PASTED LOG TEXT (Highly Efficient Batching) ---
def process_pasted_logs_batch(input_data: str, user_message: str) -> list:
    """
    Analyzes ALL chunks from a pasted block of text and generates suggestions
    in a SINGLE, efficient batch API call to minimize rate limit issues.
    """
    print("[Pipeline] Executing HIGHLY EFFICIENT 'process_pasted_logs_batch'...")
    all_logs = parse_logs_from_text(input_data)
    if not all_logs: return []

    log_chunks = chunk_logs(all_logs)
    chunks_json_string = json.dumps(log_chunks, indent=2)

    # This prompt is engineered to do all the work in one shot.
    prompt = f"""
    You are a data processing engine. Your sole function is to process the provided JSON input and return a JSON output. Do not add any conversational text, introductions, or explanations. Your output must be only a valid JSON array.

    The user's original question for context is: "{user_message}"

    Analyze the following JSON array of log chunks:
    {chunks_json_string}

    For each object in the input array, create a corresponding object in the output array. Each output object must contain these exact keys: "trace_id", "rca", "summary", "issue_description", "suggestions".

    1.  `trace_id`: Copy from the input.
    2.  `rca`: A JSON object with a "rca" (string) and a "confidence" (float).
    3.  `summary`: A JSON object with a "summary" (string) and an "exception_type" (string or null).
    4.  `issue_description`: A single string that explains the issue in relation to the user's question.
    5.  `suggestions`: A JSON array of actionable string steps for a developer.

    The final output must be a single JSON array, starting with `[` and ending with `]`. Do not wrap it in markdown ```json blocks.
    """

    try:
        print("[Pipeline] Making a SINGLE, all-in-one API call to Gemini...")
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        print("--- Full Model Response Text ---")
        print(response.text)
        print("--------------------------------")
        
        # This is the robust parsing logic to handle potential markdown code blocks.
        json_string_to_parse = ""
        # First, look for a ```json ... ``` block.
        json_match = re.search(r'```json\s*([\s\S]*?)\s*```', response.text)
        
        if json_match:
            print("[Pipeline] Found a JSON markdown block. Extracting content.")
            json_string_to_parse = json_match.group(1)
        else:
            # If no markdown, assume the entire response is the JSON content.
            print("[Pipeline] No markdown block found. Assuming entire response is JSON.")
            json_string_to_parse = response.text.strip()
            
        # Now, try to parse the string we've extracted.
        final_results = json.loads(json_string_to_parse)
        print(f"[Pipeline] Successfully parsed JSON. Found {len(final_results)} results.")
        return final_results

    except json.JSONDecodeError as e:
        print(f"🔴 ERROR: Failed to decode the extracted JSON string. Error: {e}")
        return [{"issue_description": "The AI model's response was not valid JSON.", "suggestions": ["The format of the AI's output was corrupted. Please try again."]}]
        
    except (ResourceExhausted, RetryError) as e:
        print(f"🔴 CAUGHT RATE LIMIT ERROR in 'process_pasted_logs_batch': {e}")
        return RATE_LIMIT_ERROR_RESPONSE
    except Exception as e:
        print(f"🔴 An unexpected error occurred in 'process_pasted_logs_batch': {e}")
        return [{"issue_description": "An unexpected internal error occurred during batch analysis.", "suggestions": ["Please report this issue to the administrator."]}]