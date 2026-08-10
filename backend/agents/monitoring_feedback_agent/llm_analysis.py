# llm_analysis.py

# --- Part 1: IMPORTS and SETUP ---
# We bring in all the necessary libraries and tools.
import os
import re
import json
import logging
import sqlite3
import numpy as np
import faiss
from dotenv import load_dotenv
import google.generativeai as genai
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from typing import Dict, TypedDict, List, Optional, Any
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_fixed

# Configure basic logging to see outputs and errors
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# --- Part 2: SECURELY CONFIGURE THE AI MODEL ---
# This part loads your secret key from the .env file and sets up the connection to Google's AI.
logger.info("Loading API key and configuring Gemini...")
load_dotenv()
api_key = os.getenv("GOOGLE_API_KEY")

if not api_key:
    raise ValueError("🔴 ERROR: GOOGLE_API_KEY not found. Please create a .env file and add it.")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-2.5-flash')
embedding_model = 'models/text-embedding-004'
logger.info("Gemini configured successfully.")


# --- Part 3: DEFINE DATA STRUCTURES ---
# We define the shape of our data. This is your excellent Pydantic/TypedDict setup.
class RCAResult(BaseModel):
    rca: str
    confidence: float

class SummaryResult(BaseModel):
    summary: str
    exception_type: Optional[str]

class IssueDetectionResult(BaseModel):
    known_issue: bool
    similar_issues: List[str]
    similarity_scores: List[float]

class LogChunkInput(BaseModel):
    trace_id: str
    logs: List[Dict[str, Any]]
    summary: Optional[str] = None

class AgentState(TypedDict):
    log_chunk: Dict[str, Any]
    rca: Optional[RCAResult]
    summary: Optional[SummaryResult]
    issue_detection: Optional[IssueDetectionResult]


# --- Part 4: DEFINE THE TOOLS ---
# These are special functions your AI agents can use.

@tool
def parse_stack_trace(log_chunk: LogChunkInput) -> Dict[str, Any]:
    """Parse stack trace from log chunk and categorize exceptions."""
    try:
        messages = [log['message'] for log in log_chunk.logs]
        exception_lines = messages
        exception_type = None
        if exception_lines:
            # Simple keyword-based exception finding
            for line in exception_lines:
                if 'NullPointerException' in line:
                    exception_type = 'NullPointerException'
                    break
                elif 'Database connection timeout' in line:
                    exception_type = 'DatabaseConnectionTimeout'
                    break
                elif 'API rate limit exceeded' in line:
                    exception_type = 'APIRateLimitExceeded'
                    break
                elif 'Invalid user input' in line:
                    exception_type = 'InvalidUserInput'
                    break
        return {'stack_trace': "\n".join(exception_lines), 'exception_type': exception_type}
    except Exception as e:
        logger.error(f"Error parsing stack trace: {e}")
        return {'stack_trace': '', 'exception_type': None}

@tool
def search_vector_db(query: str, k: int = 3) -> Dict[str, Any]:
    """Search FAISS for similar issues using Gemini embeddings."""
    # This tool requires a database and index file. We will create these later.
    # For now, it will gracefully handle them not existing.
    try:
        if not os.path.exists('logs.db') or not os.path.exists('vector_index.faiss'):
            logger.warning("Database or FAISS index not found. Skipping vector search.")
            return {'issues': [], 'scores': []}
        
        index = faiss.read_index('vector_index.faiss')
        if index.ntotal == 0:
            return {'issues': [], 'scores': []} # Index is empty

        with sqlite3.connect('logs.db') as conn:
            cursor = conn.cursor()
            # Fetch all issues to map indices back to text
            cursor.execute("SELECT issue FROM log_embeddings")
            issues = [row[0] for row in cursor.fetchall()]

            if not issues:
                return {'issues': [], 'scores': []}
            
            embedding_response = genai.embed_content(model=embedding_model, content=query, task_type="RETRIEVAL_QUERY")
            query_embedding = np.array([embedding_response['embedding']], dtype=np.float32)
            
            distances, indices = index.search(query_embedding, k)
            
            similar_issues = [issues[i] for i in indices[0] if i < len(issues)]
            similarity_scores = [1 / (1 + d) for d in distances[0]] # Convert L2 distance to a similarity score
            
            return {'issues': similar_issues, 'scores': similarity_scores}
    except Exception as e:
        logger.error(f"Error searching vector DB: {e}")
        return {'issues': [], 'scores': []}

def store_analysis_in_db(state: AgentState):
    """A helper function to store results in the SQLite database."""
    # This function is not an agent 'tool', but a step in our pipeline.
    try:
        with sqlite3.connect('logs.db') as conn:
            cursor = conn.cursor()
            trace_id = state['log_chunk']['trace_id']
            summary_text = state['summary'].summary if state.get('summary') else None

            # 1. Store the summary and its embedding if it exists and is new
            if summary_text:
                cursor.execute("SELECT 1 FROM log_embeddings WHERE issue = ?", (summary_text,))
                if cursor.fetchone() is None:
                    embedding_response = genai.embed_content(model=embedding_model, content=summary_text, task_type="RETRIEVAL_DOCUMENT")
                    embedding = np.array(embedding_response['embedding'], dtype=np.float32).tobytes()
                    cursor.execute("INSERT INTO log_embeddings (issue, embedding) VALUES (?, ?)", (summary_text, embedding))
                    logger.info(f"Stored new issue in vector DB: {summary_text[:80]}...")
            
            # 2. Store the analysis result
            cursor.execute(
                "INSERT INTO analysis (trace_id, rca, summary, known_issue, similar_issues) VALUES (?, ?, ?, ?, ?)",
                (
                    trace_id,
                    state['rca'].rca if state.get('rca') else "N/A",
                    summary_text or "N/A",
                    state['issue_detection'].known_issue if state.get('issue_detection') else False,
                    json.dumps(state['issue_detection'].similar_issues if state.get('issue_detection') else [])
                )
            )
            conn.commit()
        logger.info(f"Stored analysis for TraceID: {trace_id}")
    except Exception as e:
        logger.error(f"Error storing analysis in DB: {e}")


# --- Part 5: DEFINE THE "AGENTS" (The Thinkers) ---
# Each agent is a function responsible for one specific task.

def clean_json_response(text: str) -> Optional[dict]:
    """A robust function to extract a JSON object from a model's text response."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning(f"Could not decode JSON from extracted text: {match.group(0)}")
            return None
    logger.warning(f"No JSON object found in response text: {text}")
    return None

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def rca_agent(state: AgentState) -> AgentState:
    """This agent's only job is to determine the Root Cause Analysis (RCA)."""
    logger.info(f"RCA Agent: Analyzing trace ID {state['log_chunk']['trace_id']}")
    logs = json.dumps(state['log_chunk']['logs'], indent=2)
    prompt = f"""
    You are an expert software log analyst. Your task is to analyze the provided logs and identify the root cause of the issue.
    Provide a concise root cause analysis (max 100 words) and a confidence score (0.0 to 1.0).

    Logs:
    {logs}

    Return a valid JSON object with the following structure, and nothing else:
    {{
      "rca": "<root cause analysis>",
      "confidence": <float between 0.0 and 1.0>
    }}
    """
    response = model.generate_content(prompt)
    data = clean_json_response(response.text)
    if data and "rca" in data and "confidence" in data:
        state['rca'] = RCAResult(**data)
    else:
        state['rca'] = RCAResult(rca="Unable to determine root cause from logs.", confidence=0.0)
    return state

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def summary_agent(state: AgentState) -> AgentState:
    """This agent summarizes the technical issue and extracts the exception type."""
    logger.info(f"Summary Agent: Analyzing trace ID {state['log_chunk']['trace_id']}")
    stack_trace_data = parse_stack_trace.invoke({"log_chunk": state['log_chunk']})
    stack_trace = stack_trace_data['stack_trace']

    if not stack_trace:
        state['summary'] = SummaryResult(summary="No stack trace found for summarization.", exception_type=None)
        return state

    prompt = f"""
    You are a log summarization expert. Summarize the following stack trace, focusing on the primary exception and affected component.
    
    Stack trace:
    {stack_trace}

    Your output must be strictly in the following JSON format only:
    {{
      "summary": "<A concise, one-sentence summary of the technical error.>",
      "exception_type": "<The primary exception type, e.g., NullPointerException, or null if none is found.>"
    }}
    """
    response = model.generate_content(prompt)
    data = clean_json_response(response.text)
    if data and "summary" in data:
        state['summary'] = SummaryResult(summary=data['summary'], exception_type=data.get('exception_type'))
    else:
        state['summary'] = SummaryResult(summary="Failed to generate summary.", exception_type=None)
    return state
    
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def issue_detection_agent(state: AgentState) -> AgentState:
    """This agent checks if the issue has been seen before by searching the vector DB."""
    logger.info(f"Issue Detection Agent: Analyzing trace ID {state['log_chunk']['trace_id']}")
    summary = state.get('summary')
    if not summary or not summary.summary:
        state['issue_detection'] = IssueDetectionResult(known_issue=False, similar_issues=[], similarity_scores=[])
        return state

    # Use the summary to search for similar past issues
    vector_result = search_vector_db.invoke({"query": summary.summary})
    
    # Simple logic: if the top result has a high similarity score, we consider it a known issue.
    is_known = False
    if vector_result['scores'] and vector_result['scores'][0] > 0.9: # Threshold
        is_known = True

    state['issue_detection'] = IssueDetectionResult(
        known_issue=is_known,
        similar_issues=vector_result['issues'],
        similarity_scores=vector_result['scores']
    )
    return state


# --- Part 6: BUILD THE ANALYSIS GRAPH ---
# This graph defines the workflow, telling the agents in what order to work.
logger.info("Building the analysis graph...")
workflow = StateGraph(AgentState)

# Add the agent functions as "nodes" in the graph
workflow.add_node("rca_node", rca_agent)
workflow.add_node("summary_node", summary_agent)
workflow.add_node("issue_detection_node", issue_detection_agent)

# Set the connections ("edges") between the nodes
workflow.set_entry_point("rca_node")
workflow.add_edge("rca_node", "summary_node")
workflow.add_edge("summary_node", "issue_detection_node")
workflow.add_edge("issue_detection_node", END) # The last step

# Compile the graph into a runnable object
graph = workflow.compile()
logger.info("Graph built successfully.")


# --- Part 7: CREATE THE MAIN FUNCTION ---
# This is the single function that the rest of our application will call to start an analysis.
def analyze_logs(log_chunk: dict) -> dict:
    """
    The main entry point for this module.
    Takes a chunk of logs, runs it through the graph, stores the result, and returns it.
    """
    logger.info(f"Starting analysis for TraceID: {log_chunk.get('trace_id', 'N/A')}")
    initial_state = {"log_chunk": log_chunk}
    
    # Run the graph
    final_state = graph.invoke(initial_state)

    # Store the results in our database
    store_analysis_in_db(final_state)
    
    logger.info(f"Analysis finished for TraceID: {log_chunk.get('trace_id', 'N/A')}")
    return final_state