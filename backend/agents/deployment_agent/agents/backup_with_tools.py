import os
import pandas as pd
import google.generativeai as genai
from dotenv import load_dotenv
import re
import glob
from pathlib import Path
from typing import List, Dict, Any
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
import json

load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


def identify_and_load_files(uploaded_files : List[str]):
    """
    Automatically identify and load code and test files from a directory
    """
   
    # Find Excel file (test cases)
    excel_files = [x for x in uploaded_files if x.endswith(".xlsx")]
    if not excel_files:
        raise FileNotFoundError("No Excel (.xlsx) file found for test cases")
   
   
    test_path = excel_files[0]  # Take the first Excel file found
   
    # List of code file extensions
    code_extensions = ['.py', '.js', '.java', '.cpp', '.c', '.cs', '.php', '.rb',
                    '.go', '.ts', '.jsx', '.tsx', '.kt', '.swift', '.rs', '.scala', '.r', '.m', '.h']
    # Filter code files
    code_files = [x for x in uploaded_files if any(x.endswith(ext) for ext in code_extensions)]
    print("we reached here")
    if not code_files:
        raise FileNotFoundError("No code files found among uploaded files")
    # Now you can loop over `code_files` to load and process each code file
    code_contents = {}
 
    for code_path in code_files:
        try:
            with open(code_path, "r", encoding='utf-8', errors='ignore') as f:
                code_contents[code_path] = f.read()
        except Exception as e:
            print(f"Could not read {code_path}: {e}")
 
 
    # Load test cases
    test_cases = pd.read_excel(test_path)
    return code_contents, test_cases, code_files, test_path
 
 
def generate_per_test_comment(row):
    """
    Generate individual test case comments
    """
    status = str(row["Status"]).lower()
    test_id = row.get('Test Case ID', 'Unknown')
   
    if status == "pass":
        return f"✅ Passed (Test ID: {test_id})"
    elif status == "fail":
        return f"❌ Failed (Test ID: {test_id})"
    else:
        return f"⏳ Pending (Test ID: {test_id})"

def classify_intent(user_input):
    """
    Classify user intent using Gemini AI
    """
    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = f"""
You are an assistant that classifies user intent. Is the user asking to evaluate code and test cases for deployment readiness?
Reply with only "YES" or "NO".
User Input: "{user_input}"
"""
    try:
        response = model.generate_content(prompt)
        text = response.text.strip().upper()
        print("📤 Gemini classification response:", repr(text))
        return text.startswith("YES")
    except Exception as e:
        print(f"❌ Error classifying intent: {e}")
        return False

def autosize_columns(df, worksheet):
    """
    Auto-size Excel columns based on content
    """
    for idx, col in enumerate(df.columns):
        max_len = max(df[col].astype(str).map(len).max(), len(str(col))) + 2
        worksheet.set_column(idx, idx, max_len)

def write_df(df, writer, sheet_name, format_obj):
    """
    Write dataframe to Excel with formatting
    """
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
    """
    Parse AI feedback into structured data with proper grouping and no excessive detail rows
    """
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
    """
    Write AI feedback to Excel with improved formatting and merged headers
    """
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
    """
    Parse AI feedback to determine deployment readiness - looks for final decision only
    """
    combined = "\n".join(feedback_lines).lower()
   
    # Look specifically for deployment decision sections
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

def parse_code_quality(feedback_lines):
    """
    Parse AI feedback to determine code quality status
    """
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
 
def generate_ai_feedback(code, test_df, pass_pct, threshold):
    """
    Generate separate AI feedback for test and code evaluation
    """
    model = genai.GenerativeModel("gemini-2.5-flash")
    # Test Cases Evaluation Prompt
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
        # Generate both evaluations
        test_response = model.generate_content(test_prompt)
        code_response = model.generate_content(code_prompt)
        test_feedback = [line.strip() for line in test_response.text.split("\n") if line.strip()]
        code_feedback = [line.strip() for line in code_response.text.split("\n") if line.strip()]
        return test_feedback, code_feedback
    except Exception as e:
        error_msg = f"Error generating AI feedback: {e}"
        return [error_msg], [error_msg]
 
def join_code_by_filename(code_contents: Dict[str, str]) -> str:
    """
    Join multiple code files into a single string, separated by filename headers.
    """
    combined_code = ""
    for filename, content in code_contents.items():
        combined_code += f"\n\n### File: {filename} ###\n{content}\n"
    return combined_code
 
def analyze_pass_rate(test_df):
    """
    Analyze test pass rate from the test dataframe
    """
    total = len(test_df)
    passed = (test_df["Status"].str.lower() == "pass").sum()
    failed = (test_df["Status"].str.lower() == "fail").sum()
    pending = total - passed - failed
    pass_percentage = (passed / total) * 100 if total > 0 else 0
    return total, passed, failed, pending, pass_percentage
 
 
def main(user_input: str, uploaded_files : List[str]):
    """
    Main function to orchestrate the deployment evaluation process
    """
    print("🚀 Enhanced Deployment Agent - API Ready Version")
    print("=" * 60)
    print("💡 Tip: Type 'exit' at any time to quit the application")
   
    # Loop until valid intent or exit
   
    user_input = user_input
   
    # Check for exit command
    if user_input.lower() in ['exit', 'quit', 'q']:
        print("👋 Goodbye! Exiting Deployment Agent.")
        return None, None
   
    # Validate intent
    print("\n🔍 Validating request...")
    if classify_intent(user_input):
        print("✅ Valid deployment evaluation request detected!")
       
    else:
        print("❌ This doesn't appear to be a valid deployment evaluation request.")
        print("Please describe what you want to evaluate for deployment readiness.")
        print("Examples:")
        print("  - 'Evaluate my code for deployment'")
        print("  - 'Check if my application is ready for production'")
        print("  - 'Review my code and test cases for deployment readiness'")
        print("  - Type 'exit' to quit")
           
   
    # Get directory path
   
    directory_path = uploaded_files
   
    # Check for exit command
   
    if directory_path:
        pass
    else:
        print("❌ Please enter a valid directory path.")
        return None, None
   
    try:
        # Load files automatically
        print("\n📁 Loading files...")
        code, test_df, code_path, test_path = identify_and_load_files(directory_path)
        print(f"✅ Successfully loaded files:")
 
       
    except Exception as e:
        print(f"❌ Error loading files: {e}")
        return None, None
    code = join_code_by_filename(code)
    # Analyze test results
    print("\n📊 Analyzing test results...")
    total, passed, failed, pending, pass_pct = analyze_pass_rate(test_df)
    threshold = 80
   
    print(f"   Total Tests: {total}")
    print(f"   Passed: {passed} | Failed: {failed} | Pending: {pending}")
    print(f"   Pass Rate: {pass_pct:.2f}%")
   
    # Generate AI evaluation
    print("\n🤖 Generating AI evaluation...")
    test_feedback, code_feedback = generate_ai_feedback(code, test_df, pass_pct, threshold)
   
    # Parse AI decisions from both feedbacks - ONLY from final decision rows
    test_deployment_decision = parse_deployment_readiness(test_feedback)
    code_deployment_decision = parse_deployment_readiness(code_feedback)
    code_quality_status = parse_code_quality(code_feedback)
   
    print(f"   Test-Based AI Decision: {test_deployment_decision}")
    print(f"   Code-Based AI Decision: {code_deployment_decision}")
    print(f"   Code Quality Assessment: {code_quality_status}")
   
    # Add evaluation comments to test dataframe
    test_df["Deployment_Evaluation"] = test_df.apply(generate_per_test_comment, axis=1)
   
    # Create comprehensive summary - VALUES REFERENCE ONLY FINAL DECISIONS
    summary_df = pd.DataFrame({
        "Metric": [
            "Total Tests",
            "Passed Tests",
            "Failed Tests",
            "Pending Tests",
            "Pass Rate (%)",
            "Code Quality Status",
            "Test Quality Deployment Decision",
            "Code Quality Deployment Decision"
        ],
        "Value": [
            total,
            passed,
            failed,
            pending,
            round(pass_pct, 2),
            code_quality_status,
            test_deployment_decision,  # References ONLY the final test decision
            code_deployment_decision   # References ONLY the final code decision
        ]
    })
   
    # Generate output file
    output_file = "deployment_evaluation_report.xlsx"
    print(f"\n📝 Generating report: {output_file}")
   
    try:
        with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
            workbook = writer.book
           
            # Define table format
            table_format = workbook.add_format({
                'border': 1, 'valign': 'vcenter',
                'text_wrap': True, 'align': 'left'
            })
           
            # Write all sheets with improved formatting
            write_df(summary_df, writer, "Test Case Summary", table_format)
            write_df(test_df, writer, "Detailed Test Results", table_format)
            write_improved_feedback_section("Test Case Evaluation", test_feedback, writer, workbook, "Test Coverage Analysis")
            write_improved_feedback_section("Code Quality Evaluation", code_feedback, writer, workbook, "Code Quality Assessment")
       
        print(f"✅ Report successfully generated!")
       
    except Exception as e:
        print(f"❌ Error generating report: {e}")
        return summary_df, None
   
    # Display final results
    summar_string = f"""
===================================================================================================
📋 EVALUATION SUMMARY
===================================================================================================
{summary_df.to_string(index=False)}
===================================================================================================
📁 Full report saved to: {os.path.abspath(output_file)}
   """
    return summar_string, output_file


# LangChain Tools Implementation
@tool
def deployment_evaluation_tool(user_input: str, uploaded_files: List[str]) -> Dict[str, Any]:
    """
    Tool to evaluate code and test cases for deployment readiness.
    
    Args:
        user_input: User's request for deployment evaluation
        uploaded_files: List of file paths to analyze (must include code files and Excel test cases)
    
    Returns:
        Dictionary containing evaluation summary and output file path
    """
    try:
        summary_result, output_file = main(user_input, uploaded_files)
        
        return {
            "status": "success" if summary_result is not None else "failed",
            "summary": summary_result,
            "output_file": output_file,
            "message": "Deployment evaluation completed successfully" if summary_result is not None else "Deployment evaluation failed"
        }
    except Exception as e:
        return {
            "status": "error",
            "summary": None,
            "output_file": None,
            "message": f"Error during deployment evaluation: {str(e)}"
        }


@tool
def update_evaluation_output_tool(previous_output: Dict[str, Any], update_instructions: str) -> Dict[str, Any]:
    """
    Tool to update the previous deployment evaluation output based on new instructions.
    
    Args:
        previous_output: The previous output from deployment_evaluation_tool
        update_instructions: Instructions on how to modify the output
    
    Returns:
        Updated dictionary with modified evaluation results
    """
    try:
        if previous_output.get("status") != "success":
            return {
                "status": "error",
                "summary": None,
                "output_file": None,
                "message": "Cannot update failed or invalid previous output"
            }
        
        # Create updated output based on instructions
        updated_output = previous_output.copy()
        
        # Parse update instructions and modify accordingly
        instructions_lower = update_instructions.lower()
        
        if "change status" in instructions_lower or "modify decision" in instructions_lower:
            # Example: Update deployment decisions
            if "approve" in instructions_lower or "yes" in instructions_lower:
                updated_summary = previous_output["summary"].replace("NO", "YES")
                updated_output["summary"] = updated_summary
                updated_output["message"] = "Deployment evaluation updated - Status changed to approved"
            elif "reject" in instructions_lower or "no" in instructions_lower:
                updated_summary = previous_output["summary"].replace("YES", "NO")
                updated_output["summary"] = updated_summary
                updated_output["message"] = "Deployment evaluation updated - Status changed to rejected"
        
        elif "add note" in instructions_lower or "add comment" in instructions_lower:
            # Add additional notes to the summary
            note_start = instructions_lower.find("note:") + 5 if "note:" in instructions_lower else len(instructions_lower)
            additional_note = update_instructions[note_start:].strip()
            
            if additional_note:
                updated_output["summary"] = previous_output["summary"] + f"\n\n📝 Additional Note: {additional_note}"
                updated_output["message"] = "Deployment evaluation updated with additional notes"
        
        elif "regenerate" in instructions_lower or "refresh" in instructions_lower:
            # Mark for regeneration (would typically trigger a new evaluation)
            updated_output["message"] = "Marked for regeneration - recommend running new evaluation"
        
        else:
            # Generic update
            updated_output["summary"] = previous_output["summary"] + f"\n\n🔄 Update Applied: {update_instructions}"
            updated_output["message"] = "Deployment evaluation updated with custom modifications"
        
        return updated_output
        
    except Exception as e:
        return {
            "status": "error",
            "summary": None,
            "output_file": None,
            "message": f"Error updating evaluation output: {str(e)}"
        }


# Tool binding function for LangGraph/LangChain integration
def bind_deployment_tools():
    """
    Function to bind the deployment evaluation tools for use with LangChain/LangGraph
    
    Returns:
        List of bound tools ready for use in LangChain/LangGraph workflows
    """
    tools = [deployment_evaluation_tool, update_evaluation_output_tool]
    return tools


# Example usage with LangChain model binding
def create_deployment_agent():
    """
    Create a deployment evaluation agent with bound tools
    This function shows how to integrate with LangChain models
    """
    try:
        from langchain_openai import ChatOpenAI  # or your preferred model
        
        # Initialize your preferred LLM
        # llm = ChatOpenAI(model="gpt-4")  # Uncomment and configure as needed
        
        # Bind tools to the model
        tools = bind_deployment_tools()
        # llm_with_tools = llm.bind_tools(tools)  # Uncomment when using with actual LLM
        
        print("✅ Deployment agent created with bound tools")
        return tools  # Return tools for now, replace with llm_with_tools when using actual LLM
        
    except ImportError as e:
        print(f"⚠️ LangChain model import failed: {e}")
        print("Tools are available for manual binding")
        return bind_deployment_tools()


def run_evaluation():
    shared.summary_result, shared.output_path = main()
    if shared.summary_result is not None:
        print(f"\n🎉 Evaluation completed successfully!")
        print(f"📊 Summary data available in variable 'summary_result'")
        if shared.output_path:
            print(f"📄 Detailed report saved to: {shared.output_path}")
    else:
        print(f"\n❌ Evaluation failed. Please check the errors above.")


# Import shared config if available
try:
    from config import shared
except ImportError:
    # Create a simple shared object if config is not available
    class SharedConfig:
        def __init__(self):
            self.summary_result = None
            self.output_path = None
    shared = SharedConfig()


if __name__ == "__main__":
    # Example of how to test the tools
    print("🔧 Testing LangChain Tools Integration")
    
    # Create deployment agent with bound tools
    tools = create_deployment_agent()
    
    print(f"📋 Available tools: {[tool.name for tool in tools]}")
    print("\n💡 Tools are ready for integration with LangChain/LangGraph workflows")
    
    # Run original evaluation if called directly
    # run_evaluation()