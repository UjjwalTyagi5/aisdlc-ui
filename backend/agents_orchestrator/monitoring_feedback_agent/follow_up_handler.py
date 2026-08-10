import os
import json
import litellm
from typing import List, Dict
from datetime import datetime
from uuid import uuid4
from ._byok import resolved_litellm_kwargs


def _create_prompt(analysis_context: dict, chat_history: List[Dict], follow_up_question: str) -> str:
    """A private helper to generate the prompt, used by both WS and REST handlers."""
    rca_summary = analysis_context.get("rca", {}).get("rca", "N/A")
    suggestions = analysis_context.get("suggestions", [])
    
    history_summary = []
    for turn in chat_history:
        role = "User" if turn['role'] == 'user' else "Assistant"
        content = turn['content']
        message_text = content.get('responses', content) if isinstance(content, dict) else content
        history_summary.append(f"{role}: {message_text}")
    joined_history = "\n".join(history_summary)
    return f"""
    You are a helpful Senior Site Reliability Engineer (SRE) assistant, continuing a conversation with a user.

    You have already performed a log analysis. Here is a summary of your key findings from that analysis:
    **Original Root Cause (RCA):** {rca_summary}
    **Original Suggestions:** {json.dumps(suggestions, indent=2)}

    Here is the conversation so far:
    ---
    {joined_history}
    ---
    
    Now, answer the user's latest follow-up question based on all the context above. Be direct, conversational, and helpful. Do not repeat the full analysis unless specifically asked.
    
    **User's Follow-up Question:** "{follow_up_question}"
    """

async def handle_follow_up_ws(manager, session_id: str, analysis_context: dict, chat_history: List[Dict], follow_up_question: str) -> str:
    """
    Handles a follow-up question asynchronously, broadcasting status and final answer via WebSocket.
    Returns the generated text for history saving.
    """
    print("[Follow-up Handler] Broadcasting and generating async answer for follow-up question...")
    
    # 1. Broadcast that we are starting
    await manager.broadcast({
        "type": "activity_update",
        "activity": {
            "id": f"activity_{int(datetime.utcnow().timestamp()*1000)}_{uuid4().hex[:6]}",
            "message": "Thinking about your follow-up question...",
            "type": "processing",
            "time": datetime.utcnow().strftime("%H:%M:%S"),
            "sessionId": session_id,
        }
    })
    
    prompt = _create_prompt(analysis_context, chat_history, follow_up_question)
    
    try:
        response = await litellm.acompletion(
            messages=[{"role": "user", "content": prompt}],
            **resolved_litellm_kwargs(),
        )
        final_answer = response.choices[0].message.content
        print("[Follow-up Handler] Successfully generated response.")
        
        await manager.send_agent_response("Monitoring Agent", final_answer, session_id)
        
        return final_answer

    except Exception as e:
        print(f" ERROR in handle_follow_up_ws: {e}")
        error_msg = "I'm sorry, but I encountered an error while trying to answer your follow-up question. Please try asking again."
        await manager.send_agent_response("Error Agent", error_msg, session_id)
        return error_msg


def generate_follow_up_rest(analysis_context: dict, chat_history: List[Dict], follow_up_question: str) -> str:
    """
    Generates a follow-up answer synchronously for the REST endpoint. Does not broadcast.
    """
    print("[Follow-up Handler] Generating synchronous REST answer for follow-up question...")
    
    prompt = _create_prompt(analysis_context, chat_history, follow_up_question)
    
    try:
        response = litellm.completion(
            messages=[{"role": "user", "content": prompt}],
            **resolved_litellm_kwargs(),
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f" ERROR in generate_follow_up_rest: {e}")
        return "I'm sorry, but I encountered an error while trying to answer your follow-up question."