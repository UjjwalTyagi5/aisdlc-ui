# log_chunker.py

from collections import defaultdict

def chunk_logs(logs: list) -> list:
    """
    Chunks a list of log dictionaries by their 'trace_id'.
    Returns a list of "chunks", where each chunk is a dictionary
    containing the trace_id and its corresponding list of logs.
    """
    grouped = defaultdict(list)
    for log in logs:
        # The parser ensures 'trace_id' always exists.
        grouped[log['trace_id']].append(log)
    
    chunks = []
    for trace_id, log_group in grouped.items():
        # Generate a summary for context. This is helpful for the AI later.
        summary = f"Analysis for TraceID: {trace_id}"
        if trace_id == 'unstructured_blob':
            summary = f"Analysis for a block of {len(log_group)} unstructured log lines."
        elif log_group:
            summary = f"Logs for TraceID {trace_id} from app '{log_group[0].get('app', 'N/A')}' in env '{log_group[0].get('env', 'N/A')}'"

        chunk = {
            'trace_id': trace_id,
            'logs': log_group,
            'summary': summary
        }
        chunks.append(chunk)
        
    return chunks