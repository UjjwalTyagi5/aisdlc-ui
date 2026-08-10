# shared_state.py
from typing import Dict, List

# This file holds state that needs to be shared across different requests.
# For now, we only need to track the last session ID to detect when a new session starts.
prev_session_id = ""
chat_histories: Dict[str, List[Dict]] = {}
