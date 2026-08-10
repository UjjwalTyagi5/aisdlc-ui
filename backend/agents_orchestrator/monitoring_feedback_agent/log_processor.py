# log_processor.py

import re
from datetime import datetime
from pygrok import Grok

LOG_PATTERNS = {
    'my_app_log': r'%{TIMESTAMP_ISO8601:timestamp} \[%{LOGLEVEL:level}\] App: %{DATA:app}, Env: %{WORD:env}, TraceID: %{DATA:trace_id}, %{GREEDYDATA:message}',
    'nginx_access_error': r'%{IPORHOST:clientip} - %{USER:ident} \[%{HTTPDATE:timestamp}\] "%{WORD:verb} %{URIPATHPARAM:request} HTTP/%{NUMBER:httpversion}" %{NUMBER:status:int} %{NUMBER:bytes:int}',
    'linux_syslog': r'%{SYSLOGTIMESTAMP:timestamp} %{HOSTNAME:hostname} %{PROG:app}: %{GREEDYDATA:message}',
    'java_stack_trace_start': r'%{JAVACLASS:exception}: %{GREEDYDATA:message}'
}

def parse_with_grok(log_line: str) -> dict | None:
    """
    Tries each Grok pattern from our library against a log line.
    Returns a dictionary on the first successful match.
    """
    for parser_name, pattern in LOG_PATTERNS.items():
        # Create a Grok object for the current pattern
        grok = Grok(pattern)
        # Try to match the line
        match = grok.match(log_line)
        
        # If a match is found, we have our data!
        if match:
            # We add the name of the successful parser for debugging purposes.
            match['parser'] = parser_name
            
            # --- Data Normalization ---
            # Grok gives us strings. Let's ensure a few key fields are standardized.
            # If a 'status' code was parsed and it's an error code (4xx or 5xx), set the level.
            if 'status' in match and match['status'] >= 400:
                match['level'] = 'ERROR' if match['status'] >= 500 else 'WARN'
            
            # Ensure a 'trace_id' exists, even if it's generic.
            if 'trace_id' not in match:
                match['trace_id'] = 'unstructured_blob' # Default for logs without a clear trace
            
            return match
            
    # If no patterns matched after checking all of them, return None.
    return None

def parse_generic_log(log_line: str) -> dict:
    """
    This is the fallback for any line that doesn't match any of our Grok patterns.
    It ensures no log line is ever thrown away.
    """
    return {
        'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S,000"),
        'level': 'UNKNOWN',
        'trace_id': 'unstructured_blob',
        'message': log_line.strip(),
        'parser': 'generic_fallback'
    }

def parse_logs_from_text(raw_text: str) -> list:
    """
    The main function for this module. It orchestrates the parsing process.
    """
    lines = raw_text.strip().split('\n')
    processed_logs = []
    
    for line in lines:
        if not line.strip():
            continue

        # 1. Try to parse with our powerful Grok library first.
        parsed = parse_with_grok(line)
        
        # 2. If Grok fails for this line, use the generic fallback.
        if not parsed:
            parsed = parse_generic_log(line)
            
        processed_logs.append(parsed)
            
    return processed_logs