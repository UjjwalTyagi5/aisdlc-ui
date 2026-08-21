import os
import pandas as pd
from dotenv import load_dotenv
import re
import glob
from pathlib import Path
from typing import List, Dict, Any, Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from config.checkpoint import build_checkpointer
import json
from typing import TypedDict
from langgraph.graph import StateGraph
from datetime import datetime
import asyncio
import aiofiles
from config.ws_helper import set_session_id, broadcast_log
from config.connection_manager import manager


class WorkflowState(TypedDict):
    user_input: str
    uploaded_files: List[str]
    conversation_history: List[Dict[str, Any]]
    pending_action: Optional[str]
    session_id: str
    last_intent: Optional[str]
    waiting_for_files: bool

workflow = StateGraph(WorkflowState)

def dummy_node(state):
    return state

workflow = StateGraph(WorkflowState)
workflow.add_node("start", dummy_node)
workflow.set_entry_point("start")
workflow.set_finish_point("start")

load_dotenv()
from config.env import LITELLM_BASE_URL

_deploy_checkpointer = build_checkpointer("deployment")
app = workflow.compile(checkpointer=_deploy_checkpointer)


def _litellm_generate(prompt: str) -> str:
    """Synchronous LiteLLM call for deployment agent helpers — BYOK only.

    P3.6 (B3): no platform LiteLLM key. The model is read from the run's
    tenant-resolved BYOK model in the model_resolver contextvar. Fails CLOSED
    when nothing is resolved — there is no platform fallback.

    NOTE: this module is a prototype not on any live tenant-aware run path
    (the live deployment API uses agents/pipeline_app.py; nothing imports the
    deploying graph). It only works once a caller resolves a model and sets the
    contextvar (e.g. via contextvars.copy_context().run around the executor call,
    as the testing/design agents do) before invoking these helpers.
    """
    from shared.services.model_resolver import get_resolved_model

    resolved = get_resolved_model()
    if resolved is None:
        raise RuntimeError(
            "No BYOK model resolved for this deployment run. An administrator must "
            "configure and verify a model provider in Org Settings -> Model Providers."
        )
    # Deferred: importing litellm costs ~7s. sys.modules makes repeat calls free.
    import litellm
    response = litellm.completion(
        model=resolved.model,
        custom_llm_provider=resolved.litellm_provider,
        api_base=resolved.base_url or LITELLM_BASE_URL,
        api_key=resolved.api_key,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


# Global conversation memory storage
conversation_memory = {}

async def get_conversation_history_async(session_id: str) -> List[Dict[str, Any]]:
    """Get conversation history for a session - async version"""
    if session_id not in conversation_memory:
        conversation_memory[session_id] = []
    return conversation_memory[session_id]

async def add_to_conversation_history_async(session_id: str, user_input: str, response: str, files: List[str] = None, intent: str = None, action: str = None):
    """Add a conversation turn to history - async version"""
    
    if session_id not in conversation_memory:
        conversation_memory[session_id] = []
    
    conversation_memory[session_id].append({
        "timestamp": datetime.now().isoformat(),
        "user_input": user_input,
        "response": response,
        "files": files or [],
        "intent": intent,
        "action": action
    })
    
    # Keep only last 10 conversations to prevent memory bloat
    if len(conversation_memory[session_id]) > 10:
        conversation_memory[session_id] = conversation_memory[session_id][-10:]

async def get_conversation_context_async(session_id: str) -> str:
    """Get formatted conversation context for AI analysis - async version"""
    history = await get_conversation_history_async(session_id)
    if not history:
        return "No previous conversation history."
    
    context_parts = []
    for i, turn in enumerate(history[-3:], 1):  # Last 3 turns for context
        context_parts.append(f"""
Turn {i}:
User: {turn['user_input']}
Files: {turn.get('files', [])}
Intent: {turn.get('intent', 'Unknown')}
Response: {turn['response'][:200]}{'...' if len(turn['response']) > 200 else ''}
""")
    
    return "Previous conversation context:\n" + "\n".join(context_parts)

async def clear_memory_async(session_id: str = None):
    """Clear memory for a specific session or all sessions - async version"""
    global app, conversation_memory

    if session_id:
        try:
            if session_id in conversation_memory:
                del conversation_memory[session_id]
            app = workflow.compile(checkpointer=build_checkpointer("deployment"))
            broadcast_log(manager, f"✅ Memory cleared for session: {session_id}", level="INFO")
        except Exception as e:
            broadcast_log(manager, f"❌ Error clearing memory for session {session_id}: {e}", level="ERROR")
    else:
        conversation_memory.clear()
        app = workflow.compile(checkpointer=build_checkpointer("deployment"))
        broadcast_log(manager, "✅ All memory cleared", level="INFO")

@tool
async def classify_user_intent_with_context(user_input: str, session_id: str = "default", uploaded_files: List[str] = None) -> Dict[str, Any]:
    """Enhanced tool to classify user intent considering conversation history and current context."""
    broadcast_log(manager, f"Classifying user intent for session: {session_id}", level="INFO")
    
    # Get conversation context
    context = await get_conversation_context_async(session_id)
    files_uploaded = uploaded_files and len(uploaded_files) > 0
    
    prompt = f"""
You are an intelligent intent classification assistant with conversation memory. Analyze the user input considering the conversation history and current context.

CONVERSATION CONTEXT:
{context}

CURRENT USER INPUT: "{user_input}"
FILES CURRENTLY UPLOADED: {uploaded_files or []}
FILES AVAILABLE: {'YES' if files_uploaded else 'NO'}

INTENT CATEGORIES:
1. EVALUATE - User wants to evaluate code/test cases for deployment readiness
2. UPDATE - User wants to modify/update an existing evaluation report 
3. GREETING - User is greeting, saying hello, or making casual conversation 
4. HELP - User is asking for help, guidance, or what they can do
5. CONTINUE_EVALUATION - User uploaded files after being asked to, continuing previous evaluation request
6. CONTINUE_UPDATE - User uploaded files after being asked to, continuing previous update request
7. INVALID - Request is genuinely not related to deployment evaluation

SPECIAL LOGIC:
- If user previously asked for evaluation/help but no files were available, and now files are uploaded, classify as CONTINUE_EVALUATION
- If user previously asked for updates but no report existed, and now files are uploaded, classify as CONTINUE_UPDATE
- If user says "here are the files" or similar after being asked for files, classify as CONTINUE_EVALUATION or CONTINUE_UPDATE based on context
- If user just uploaded files without explicit request, suggest what they can do

RESPONSE FORMAT:
{{
    "intent": "EVALUATE|UPDATE|GREETING|HELP|CONTINUE_EVALUATION|CONTINUE_UPDATE|INVALID",
    "confidence": "HIGH|MEDIUM|LOW",
    "reasoning": "Brief explanation of why you classified it this way, considering conversation history",
    "recommended_tool": "deployment_evaluation_tool|update_response|none",
    "suggested_response": "Brief helpful response",
    "context_analysis": "How conversation history influenced this classification",
    "waiting_for_files": false,
    "can_proceed": true
}}
"""
    
    try:
        broadcast_log(manager, "Generating AI intent classification...", level="LOGS")
        loop = asyncio.get_event_loop()
        response_text = await loop.run_in_executor(None, _litellm_generate, prompt)
        text = response_text.strip()
        
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
        else:
            result = {
                "intent": "HELP",
                "confidence": "LOW",
                "reasoning": "Failed to parse AI response, defaulting to helpful mode",
                "recommended_tool": "none",
                "suggested_response": "I'm here to help with deployment evaluations. What would you like to do?",
                "context_analysis": "No context analysis available",
                "waiting_for_files": False,
                "can_proceed": True
            }
        
        broadcast_log(manager, f"🤖 Intent Classification (Session: {session_id}): {result}", level="LOGS")
        return result
        
    except Exception as e:
        broadcast_log(manager, f"❌ Error classifying intent: {e}", level="ERROR")
        return {
            "intent": "HELP",
            "confidence": "LOW",
            "reasoning": f"Error occurred, defaulting to helpful mode: {str(e)}",
            "recommended_tool": "none",
            "suggested_response": "I encountered an error but I'm here to help with deployment evaluations!",
            "context_analysis": "Error prevented context analysis",
            "waiting_for_files": False,
            "can_proceed": True
        }

@tool
async def update_response(file_names: List[str], query: str, content: str):
    """
    Completely dynamic tool that can update ANY part of the Excel report based on natural language requests.
    Uses AI to understand the request and make precise modifications to the Excel file.
    """
    broadcast_log(manager, f"🔧 Executing dynamic update tool for files: {file_names}", level="INFO")
    
    # Load file contexts
    file_contexts = []
    for name in file_names:
        if not os.path.exists(name):
            continue
        
        try:
            if name.endswith(('.xlsx', '.xls')):
                loop = asyncio.get_event_loop()
                df = await loop.run_in_executor(None, pd.read_excel, name)
                file_context = f"Excel file '{name}' content:\n{df.to_string()}\n\nColumns: {list(df.columns)}\n"
                file_contexts.append(file_context)
            elif name.endswith(('.py', '.js', '.java', '.cpp', '.c', '.cs', '.php', '.rb', 
                               '.go', '.ts', '.jsx', '.tsx', '.kt', '.swift', '.rs', '.scala', '.r', '.m', '.h')):
                async with aiofiles.open(name, "r", encoding='utf-8', errors='ignore') as f:
                    code_content = await f.read()
                file_context = f"Code file '{name}' content:\n```\n{code_content}\n```\n"
                file_contexts.append(file_context)
            elif name.endswith(('.txt', '.md', '.csv')):
                async with aiofiles.open(name, "r", encoding='utf-8', errors='ignore') as f:
                    text_content = await f.read()
                file_context = f"Text file '{name}' content:\n{text_content}\n"
                file_contexts.append(file_context)
        except Exception as e:
            file_contexts.append(f"Error reading file '{name}': {str(e)}")
    
    # Find the deployment report
    report_path = "deployment_evaluation_report.xlsx"
    updated_report_path = "deployment_evaluation_report_updated.xlsx"
    
    if not os.path.exists(report_path):
        return "❌ Error: No existing deployment report found to update. Please run an evaluation first."
    
    try:
        broadcast_log(manager, "Reading existing Excel file structure...", level="LOGS")
        # Read the existing Excel file structure
        loop = asyncio.get_event_loop()
        excel_file = await loop.run_in_executor(None, pd.ExcelFile, report_path)
        sheet_data = {}
        
        broadcast_log(manager, f"📊 Found sheets: {excel_file.sheet_names}", level="LOGS")
        
        # Load all sheets and their data
        for sheet_name in excel_file.sheet_names:
            df = await loop.run_in_executor(None, pd.read_excel, report_path, sheet_name)
            sheet_data[sheet_name] = df
            broadcast_log(manager, f"📄 Loaded sheet '{sheet_name}' with {len(df)} rows and columns: {list(df.columns)}", level="LOGS")
            
            # Show sample content for debugging
            if len(df) > 0:
                
                for col in df.columns:
                    sample_values = df[col].dropna().astype(str).head(3).tolist()
                    if sample_values:
                        broadcast_log(manager, f"   {col}: {sample_values}", level="LOGS")
            await asyncio.sleep(0.001)  # Allow other tasks to run
        
        # Create AI prompt to understand what needs to be updated
        combined_context = "\n".join(file_contexts)
        
        # Prepare sheet information for the AI
        sheet_info = []
        for sheet_name, df in sheet_data.items():
            sheet_info.append(f"""
Sheet: {sheet_name}
Columns: {list(df.columns)}
Sample data:
{df.head(3).to_string()}
Total rows: {len(df)}
""")
        
        sheets_description = "\n".join(sheet_info)
        
        ai_prompt = f"""
You are an Excel modification expert. The user wants to update an existing Excel report based on their request.

USER REQUEST: "{query}"

CURRENT EXCEL STRUCTURE:
{sheets_description}

CONTEXT FROM UPLOADED FILES:
{combined_context}

IMPORTANT: Use the EXACT sheet names and column names as shown above. Available sheets are: {list(sheet_data.keys())}

Your task is to provide specific modification instructions in JSON format. Analyze the user's request and determine:
1. Which sheet(s) need to be modified (use exact names from the list above)
2. What type of modification is needed
3. Exact instructions for the modification

Look carefully at the data structure. Common patterns:
- "Test Case Evaluation" or "Test Evaluation" sheets often contain sections like "Test-Based Deployment Decision"
- "Code Evaluation" sheets often contain sections like "Code-Based Deployment Decision"  
- "Evaluation Summary" sheets contain metrics and values
- Headers might be in 'Section' columns or direct column names

Respond with a JSON object containing:
{{
    "modifications": [
        {{
            "sheet_name": "exact_sheet_name_from_available_sheets",
            "modification_type": "rename_column|change_value|replace_text|add_column|remove_column|other",
            "target": "exact_text_or_column_name_to_find",
            "new_value": "what_to_replace_it_with",
            "condition": "optional_condition_for_when_to_apply_change",
            "description": "human_readable_description_of_change"
        }}
    ],
    "summary": "Brief description of all changes being made",
    "debug_info": {{
        "found_sheets": {list(sheet_data.keys())},
        "search_terms": ["terms", "you", "looked", "for"],
        "reasoning": "explanation of how you interpreted the request"
    }}
}}

Examples of modification types:
- rename_column: Change column header names
- change_value: Change specific cell values  
- replace_text: Find and replace text within cells (most common for section names)
- add_column: Add a new column
- remove_column: Remove an existing column

Be very specific and accurate with sheet names and column names as they appear in the data. If you can't find the exact text, suggest the closest match and explain why.
"""
        
        # Get AI analysis of what needs to be updated
        broadcast_log(manager, "Generating AI analysis for modifications...", level="LOGS")
        ai_response_raw = await loop.run_in_executor(None, _litellm_generate, ai_prompt)
        ai_response = ai_response_raw.strip()
        
        broadcast_log(manager, f"🤖 AI Analysis: {ai_response}", level="LOGS")
        
        # Parse AI response
        json_match = re.search(r'\{.*\}', ai_response, re.DOTALL)
        if not json_match:
            return "❌ Error: Could not understand the modification request. Please be more specific."
        
        try:
            modification_plan = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            return f"❌ Error parsing AI response: {e}"
        
       
        
        
        # Apply modifications dynamically
        changes_made = []
        modified_sheets = {}
        
        for sheet_name, df in sheet_data.items():
            modified_sheets[sheet_name] = df.copy()
        
        for mod in modification_plan.get("modifications", []):
            sheet_name = mod.get("sheet_name")
            mod_type = mod.get("modification_type")
            target = mod.get("target")
            new_value = mod.get("new_value")
            description = mod.get("description", "")
            
            
            
            if sheet_name not in modified_sheets:
                # Try to find a close match
                close_matches = [s for s in modified_sheets.keys() if target.lower() in s.lower() or sheet_name.lower() in s.lower()]
                if close_matches:
                    sheet_name = close_matches[0]
                    broadcast_log(manager, f"📍 Using closest match sheet: '{sheet_name}'", level="LOGS")
                else:
                    broadcast_log(manager, f"⚠️ Warning: Sheet '{sheet_name}' not found. Available sheets: {list(modified_sheets.keys())}", level="LOGS")
                    continue
            
            df = modified_sheets[sheet_name]
            
            try:
                if mod_type == "rename_column":
                    if target in df.columns:
                        df.rename(columns={target: new_value}, inplace=True)
                        changes_made.append(f"✅ Renamed column '{target}' to '{new_value}' in sheet '{sheet_name}'")
                    else:
                        broadcast_log(manager, f"⚠️ Column '{target}' not found in sheet '{sheet_name}'. Available columns: {list(df.columns)}", level="LOGS")
                
                elif mod_type == "replace_text":
                    changes_count = 0
                    target_lower = str(target).lower()
                    
                    broadcast_log(manager, f"🔍 Looking for text '{target}' in sheet '{sheet_name}'", level="LOGS")
                    
                    for col in df.columns:
                        if df[col].dtype == 'object':  # String columns
                            # Check each cell for the target text
                            for idx in df.index:
                                cell_value = str(df.at[idx, col])
                                if target_lower in cell_value.lower():
                                    broadcast_log(manager, f"🎯 Found '{target}' in cell [{idx}, '{col}']: '{cell_value}'", level="LOGS")
                                    # Replace with case-insensitive matching but preserve case in replacement
                                    new_cell_value = re.sub(re.escape(target), new_value, cell_value, flags=re.IGNORECASE)
                                    df.at[idx, col] = new_cell_value
                                    changes_count += 1
                                    broadcast_log(manager, f"✏️ Updated to: '{new_cell_value}'", level="LOGS")
                    
                    if changes_count > 0:
                        changes_made.append(f"✅ Replaced '{target}' with '{new_value}' in {changes_count} cells in sheet '{sheet_name}'")
                    else:
                        # Try partial matching
                        partial_matches = []
                        for col in df.columns:
                            if df[col].dtype == 'object':
                                for idx in df.index:
                                    cell_value = str(df.at[idx, col])
                                    if any(word in cell_value.lower() for word in target_lower.split()):
                                        partial_matches.append(f"'{cell_value}' in [{idx}, '{col}']")
                        
                        if partial_matches:
                            changes_made.append(f"⚠️ Text '{target}' not found exactly, but found similar: {partial_matches[:3]}")
                        else:
                            changes_made.append(f"⚠️ Text '{target}' not found in sheet '{sheet_name}'")
                
                elif mod_type == "change_value":
                    changes_count = 0
                    target_lower = str(target).lower()
                    
                    for col in df.columns:
                        mask = df[col].astype(str).str.lower().str.contains(target_lower, na=False)
                        if mask.any():
                            df.loc[mask, col] = new_value
                            changes_count += mask.sum()
                    
                    if changes_count > 0:
                        changes_made.append(f"✅ Changed '{target}' to '{new_value}' in {changes_count} cells in sheet '{sheet_name}'")
                    else:
                        changes_made.append(f"⚠️ Value '{target}' not found in sheet '{sheet_name}'")
                
                elif mod_type == "add_column":
                    df[new_value] = ""  # Add empty column
                    changes_made.append(f"✅ Added new column '{new_value}' to sheet '{sheet_name}'")
                
                elif mod_type == "remove_column":
                    if target in df.columns:
                        df.drop(columns=[target], inplace=True)
                        changes_made.append(f"✅ Removed column '{target}' from sheet '{sheet_name}'")
                    else:
                        changes_made.append(f"⚠️ Column '{target}' not found in sheet '{sheet_name}'")
                
                else:
                    changes_made.append(f"⚠️ Unknown modification type: {mod_type}")
                
                modified_sheets[sheet_name] = df
                
            except Exception as e:
                changes_made.append(f"❌ Error applying modification in sheet '{sheet_name}': {str(e)}")
                broadcast_log(manager, f"❌ Exception details: {e}", level="ERROR")
                import traceback
                traceback.print_exc()
        
        # Save the modified Excel file with proper formatting
        broadcast_log(manager, "Saving modified Excel file...", level="LOGS")
        
        def _save_excel_with_formatting():
            with pd.ExcelWriter(updated_report_path, engine='xlsxwriter') as writer:
                workbook = writer.book
                
                # Define formatting
                header_format = workbook.add_format({
                    'bold': True, 'text_wrap': True, 'valign': 'vcenter',
                    'border': 1, 'bg_color': '#D9E1F2'
                })
                cell_format = workbook.add_format({
                    'text_wrap': True, 'valign': 'top', 'border': 1
                })
                
                for sheet_name, df in modified_sheets.items():
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    worksheet = writer.sheets[sheet_name]
                    
                    # Apply formatting to headers
                    for col_num, column_name in enumerate(df.columns):
                        worksheet.write(0, col_num, column_name, header_format)
                    
                    # Apply formatting to data cells
                    for row_num in range(1, len(df) + 1):
                        for col_num in range(len(df.columns)):
                            cell_value = df.iloc[row_num-1, col_num]
                            if pd.isna(cell_value):
                                cell_value = ""
                            worksheet.write(row_num, col_num, cell_value, cell_format)
                    
                    # Auto-resize columns based on content
                    for idx, col in enumerate(df.columns):
                        # Calculate max width needed
                        max_len = max(
                            df[col].astype(str).map(len).max() if not df[col].empty else 0,
                            len(str(col))
                        ) + 3
                        # Set reasonable limits
                        max_len = min(max_len, 80)  # Max width of 80
                        max_len = max(max_len, 15)  # Min width of 15
                        worksheet.set_column(idx, idx, max_len)
        
        await loop.run_in_executor(None, _save_excel_with_formatting)
        
        # Prepare response
        if changes_made:
            changes_summary = "\n".join(changes_made)
            overall_summary = modification_plan.get("summary", "Excel file updated successfully")
            
            broadcast_log(manager, f"✅ Excel report updated successfully: {updated_report_path}", level="INFO")
            
            return f"""✅ Excel report updated!

{overall_summary}

Detailed changes made:
{changes_summary}

📊 Updated report saved as: {updated_report_path}

The modifications have been applied dynamically based on your request."""
        else:
            return "⚠️ No changes were made. Please check your request and try again."
            
    except Exception as e:
        broadcast_log(manager, f"❌ Error updating Excel file: {str(e)}", level="ERROR")
        return f"❌ Error updating Excel file: {str(e)}"

async def identify_and_load_files_async(uploaded_files: List[str]):
    """Automatically identify and load code and test files from uploaded files - async version"""
    broadcast_log(manager, "Identifying and loading files...", level="LOGS")
    
    # Find Excel file (test cases)
    excel_files = [x for x in uploaded_files if x.endswith(".xlsx")]
    if not excel_files:
        raise FileNotFoundError("No Excel (.xlsx) file found for test cases")
    
    test_path = excel_files[0]
    
    # List of code file extensions
    code_extensions = ['.py', '.js', '.java', '.cpp', '.c', '.cs', '.php', '.rb',
                      '.go', '.ts', '.jsx', '.tsx', '.kt', '.swift', '.rs', '.scala', '.r', '.m', '.h']
    
    # Filter code files
    code_files = [x for x in uploaded_files if any(x.endswith(ext) for ext in code_extensions)]
    
    if not code_files:
        raise FileNotFoundError("No code files found among uploaded files")
    
    # Load code contents
    code_contents = {}
    for code_path in code_files:
        try:
            async with aiofiles.open(code_path, "r", encoding='utf-8', errors='ignore') as f:
                code_contents[code_path] = await f.read()
        except Exception as e:
            broadcast_log(manager, f"Could not read {code_path}: {e}", level="ERROR")
    
    # Load test cases
    loop = asyncio.get_event_loop()
    test_cases = await loop.run_in_executor(None, pd.read_excel, test_path)
    
    broadcast_log(manager, f"✅ Successfully loaded {len(code_files)} code files and test cases", level="INFO")
    return code_contents, test_cases, code_files, test_path

def generate_per_test_comment(row):
    """Generate individual test case comments"""
    status = str(row["Status"]).lower()
    test_id = row.get('Test Case ID', 'Unknown')
    
    if status == "pass":
        return f"✅ Passed (Test ID: {test_id})"
    elif status == "fail":
        return f"❌ Failed (Test ID: {test_id})"
    else:
        return f"⏳ Pending (Test ID: {test_id})"

def autosize_columns(df, worksheet):
    """Auto-size Excel columns based on content"""
    for idx, col in enumerate(df.columns):
        max_len = max(df[col].astype(str).map(len).max(), len(str(col))) + 2
        worksheet.set_column(idx, idx, max_len)

def write_df(df, writer, sheet_name, format_obj):
    """Write dataframe to Excel with formatting"""
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    sheet = writer.sheets[sheet_name]
    
    # Write data with formatting
    for row_idx, (_, row) in enumerate(df.iterrows(), start=1):
        for col_idx, value in enumerate(row):
            clean_value = "" if pd.isna(value) else value
            sheet.write(row_idx, col_idx, clean_value, format_obj)
    
    # Write headers with formatting
    for col_idx, col in enumerate(df.columns):
        sheet.write(0, col_idx, col, format_obj)
    
    autosize_columns(df, sheet)

def parse_ai_feedback_to_structured_data(feedback_lines, analysis_type):
    """Parse AI feedback into structured data with proper grouping"""
    structured_data = []
    current_section = None
    section_content = []
    
    for line in feedback_lines:
        clean_line = re.sub(r'[*_`>#-]+', '', line).strip()
        if not clean_line:
            continue
            
        # Check if this is a section header (contains numbers and colons)
        if re.match(r'^\d+\..*:', clean_line) or clean_line.endswith(':'):
            # Save previous section if it exists
            if current_section and section_content:
                content_text = ' '.join(section_content).strip()
                if content_text:
                    structured_data.append({
                        'Section': current_section,
                        'Content': content_text
                    })
            
            # Start new section
            current_section = clean_line.replace(':', '').strip()
            section_content = []
        else:
            # Add content to current section
            if current_section:
                section_content.append(clean_line)
    
    # Don't forget the last section
    if current_section and section_content:
        content_text = ' '.join(section_content).strip()
        if content_text:
            structured_data.append({
                'Section': current_section,
                'Content': content_text
            })
    
    return structured_data

def write_improved_feedback_section(sheet_name, feedback_lines, writer, workbook, analysis_type):
    """Write AI feedback to Excel with improved formatting"""
    worksheet = workbook.add_worksheet(sheet_name)
    writer.sheets[sheet_name] = worksheet
    
    # Define formats
    main_header_format = workbook.add_format({
        'bold': True, 'font_size': 14, 'align': 'center', 'valign': 'vcenter',
        'border': 2, 'bg_color': '#4472C4', 'font_color': 'white'
    })
    section_header_format = workbook.add_format({
        'bold': True, 'align': 'left', 'valign': 'top', 'border': 1,
        'bg_color': '#D9E1F2', 'text_wrap': True
    })
    content_format = workbook.add_format({
        'text_wrap': True, 'valign': 'top', 'align': 'left', 'border': 1
    })
    
    # Parse feedback into structured data
    structured_data = parse_ai_feedback_to_structured_data(feedback_lines, analysis_type)
    
    row = 0
    
    # Write main header spanning both columns
    worksheet.merge_range(row, 0, row, 1, analysis_type.title(), main_header_format)
    row += 1
    
    # Write structured data
    for item in structured_data:
        section = item['Section']
        content = item['Content']
        
        # Ensure content is not empty
        if not content or content.isspace():
            content = "No additional details provided."
        
        worksheet.write(row, 0, section, section_header_format)
        worksheet.write(row, 1, content, content_format)
        row += 1
    
    # Set column widths
    worksheet.set_column(0, 0, 35)
    worksheet.set_column(1, 1, 80)

def parse_deployment_readiness(feedback_lines):
    """Parse AI feedback to determine deployment readiness"""
    combined = "\n".join(feedback_lines).lower()
    
    # Dynamic pattern matching for deployment decisions
    decision_patterns = [
        r'deployment decision[:\s]*([a-zA-Z]+)',
        r'decision[:\s]*([a-zA-Z]+)',
        r'ready for deployment[:\s]*([a-zA-Z]+)',
        r'deployment readiness[:\s]*([a-zA-Z]+)'
    ]
    
    for pattern in decision_patterns:
        matches = re.findall(pattern, combined)
        for match in matches:
            if 'yes' in match.lower():
                return "YES"
            elif 'no' in match.lower():
                return "NO"
    
    # Look for explicit YES/NO statements in decision contexts
    decision_lines = [line for line in feedback_lines if any(keyword in line.lower()
                     for keyword in ["deployment decision", "decision", "ready for deployment"])]
    
    for line in decision_lines:
        line_lower = line.lower()
        if 'yes' in line_lower and 'no' not in line_lower:
            return "YES"
        elif 'no' in line_lower and 'yes' not in line_lower:
            return "NO"
    
    return ""

def parse_code_quality(feedback_lines):
    """Parse AI feedback to determine code quality status"""
    combined = "\n".join(feedback_lines).lower()
    
    # Look for explicit quality indicators
    if any(indicator in combined for indicator in ["excellent", "high quality", "good quality", "well written"]):
        return "GOOD"
    elif any(indicator in combined for indicator in ["poor quality", "low quality", "bad", "critical issues"]):
        return "POOR"
    elif any(indicator in combined for indicator in ["average", "moderate", "acceptable"]):
        return "AVERAGE"
    
    # Default based on overall sentiment
    positive_words = ["clean", "maintainable", "readable", "secure", "efficient"]
    negative_words = ["issues", "problems", "vulnerabilities", "poor", "weak"]
    
    pos_count = sum(1 for word in positive_words if word in combined)
    neg_count = sum(1 for word in negative_words if word in combined)
    
    if neg_count > pos_count:
        return "POOR"
    elif pos_count > neg_count:
        return "GOOD"
    
    return "AVERAGE"

async def generate_ai_feedback_async(code, test_df, pass_pct, threshold):
    """Generate AI feedback for test and code evaluation with dynamic prompts - async version"""
    broadcast_log(manager, "Generating AI feedback for evaluation...", level="LOGS")
    
    # Dynamic test evaluation prompt
    test_prompt = f"""
You are a professional test evaluation specialist. Analyze the following test results for deployment readiness.
### Test Results Analysis:
{test_df.to_markdown()}
Test Statistics:
- Pass Rate: {pass_pct:.2f}%
- Required Threshold: {threshold}%
Please provide a structured evaluation with the following sections, you must strictly use the headers provided below:
1. Test Coverage Analysis: Summarize overall test comprehensiveness, test case design quality and coverage of edge cases
2. Test Results Summary: Provide key insights from test pass/fail results and any significant trends.
3. Critical Issues: Identify the most critical issue found, including missing test scenarios, failed tests, design flaws or uncovered edge cases.
4. Recommendations: Offer one actionable recommendation that addresses the most impactful improvement to the test suite or strategy.
5. Test-Based Deployment Decision: State a clear YES or NO on whether the product is ready for deployment based on the test results, with a brief justification.
"""
    # Code Quality Evaluation Prompt - STRUCTURED TO MATCH TEST EVALUATION FORMAT
    code_prompt = f"""
You are a professional code reviewer and security analyst. Analyze the following code for deployment readiness and quality.
### Code to Review:
```
{code}
```
Please provide a structured evaluation with the following sections, you must strictly use the headers provided below:
1. Code Quality Analysis: Summarize overall code structure, readability, maintainability, and adherence to coding standards and best practices.
2. Security Assessment Summary: Provide key insights on security vulnerabilities, authentication/authorization mechanisms, data handling, and potential security risks.
3. Critical Issues: Identify the most critical code issue found, including security vulnerabilities, structural problems, performance bottlenecks, or major violations of best practices.
4. Recommendations: Offer one actionable recommendation that addresses the most impactful improvement to code quality, security, or maintainability.
5. Code-Based Deployment Decision: State a clear YES or NO on whether the code is ready for deployment based on quality and security assessment, with a brief justification.
"""
    
    try:
        broadcast_log(manager, "Generating test evaluation feedback...", level="LOGS")
        loop = asyncio.get_event_loop()
        test_response_text = await loop.run_in_executor(None, _litellm_generate, test_prompt)
        
        broadcast_log(manager, "Generating code evaluation feedback...", level="LOGS")
        code_response_text = await loop.run_in_executor(None, _litellm_generate, code_prompt)
        
        test_feedback = [line.strip() for line in test_response_text.split("\n") if line.strip()]
        code_feedback = [line.strip() for line in code_response_text.split("\n") if line.strip()]
        
        broadcast_log(manager, "✅ AI feedback generation completed", level="LOGS")
        return test_feedback, code_feedback
        
    except Exception as e:
        error_msg = f"Error generating AI feedback: {e}"
        broadcast_log(manager, f"❌ {error_msg}", level="ERROR")
        return [error_msg], [error_msg]

def join_code_by_filename(code_contents: Dict[str, str]) -> str:
    """Join multiple code files into a single string"""
    combined_code = ""
    for filename, content in code_contents.items():
        combined_code += f"\n\n### File: {filename} ###\n{content}\n"
    return combined_code

def analyze_pass_rate(test_df):
    """Analyze test pass rate from the test dataframe"""
    total = len(test_df)
    passed = (test_df["Status"].str.lower() == "pass").sum()
    failed = (test_df["Status"].str.lower() == "fail").sum()
    pending = total - passed - failed
    pass_percentage = (passed / total) * 100 if total > 0 else 0
    return total, passed, failed, pending, pass_percentage

@tool
async def deployment_evaluation_tool(user_input: str, uploaded_files: List[str]) -> Dict[str, Any]:
    """
    Completely dynamic tool to evaluate code and test cases for deployment readiness - async version.
    No hardcoded thresholds or criteria - everything is determined dynamically.
    """
    try:
        broadcast_log(manager, "🚀 Dynamic Deployment Agent - Fully Flexible Version", level="INFO")
        broadcast_log(manager, "🔡 Running deployment evaluation...", level="INFO")
        
        # Load files automatically
        broadcast_log(manager, "📂 Loading files...", level="LOGS")
        code, test_df, code_path, test_path = await identify_and_load_files_async(uploaded_files)
        broadcast_log(manager, "✅ Successfully loaded files", level="INFO")
        
        code = join_code_by_filename(code)
        
        # Analyze test results
        broadcast_log(manager, "📊 Analyzing test results...", level="LOGS")
        total, passed, failed, pending, pass_pct = analyze_pass_rate(test_df)
        
        # Dynamically determine threshold based on context
        if pass_pct >= 95:
            threshold = 95
        elif pass_pct >= 85:
            threshold = 85
        elif pass_pct >= 70:
            threshold = 70
        else:
            threshold = 60
        
        broadcast_log(manager, f"   Total Tests: {total}", level="LOGS")
        broadcast_log(manager, f"   Passed: {passed} | Failed: {failed} | Pending: {pending}", level="LOGS")
        broadcast_log(manager, f"   Pass Rate: {pass_pct:.2f}%", level="LOGS")
        broadcast_log(manager, f"   Dynamic Threshold: {threshold}%", level="LOGS")
        
        # Generate AI evaluation
        broadcast_log(manager, "🤖 Generating AI evaluation...", level="INFO")
        test_feedback, code_feedback = await generate_ai_feedback_async(code, test_df, pass_pct, threshold)
        
        # Parse AI decisions dynamically
        test_deployment_decision = parse_deployment_readiness(test_feedback)
        code_deployment_decision = parse_deployment_readiness(code_feedback)
        code_quality_status = parse_code_quality(code_feedback)
        
        broadcast_log(manager, f"   Test-Based AI Decision: {test_deployment_decision}", level="LOGS")
        broadcast_log(manager, f"   Code-Based AI Decision: {code_deployment_decision}", level="LOGS")
        broadcast_log(manager, f"   Code Quality Assessment: {code_quality_status}", level="LOGS")
        
        # Add evaluation comments to test dataframe
        test_df["Deployment_Evaluation"] = test_df.apply(generate_per_test_comment, axis=1)
        
        # Create dynamic summary
        summary_df = pd.DataFrame({
            "Metric": [
                "Total Tests",
                "Passed Tests", 
                "Failed Tests",
                "Pending Tests",
                "Pass Rate (%)",
                "Dynamic Threshold (%)",
                "Code Quality Status",
                "Test-Based Deployment Decision",
                "Code-Based Deployment Decision"
            ],
            "Value": [
                total,
                passed,
                failed,
                pending,
                round(pass_pct, 2),
                threshold,
                code_quality_status,
                test_deployment_decision,
                code_deployment_decision
            ]
        })
        
        # Generate output file
        output_file = "deployment_evaluation_report.xlsx"
        broadcast_log(manager, f"📄 Generating report: {output_file}", level="INFO")
        
        try:
            def _save_excel_report():
                with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
                    workbook = writer.book
                    
                    # Define table format
                    table_format = workbook.add_format({
                        'border': 1, 'valign': 'vcenter',
                        'text_wrap': True, 'align': 'left'
                    })
                    
                    # Write all sheets with improved formatting
                    write_df(summary_df, writer, "Evaluation Summary", table_format)
                    write_df(test_df, writer, "Test Results Details", table_format)
                    write_improved_feedback_section("Test Evaluation", test_feedback, writer, workbook, "Test Analysis")
                    write_improved_feedback_section("Code Evaluation", code_feedback, writer, workbook, "Code Analysis")
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _save_excel_report)
            
            broadcast_log(manager, "✅ Report successfully generated!", level="INFO")
            
        except Exception as e:
            broadcast_log(manager, f"❌ Error generating report: {e}", level="ERROR")
            return {
                "status": "error",
                "summary": None,
                "output_file": None,
                "message": f"Error generating report: {str(e)}"
            }
        
        # Display final results
        summary_string = f"""
======================================
🚀 DYNAMIC EVALUATION SUMMARY
======================================
{summary_df.to_string(index=False)}
======================================
📊 Full report saved to: {os.path.abspath(output_file)}
        """
        
        broadcast_log(manager, "✅ Deployment evaluation completed successfully", level="INFO")
        
        return {
            "status": "success",
            "summary": summary_string,
            "output_file": output_file,
            "message": "Deployment evaluation completed successfully with dynamic analysis"
        }
        
    except Exception as e:
        broadcast_log(manager, f"❌ Error during deployment evaluation: {str(e)}", level="ERROR")
        return {
            "status": "error",
            "summary": None,
            "output_file": None,
            "message": f"Error during deployment evaluation: {str(e)}"
        }

async def main_with_memory_async(user_input: str, uploaded_files: List[str], session_id: str = "default"):
    """
    Enhanced main function with conversation memory that handles contextual interactions - async version.
    Perfect for chatbot integration - remembers previous conversations and context.
    """
    broadcast_log(manager, f"🚀 Dynamic Deployment Agent - Memory-Enhanced Session: {session_id}", level="INFO")
    
    # Step 1: Classify user intent with conversation context
    intent_result = await classify_user_intent_with_context.ainvoke({
        "user_input": user_input,
        "session_id": session_id,
        "uploaded_files": uploaded_files
    })
    
    response_text = ""
    output_file = None
    
    # Handle different intent types with memory awareness
    if intent_result["intent"] == "GREETING":
        response_text = f"""
👋 {intent_result.get('suggested_response', 'Hello! Nice to meet you!')}

I'm your deployment evaluation assistant. I can help you with:
  ✅ Evaluate code and test cases for deployment readiness
  📊 Generate comprehensive deployment reports  
  🔧 Update existing evaluation reports
  📝 Modify report content, thresholds, or formatting

What would you like me to help you with today?
        """
    
    elif intent_result["intent"] == "HELP":
        # Get conversation context for personalized help
        history = await get_conversation_history_async(session_id)
        context_help = ""
        
        if history:
            recent_actions = [turn.get('action') for turn in history[-2:] if turn.get('action')]
            if 'evaluation' in str(recent_actions).lower():
                context_help = "\n💡 I see you were working on evaluations. You can now update your reports or run new evaluations."
            elif 'update' in str(recent_actions).lower():
                context_help = "\n💡 I see you were updating reports. You can continue with more updates or run new evaluations."
        
        response_text = f"""
🤖 {intent_result.get('suggested_response', 'I am here to help!')}

Here's what I can do for you:

🚀 **Deployment Evaluation:**
  • "Evaluate my code for deployment"
  • "Check if my application is ready for production"  
  • "Review my code and test cases for deployment readiness"

🔧 **Report Updates:**
  • "Change the column names in the summary"
  • "Update the pass threshold to 90%"
  • "Rename 'Test Coverage Analysis' to 'Test Analysis'"
  • "Change the sheet name from X to Y"

📂 **File Requirements:**
  • Upload your code files (.py, .js, .java, etc.)
  • Upload test results in Excel format (.xlsx)

{context_help}

Just tell me what you'd like to do, and I'll take care of the rest!"""
    
    elif intent_result["intent"] == "CONTINUE_EVALUATION":
        broadcast_log(manager, "✅ Continuing previous evaluation request with uploaded files...", level="LOGS")
        
        # Find the original evaluation request from history
        history = await get_conversation_history_async(session_id)
        original_request = "Evaluate code for deployment"
        
        for turn in reversed(history):
            if any(keyword in turn['user_input'].lower() for keyword in ['evaluate', 'deployment', 'check', 'analyze']):
                original_request = turn['user_input']
                break
        
        try:
            result = await deployment_evaluation_tool.ainvoke({
                "user_input": original_request,
                "uploaded_files": uploaded_files
            })
            
            if result["status"] == "success":
                response_text = f"""✅ **Continuing Your Evaluation Request**

I remember you asked: "{original_request}"

Now that you've uploaded the files, here's your evaluation:

{result["summary"] or "✅ Evaluation completed successfully!"}"""
                output_file = result["output_file"]
            else:
                response_text = f"""
❌ **Evaluation Failed**

I tried to continue your previous request: "{original_request}"

Error: {result["message"]}

Please check:
  • Files are properly formatted
  • Code files have supported extensions (.py, .js, etc.)
  • Excel file contains test case data
  • All files are accessible
                """
                
        except Exception as e:
            response_text = f"""
❌ **Error Continuing Evaluation**

I tried to continue your previous request: "{original_request}"

Technical details: {str(e)}

Please try re-uploading your files and making the request again.
            """
    
    elif intent_result["intent"] == "CONTINUE_UPDATE":
        broadcast_log(manager, "✅ Continuing previous update request with uploaded files...", level="LOGS")
        
        # Find the original update request from history
        history = await get_conversation_history_async(session_id)
        original_request = "Update report"
        
        for turn in reversed(history):
            if any(keyword in turn['user_input'].lower() for keyword in ['update', 'change', 'modify', 'rename']):
                original_request = turn['user_input']
                break
        
        # Check if report exists or needs to be generated first
        report_path = "deployment_evaluation_report.xlsx"
        
        if not os.path.exists(report_path) and uploaded_files:
            broadcast_log(manager, "📊 No existing report found. Generating initial evaluation first...", level="LOGS")
            try:
                eval_result = await deployment_evaluation_tool.ainvoke({
                    "user_input": "Generate deployment evaluation report",
                    "uploaded_files": uploaded_files
                })
                
                if eval_result["status"] != "success":
                    response_text = f"""
❌ **Failed to Generate Initial Report**

I tried to continue your update request: "{original_request}"

But I needed to generate an evaluation report first, and that failed.

Error: {eval_result['message']}

Please ensure your files are properly formatted and try again.
                    """
                    await add_to_conversation_history_async(session_id, user_input, response_text, uploaded_files, intent_result["intent"], "failed_evaluation")
                    return response_text, output_file
                    
            except Exception as e:
                response_text = f"""
❌ **Error Generating Initial Report**

I tried to continue your update request: "{original_request}"

Technical details: {str(e)}

Please try uploading your files and making a basic evaluation request first.
                """
                await add_to_conversation_history_async(session_id, user_input, response_text, uploaded_files, intent_result["intent"], "failed_evaluation")
                return response_text, output_file
        
        # Now proceed with the update
        current_content = f"Continuing previous update request: {original_request}"
        
        try:
            updated_content = await update_response.ainvoke({
                "file_names": uploaded_files + [report_path] if os.path.exists(report_path) else uploaded_files,
                "query": original_request,
                "content": current_content
            })
            
            output_file = "deployment_evaluation_report_updated.xlsx"
            
            response_text = f"""✅ **Continuing Your Update Request**

I remember you asked: "{original_request}"

Now that you've uploaded the files, here's the result:

{updated_content}"""
            
        except Exception as e:
            response_text = f"""
❌ **Error Continuing Update**

I tried to continue your previous request: "{original_request}"

Technical details: {str(e)}

Please try being more specific about what to update or re-upload your files.
            """
    
    elif intent_result["intent"] == "INVALID":
        # Provide context-aware invalid response
        history = await get_conversation_history_async(session_id)
        context_suggestion = ""
        
        if history:
            recent_intents = [turn.get('intent') for turn in history[-2:] if turn.get('intent')]
            if 'EVALUATE' in recent_intents or 'CONTINUE_EVALUATION' in recent_intents:
                context_suggestion = "\n💡 I see you were working on evaluations. Perhaps you meant to update your report or run another evaluation?"
        
        response_text = f"""
🤔 I'm not sure how to help with that request.

Reasoning: {intent_result['reasoning']}

I specialize in deployment evaluations. Here's what I can help you with:

🚀 **Evaluation Examples:**
  • "Evaluate my code for deployment"
  • "Check if my application is ready for production"
  • "Review my code and test cases for deployment readiness"

🔧 **Update Examples:**  
  • "Change the column names in the summary"
  • "Update the pass threshold"
  • "Rename Test Coverage Analysis to Test"
  • "Change the sheet name from X to Y"

{context_suggestion}

Could you rephrase your request or let me know what specific deployment task you need help with?
        """
    
    # Handle EVALUATE and UPDATE intents (similar to original logic but with memory)
    elif intent_result["intent"] == "EVALUATE":
        broadcast_log(manager, "✅ Routing to dynamic deployment evaluation tool...", level="LOGS")
        
        if not uploaded_files:
            response_text = """
📂 **Files Required for Evaluation**

To evaluate your code for deployment, I need:
  • Code files (.py, .js, .java, .cpp, etc.)
  • Test results in Excel format (.xlsx)

Please upload your files and I'll remember your request to evaluate them for deployment!
            """
        else:
            try:
                result = await deployment_evaluation_tool.ainvoke({
                    "user_input": user_input,
                    "uploaded_files": uploaded_files
                })
                
                if result["status"] == "success":
                    response_text = result["summary"] or "✅ Evaluation completed successfully!"
                    output_file = result["output_file"]
                else:
                    response_text = f"""
❌ **Evaluation Failed**

Error: {result["message"]}

Please check:
  • Files are properly formatted
  • Code files have supported extensions
  • Excel file contains test case data
  • All files are accessible

Try uploading your files again and request another evaluation.
                    """
                    
            except Exception as e:
                response_text = f"""
❌ **Unexpected Error During Evaluation**

Technical details: {str(e)}

Please try:
  1. Re-uploading your files
  2. Checking file formats (.py, .xlsx, etc.)
  3. Requesting evaluation again

If the problem persists, please check your file contents and try again.
                """
        
    elif intent_result["intent"] == "UPDATE":
        broadcast_log(manager, "✅ Routing to dynamic update response tool...", level="LOGS")
        
        report_path = "deployment_evaluation_report.xlsx"
        
        if not os.path.exists(report_path):
            if not uploaded_files:
                response_text = """
📊 **No Existing Report Found**

To update a report, I need either:
  1. An existing evaluation report, OR
  2. Your code and test files to generate a new report first

Please upload your code files (.py, .js, etc.) and test results (.xlsx), and I'll generate a report that you can then update!
                """
            else:
                # Generate evaluation first, then apply update
                broadcast_log(manager, "📊 No existing report found. Generating initial evaluation...", level="LOGS")
                try:
                    result = await deployment_evaluation_tool.ainvoke({
                        "user_input": "Generate deployment evaluation report",
                        "uploaded_files": uploaded_files
                    })
                    
                    if result["status"] == "success":
                        # Now apply the update
                        try:
                            updated_content = await update_response.ainvoke({
                                "file_names": uploaded_files + [result["output_file"]],
                                "query": user_input,
                                "content": f"Newly generated report: {result['output_file']}"
                            })
                            
                            response_text = f"""✅ **Generated Report and Applied Update**

I first generated your evaluation report, then applied your update request:

{updated_content}"""
                            output_file = "deployment_evaluation_report_updated.xlsx"
                            
                        except Exception as e2:
                            response_text = f"""✅ **Report Generated Successfully**

{result['summary']}

However, I encountered an issue applying your update: {str(e2)}

You can now make update requests to modify the generated report.
                            """
                            output_file = result["output_file"]
                    else:
                        response_text = f"""
❌ **Failed to Generate Initial Report**

Error: {result['message']}

To proceed with updates, I need a working evaluation report. Please:
  1. Check your uploaded files
  2. Request a basic evaluation first
  3. Then ask for specific updates
                        """
                        
                except Exception as e:
                    response_text = f"""
❌ **Error Generating Initial Evaluation**

Technical details: {str(e)}

Please:
  1. Upload your code and test files
  2. Request evaluation first: "Evaluate my code"
  3. Then request updates: "Change column X to Y"
                    """
        else:
            # Report exists, proceed with update
            try:
                current_content = f"Existing report found at: {report_path}"
                updated_content = await update_response.ainvoke({
                    "file_names": uploaded_files + [report_path],
                    "query": user_input,
                    "content": current_content
                })
                
                response_text = updated_content
                output_file = "deployment_evaluation_report_updated.xlsx"
                
            except Exception as e:
                response_text = f"""
❌ **Error During Update**

Request: "{user_input}"
Technical details: {str(e)}

Please try:
  1. Being more specific about what to change
  2. Using exact column/sheet names from the report
  3. Simplifying your update request

Example: "Change Status to Test Status" or "Rename sheet Summary to Overview"
                """
    
    # Add conversation to memory
    action = intent_result["intent"].lower()
    if output_file:
        action += "_with_file"
        
    await add_to_conversation_history_async(session_id, user_input, response_text, uploaded_files, intent_result["intent"], action)
    
    return response_text, output_file

# Async wrapper functions
async def main_async(user_input: str, uploaded_files: List[str], session_id: str = "default"):
    """
    Async main function wrapper that calls the memory-enhanced version.
    This maintains backward compatibility while adding async features.
    """
    response_text, output_file = await main_with_memory_async(user_input, uploaded_files, session_id)
    return response_text, output_file

# Sync wrapper for backward compatibility
def main(user_input: str, uploaded_files: List[str], session_id: str = "default"):
    """
    Main function wrapper that calls the async version using asyncio.run.
    This maintains backward compatibility while using async internally.
    """
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context, so we need to handle this differently
        import nest_asyncio
        nest_asyncio.apply()
        return asyncio.run(main_async(user_input, uploaded_files, session_id))
    except RuntimeError:
        # No running loop, safe to use asyncio.run
        return asyncio.run(main_async(user_input, uploaded_files, session_id))

# Async session management functions
async def create_new_session_async(session_id: str = None):
    """Create a new conversation session - async version"""
    if not session_id:
        session_id = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    if session_id in conversation_memory:
        await clear_memory_async(session_id)
    
    conversation_memory[session_id] = []
    broadcast_log(manager, f"🆕 Created new session: {session_id}", level="INFO")
    return session_id

async def list_active_sessions_async():
    """List all active conversation sessions - async version"""
    sessions = list(conversation_memory.keys())
    broadcast_log(manager, f"📋 Active sessions: {sessions}", level="INFO")
    return sessions

async def get_session_summary_async(session_id: str):
    """Get a summary of a conversation session - async version"""
    history = await get_conversation_history_async(session_id)
    if not history:
        return f"No conversation history found for session: {session_id}"
    
    summary = f"Session {session_id} Summary:\n"
    summary += f"  • Total interactions: {len(history)}\n"
    
    intents = [turn.get('intent') for turn in history if turn.get('intent')]
    intent_counts = {intent: intents.count(intent) for intent in set(intents)}
    
    summary += f"  • Intent breakdown: {intent_counts}\n"
    summary += f"  • Started: {history[0]['timestamp']}\n"
    summary += f"  • Last activity: {history[-1]['timestamp']}\n"
    
    return summary

