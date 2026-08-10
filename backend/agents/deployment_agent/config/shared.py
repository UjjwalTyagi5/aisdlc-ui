# agents/pipeline_agent/config/shared.py
"""
Shared configuration and state variables for the Pipeline Agent.
This file maintains state across different API calls and sessions.
"""

# Session management
prev_session_id = ""
current_session_id = ""

# Output file tracking
output_file = ""
output_files = []

# Directory paths
input_dir = "inputs"
output_dir = "pipeline_outputs"
codebase_dir = "codebase"
testing_dir = "testing"

# Pipeline execution state
pipeline_running = False
current_step = ""
total_steps = 11

# Analysis results tracking
analysis_results = {
    "codebase_extracted": False,
    "testing_extracted": False,
    "risk_analysis_complete": False,
    "deployment_validation_complete": False,
    "dockerfile_generated": False,
    "readme_generated": False,
    "docker_instructions_generated": False,
    "error_count": 0,
    "files_processed": 0
}

# API configuration
gemini_api_key = ""
gemini_base_url = ""
api_timeout = 120

# File processing configuration
max_file_size_mb = 10
supported_code_extensions = {'.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs', '.rb', '.php', '.cs', '.kt'}
config_keywords = ['requirements', 'package', 'makefile', 'dockerfile', 'config', 'env', 'yaml', 'yml', 'json']
report_extensions = {'.txt', '.md', '.json', '.xml', '.html', '.log', '.csv', '.pdf', '.docx', '.xlsx'}

# Error tracking
errors = []
warnings = []

# Pipeline step names for progress tracking
pipeline_steps = [
    "extract_codebase",
    "extract_testing", 
    "read_codebase",
    "read_testing",
    "combine_content",
    "risk_analysis",
    "deployment_validation",
    "generate_dockerfile",
    "generate_readme",
    "generate_docker_instructions",
    "write_outputs"
]

def reset_session_state():
    """Reset all session-specific state variables"""
    global analysis_results, errors, warnings, output_files, pipeline_running, current_step
    
    analysis_results = {
        "codebase_extracted": False,
        "testing_extracted": False,
        "risk_analysis_complete": False,
        "deployment_validation_complete": False,
        "dockerfile_generated": False,
        "readme_generated": False,
        "docker_instructions_generated": False,
        "error_count": 0,
        "files_processed": 0
    }
    
    errors = []
    warnings = []
    output_files = []
    pipeline_running = False
    current_step = ""

def update_analysis_results(key: str, value: bool):
    """Update analysis results tracking"""
    global analysis_results
    analysis_results[key] = value

def add_error(error_msg: str):
    """Add an error to the tracking list"""
    global errors
    errors.append(error_msg)
    analysis_results["error_count"] = len(errors)

def add_warning(warning_msg: str):
    """Add a warning to the tracking list"""
    global warnings
    warnings.append(warning_msg)

def add_output_file(file_path: str):
    """Add an output file to the tracking list"""
    global output_files
    output_files.append(file_path)

def get_session_summary():
    """Get a summary of the current session"""
    return {
        "session_id": current_session_id,
        "pipeline_running": pipeline_running,
        "current_step": current_step,
        "analysis_results": analysis_results,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "output_files_count": len(output_files),
        "errors": errors[-5:],  # Last 5 errors
        "warnings": warnings[-5:],  # Last 5 warnings
        "output_files": output_files
    }