from typing import Dict, Any

class BaseAgent():
    error = ""
    def __init__(self, llm_client, prompt_template):
        self.llm_client = llm_client
        self.prompt_template = prompt_template
        
