# action_decision.py

import os
import re
import json
from dotenv import load_dotenv
import google.generativeai as genai

# --- Setup (Unchanged) ---
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("🔴 ERROR in action_decision.py: GOOGLE_API_KEY not found.")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-2.5-flash')


# --- UPDATED FUNCTION SIGNATURE ---
# It now accepts the user's original message as context.
def generate_issue_description_and_suggestions(analysis: dict, user_message: str) -> dict:
    """
    Takes a completed analysis and the user's original question to generate
    a tailored, user-friendly issue description and suggestions.
    """
    rca_result = analysis.get('rca')
    
    if not rca_result or not rca_result.get('rca'):
        return {
            "issue_description": "Could not determine the issue from the logs provided.",
            "suggestions": ["Please provide more detailed logs, especially any error or exception stack traces."]
        }

    # --- UPDATED PROMPT ---
    # The prompt now includes the user's question, making the AI's response contextual.
    prompt = f"""
    You are a helpful Senior Site Reliability Engineer (SRE) assistant.
    A user has asked the following question about their logs:
    **User's Question:** "{user_message}"

    An automated analysis of the logs has produced the following Root Cause Analysis (RCA):
    **RCA:** {rca_result.get('rca')}
    **Confidence Score:** {rca_result.get('confidence')}

    Based on the user's question and the RCA, your task is to do two things:
    1.  Write a clear "issue_description" that directly answers the user's question based on the RCA.
    2.  Create a list of 2-3 concrete, actionable "suggestions" for a developer to resolve the issue.

    You MUST return the output as a single, valid JSON object with the keys "issue_description" and "suggestions". Do not include any other text, markdown, or explanations.
    """

    try:
        response = model.generate_content(prompt)
        json_match = re.search(r'\{.*\}', response.text, re.DOTALL)
        if json_match:
            result_dict = json.loads(json_match.group(0))
            if "issue_description" in result_dict and "suggestions" in result_dict:
                return result_dict
    
    except Exception as e:
        print(f"🔴 ERROR in generate_issue_description_and_suggestions: {e}")

    # Fallback response
    return {
        "issue_description": "An error occurred while generating a tailored response.",
        "suggestions": ["The analysis was completed, but suggestions could not be generated. Please review the RCA manually."]
    }