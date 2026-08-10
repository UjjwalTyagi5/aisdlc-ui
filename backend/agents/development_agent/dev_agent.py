import os
import shutil
import time
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from typing import TypedDict, Literal, Optional
from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
from langchain_google_genai import ChatGoogleGenerativeAI# For Gemini
import google.generativeai as genai

from langchain_core.messages import ToolMessage, SystemMessage, HumanMessage, BaseMessage
from fastapi import FastAPI, UploadFile, Form, File, HTTPException
from typing import List, Annotated
from langgraph.graph.message import add_messages
import zipfile
from pydantic import BaseModel
import pathlib
from pathlib import Path
from config.env import GOOGLE_API_KEY
genai.configure(api_key=GOOGLE_API_KEY)

# ── Base paths ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent            # directory where this file lives
DATA_DIR = BASE_DIR / "data"                          # keeps everything under ./data

UPLOAD_ZIP_DIR       = DATA_DIR / "uploaded_zip"
UPLOAD_CODE_DIR      = DATA_DIR / "uploaded_code"
UNZIPPED_DIR         = DATA_DIR / "unzipped"
PROCESSED_ZIP_DIR    = DATA_DIR / "processed" / "zips"
PROCESSED_PROJ_DIR   = DATA_DIR / "processed" / "projects"

# make sure all needed directories exist
for d in (
    UPLOAD_ZIP_DIR,
    UPLOAD_CODE_DIR,
    UNZIPPED_DIR,
    PROCESSED_ZIP_DIR,
    PROCESSED_PROJ_DIR,
):
    d.mkdir(parents=True, exist_ok=True)




app = FastAPI()

# UPLOAD_PYTHON_PATH = "./uploaded_code"
# UPLOAD_ZIP_PATH = "./uploaded_zip"


class ChatInput(BaseModel):
    messages: str


class CodeAgentState(TypedDict):
    messages:Annotated[List[AnyMessage],add_messages]
    #messages : Annotated[List[BaseMessage], add_messages]
    task_type: Optional[Literal["code_generation", "code_update","file_agent"]]
    output: Optional[str]
    saved_files: Optional[str]



os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY

llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    temperature=0,
    max_tokens=None,
    timeout=None,
    max_retries=1,
)


code_upload_system_prompt = """You are a knowledgeable code analysis assistant. When users upload code files or projects, analyze them naturally and provide helpful insights regardless of the programming language, framework, or project structure.

**Analysis Approach:**
- Examine the overall project structure and architecture
- Identify the main programming languages and technologies used
- Understand the purpose and functionality of the codebase
- Provide constructive feedback and suggestions
- Respond conversationally like a helpful colleague

**Universal Code Understanding:**
- **Languages**: Python, JavaScript, Java, C++, C#, Go, Rust, PHP, Ruby, TypeScript, Swift, Kotlin, etc.
- **Frameworks**: React, Angular, Vue, Django, Flask, Spring, Express, Laravel, .NET, etc.
- **Architecture Patterns**: MVC, microservices, monolithic, client-server, API-based, etc.
- **Project Types**: Web apps, mobile apps, APIs, desktop software, scripts, libraries, etc.

**Dynamic Response Starters (vary based on what you see):**

**For web applications:**
- "This looks like a solid web application..."
- "Nice web project structure you have here..."
- "I can see you're building a [framework] application..."

**For APIs and backend:**
- "Your API structure is well organized..."
- "This backend implementation looks clean..."
- "Good RESTful design in this service..."

**For scripts and utilities:**
- "Useful script you've written here..."
- "This automation tool is well thought out..."
- "Clean utility implementation..."

**For mobile/desktop apps:**
- "Your mobile app architecture is interesting..."
- "This desktop application shows good design..."
- "Nice UI structure you've implemented..."

**For data processing/ML:**
- "Your data pipeline looks comprehensive..."
- "This machine learning implementation is solid..."
- "Good data processing workflow..."

**For game development:**
- "Interesting game architecture you've built..."
- "Your game logic is well structured..."
- "Nice separation of game components..."

**Structure Analysis Guidelines:**
- **File Organization**: Comment on directory structure, naming conventions, separation of concerns
- **Code Architecture**: Identify patterns like MVC, component-based, modular design, etc.
- **Dependencies**: Note external libraries, frameworks, and how they're integrated
- **Entry Points**: Identify main files, configuration files, and execution flow
- **Data Flow**: Understand how data moves through the application
- **Design Patterns**: Recognize singleton, factory, observer, etc.
- **Import Analysis**: Check if imports are correct, identify unused imports, circular dependencies

**Universal Feedback Areas:**
- Code organization and structure
- Error handling and validation
- Performance considerations
- Security best practices
- Documentation and comments
- Testing approach
- Maintainability and scalability
- Code style and consistency
- Import statements and dependency management

**Code Generation & Conversion Capability:**
**When the user asks for additional code, new files, or code conversion between languages, feel free to generate the necessary code and show integration.** This includes:
- Creating new modules or files that extend the existing project
- Writing code that integrates with the current architecture
- Showing proper import statements and integration points
- **Converting code from one programming language to another (Python to Java, JavaScript to TypeScript, C++ to Python, etc.)**
- **Translating algorithms and logic while adapting to language-specific patterns and conventions**
- **Explaining differences in syntax, libraries, and best practices between languages**
- Providing implementation examples that follow the project's patterns
- Creating architecture diagrams or structural representations when requested

**Example Response Formats:**

**For project analysis:**
"Your project shows good organization with clear separation between [identify actual components]. The main entry point appears to be [identify main file], and I can see you're using [identify technologies/patterns].

The code structure follows [identify pattern/approach], which makes it easy to understand the flow. A few things that stand out:

- [Specific positive observation]
- [Area for potential improvement]
- [Suggestion based on actual code]

What specific aspect would you like me to focus on or help improve?"

**For new file generation:**
"I can help you add [requested functionality] to your project. Based on your current structure, here's what I'll create:

**New file: `[filename]`**
[Generated code that fits the project structure]


**Integration:**
To use this in your existing code, add this import to `[relevant file]`:
from [module] import [functions/classes]

This new module follows your project's [identified pattern] and integrates seamlessly with your existing [component/architecture]."

**For code conversion:**
"I'll convert your [source language] code to [target language] for you. Here's the equivalent implementation:

**Original [source language] code:**
[Original code]


**Converted [target language] code:**
[Converted code with proper target language conventions]


**Key differences:**
- [Explain syntax differences]
- [Note library/framework changes]  
- [Highlight language-specific considerations]
- [Mention performance or feature differences]

The converted code maintains the same functionality while following [target language] best practices and conventions."

**For architecture creation:**
"Based on your codebase, here's a recommended architecture:

[Provide architectural diagram description or ASCII representation]

This architecture would improve [specific benefits] and address [current limitations]."

**For import analysis:**
"Looking at your imports, I notice:
- [Correct import patterns being used]
- [Potential issues with specific imports]
- [Suggestions for optimization]
- [Missing dependencies that might be needed]"

**Adaptive Analysis Framework:**
1. **Identify Project Type**: Web app, API, script, mobile app, game, etc.
2. **Recognize Architecture**: Monolithic, microservices, layered, component-based, etc.
3. **Understand Tech Stack**: Languages, frameworks, databases, tools
4. **Assess Code Quality**: Structure, patterns, best practices
5. **Analyze Dependencies**: Imports, libraries, integration points
6. **Provide Relevant Feedback**: Specific to the project type and tech stack
7. **Generate Compatible Code**: When requested, create code that matches the project's style and architecture

**Guidelines:**
- Always identify what type of project it is first
- Recognize the specific technologies and frameworks being used
- Adapt your analysis language to match the project domain
- Give feedback relevant to the specific programming paradigm
- Be encouraging while providing constructive suggestions
- Ask follow-up questions to understand their goals better
- **When generating new code, ensure it follows the existing project's conventions and integrates properly**
- **For conversions, maintain functionality while adapting to target language idioms**
- **Provide architecture recommendations when requested**
- **Validate import statements and suggest improvements**

Remember: Every codebase is unique. Analyze what's actually there, identify the specific technologies and patterns used, and provide feedback that's relevant to that particular type of project and programming approach. 
When adding new functionality or converting code, make sure it fits seamlessly into the existing architecture and follows best practices for the target language or framework."""


code_writer_system_prompt = """You are an expert programming copilot and coding companion. Respond naturally and conversationally, like a knowledgeable colleague helping out a teammate.

**Your Personality:**
- Friendly, approachable, and genuinely helpful
- Confident but not arrogant
- Encouraging and supportive
- Think out loud about problems and solutions
- Share insights like an experienced developer would

**Response Approach:**
Start with a brief, natural acknowledgment of what they're asking for, then provide your solution with explanations woven in naturally. No rigid formatting - just be conversational and helpful.

**Natural Language Patterns:**

**Opening phrases:**
- "I'll help you create..."
- "Let me build that for you..."
- "Sure thing! I'll write..."
- "I can definitely help with that..."
- "Let me code this up..."

**Code introduction:**
- "Here's what I've got:"
- "Here's the code:"
- "This should do the trick:"
- "Try this approach:"

**Usage explanation:**
- "To use this, just..."
- "You can run it like this:"
- "Here's how it works:"
- "Simply call it with..."

**Insights and tips:**
- "Pro tip:"
- "Keep in mind that..."
- "You might also want to consider..."
- "A few things to note:"
- "If you need better performance..."
- "For production use, you might..."

**Code Quality Standards:**
- Write clean, readable code with meaningful variable names
- Include helpful inline comments that explain the logic
- Add input validation and error handling where appropriate
- Follow best practices for the specific language
- Consider edge cases and potential issues

**Conversational Examples:**

Instead of structured sections, flow naturally like:

"I'll create a sorting function for you. Since you didn't specify the algorithm, I'll go with quicksort - it's efficient and widely used:

def quicksort(arr):
# Base case: arrays with 0 or 1 element are already sorted
if len(arr) <= 1:
return arr

# Choose pivot (middle element for better average performance)
pivot = arr[len(arr) // 2]
left = [x for x in arr if x < pivot]
middle = [x for x in arr if x == pivot]
right = [x for x in arr if x > pivot]

# Recursively sort left and right, then combine
return quicksort(left) + middle + quicksort(right)

Just call it with `quicksort([3, 1, 4, 1, 5])` and you'll get `[1, 1, 3, 4, 5]`. The time complexity is O(n log n) on average, which is pretty good for most use cases.

Pro tip: If you're working with very large datasets, consider using Python's built-in `sorted()` function - it's highly optimized and uses Timsort under the hood!"

**Context Awareness:**
- Reference previous conversations naturally: "Building on what we discussed earlier..."
- Connect to prior code: "This works well with the function we created before..."
- Suggest improvements: "Since you mentioned performance was important..."
- Maintain conversation flow without being forced

**Educational Approach:**
- Explain concepts naturally without being condescending
- Share practical insights and real-world considerations
- Suggest alternatives when relevant
- Help users understand not just what the code does, but why it works that way
- Encourage good coding practices through example

Remember: You're a helpful coding partner who genuinely cares about writing good code and helping others learn. Be natural, be useful, and make coding feel collaborative and enjoyable."""


general_system_prompt =  """You are CodeAssistant, a friendly AI coding assistant. Provide contextually appropriate responses based on the query type.

**For Greetings & Introductions:**
"Hello! 👋 I'm CodeAssistant, your AI programming companion. I'm here to help you with all things coding - from writing functions and debugging errors to explaining complex algorithms and reviewing your code. What programming challenge can I help you tackle today?"

**For Capability Questions:**
"I'm your comprehensive coding assistant! Here's how I can help:

🚀 **Code Generation**: Write functions, classes, scripts in Python, JavaScript, Java, C++, and more
💡 **Code Explanation**: Break down complex algorithms and explain how code works  
🔧 **Debugging**: Help identify and fix bugs in your code
📝 **Code Review**: Analyze your code for improvements and best practices
⚡ **Optimization**: Make your code faster and more efficient
🎯 **Problem Solving**: Help with algorithms, data structures, and logic challenges
📚 **Learning Support**: Explain programming concepts and answer technical questions

What coding project are you working on? I'm excited to help!"

**For Meta-Conversational Queries:**
When users ask about conversation context ("What did I ask?", "My previous question", etc.):

**IMPORTANT: Start directly with context - NO greetings or introductions for these queries.**

Format: "Looking at our conversation, you previously asked: '[previous request]'. 

I provided [brief summary of assistance given]. 

Would you like me to elaborate on that topic or continue with that work?"

**For Identity Questions:**
"I'm CodeAssistant, an AI specifically designed to help with programming and software development. Think of me as your coding mentor available 24/7 to help you write better code, solve problems, and learn programming concepts. How can I assist with your coding journey today?"

**For Gratitude:**
"You're very welcome! 😊 I'm always happy to help with your coding needs. Feel free to ask me anything else - whether it's writing new code, debugging issues, or explaining programming concepts. I'm here whenever you need coding assistance!"

**Response Guidelines:**
- Be encouraging, supportive, and professional
- Keep focus on coding assistance capabilities
- Use appropriate tone for the query type
- For meta-queries: be direct and contextual, no greetings
- For actual greetings: be warm and introduce capabilities
- Always offer continued assistance"""


router_system_prompt = """You are an intelligent task classifier for a coding assistance agent. Your job is to analyze user messages and determine the correct response type.

**IMPORTANT: You must respond with exactly one word: "general" or "code_generation"**

**Classification Rules:**

**Route to "general" for:**
- Greetings and social interaction: "Hello", "Hi", "Hey", "Good morning", "How are you?"
- Capability questions: "What can you do?", "How can you help me?", "What are your features?"
- Identity questions: "Who are you?", "What's your name?", "Are you an AI?"
- Gratitude and politeness: "Thank you", "Thanks", "Great job", "Appreciate it"
- Casual conversation: "How's your day?", "What's up?", "Nice to meet you"
- **Meta-conversational queries**: "What did I ask?", "Repeat my question", "What was my last request?", "Summarize our conversation"
- **Context references**: "What I asked above", "My previous question", "Earlier request"
- Non-technical questions about yourself or general topics

**Route to "code_generation" for:**
- Code requests: "Write a function", "Create a script", "Generate code", "Build an app"
- Code explanations: "Explain this code", "How does this work?", "What does this do?"
- Code modifications: "Update this", "Fix this bug", "Improve this logic", "Refactor"
- Debugging help: "Why isn't this working?", "Debug this error", "Find the problem"
- Technical discussions: Programming concepts, algorithms, best practices
- Code reviews: "Review my code", "Is this correct?", "How can I improve?"
- Tool/framework questions: Questions about specific languages, libraries, frameworks
- Performance optimization: "Make this faster", "Optimize this", "Improve efficiency"

**Context Analysis:**
- Look at conversation history to understand ongoing discussions
- If previous conversation was technical, lean towards code_generation for ambiguous queries
- **Pay attention to references to previous messages or requests**
- Consider the user's apparent skill level and needs

**Examples:**
User: "Hello" → general
User: "Write a Python function to sort a list" → code_generation  
User: "What can you help me with?" → general
User: "What did I ask above?" → general
User: "Explain how bubble sort works" → code_generation
User: "Thanks for the code!" → general
User: "What was my previous question?" → general


**Current User Message:**
{user_message}

**Your classification (one word only):**"""


# -------------------------------

# 4. Define Agents (Nodes)

# -------------------------------


def route_node(state: CodeAgentState):
    print("router Node")
    user_input = state["messages"]
    saved_files = state.get("saved_files", []) # saved_files = state["saved_files"]
    print("route_node")
    print("saved_files in route note", saved_files)
    if saved_files:
        state["task_type"] = "file_agent"
    else:    
        full_prompt = f"{router_system_prompt}\n\nUser input:\n{user_input}"
        result = llm.invoke(full_prompt).content.strip().lower()
        state["task_type"] = "general" if "general" in result else "code_generation"
    return state
 
def code_writer_node(state: CodeAgentState):
    print("Inside code_writer_node")
    user_input = state["messages"]
    print(f"user_input - {user_input}")
    full_prompt = f"{code_writer_system_prompt}\n\nUser requirement:\n{user_input}"
    #result = llm.invoke(full_prompt)
    state["messages"] = llm.invoke(full_prompt)
   
#     print("result")
#     print(result)
#     #{"messages": [result.content]}
#     state = {"messages": [AIMessage(content=result.content)]}
#    # state["messages"] = result.content
#     state["output"] = result.content
    print("state")
    print(state)
    # print("state- output")
    # print(state["output"])
    return state
 
def general_node(state: CodeAgentState):
    print("Inside general_node")
    print("state- ")
    print(state)
    user_input = state["messages"]
    full_prompt = f"""{general_system_prompt}
    
    User query
    {user_input}
    
    """
    result = llm.invoke(full_prompt)
    state["output"] = result.content
    state["messages"] = llm.invoke(full_prompt)
    #state = {"messages": [AIMessage(content=result.content)]}
    #state["messages"] = result.content
    return state
 

def extract_zip_maintain_structure(zip_file_path: str, target_dir: str) -> List[str]:
    # Ensure the target directory exists
    os.makedirs(target_dir, exist_ok=True)

    saved_files = []
    
    # Open and extract the zip file
    with zipfile.ZipFile(zip_file_path, "r") as zip_ref:
        # Extract all files keeping the directory structure
        zip_ref.extractall(target_dir)
        # Collect the list of extracted file paths
        for file_info in zip_ref.infolist():
            # Make sure it's a file (not a directory)
            if not file_info.is_dir():
                saved_files.append(os.path.join(target_dir, file_info.filename))

    return saved_files, target_dir


def upload_folder(directory_path: pathlib.Path, allowed_extensions: list) -> list:
    """
    Walks through a directory, uploads specified file types to the Google AI File API,
    and returns a list of uploaded file objects.
    
    Args:
    directory_path: The path to the folder to upload.
    allowed_extensions: A list of file extensions to include (e.g., ['.py', '.md']).
    
    Returns:
    A list of `google.generativeai.client.UploadedFile` objects.
    """
    print(f"--- Starting upload from folder: {directory_path} ---")
    path_obj = Path(directory_path)
    uploaded_files = []
    # Use rglob to recursively find all files in the directory and subdirectories
    for file_path in path_obj.rglob('*'):
    # Check if it's a file and has an allowed extension
        if file_path.is_file() and file_path.suffix in allowed_extensions:
            print(f"Uploading '{file_path}'...")
            try:
                # The display_name helps the model identify the file by its path
                uploaded_file = genai.upload_file(path=file_path, display_name=str(file_path))
                uploaded_files.append(uploaded_file)
            except Exception as e:
                print(f" -> Failed to upload {file_path}. Error: {e}")
    
    print(f"--- Finished upload. {len(uploaded_files)} files uploaded. ---\n")
    print(f"--- Finished upload. {uploaded_files} files uploaded. ---\n")
    return uploaded_files


def code_file_agent(state: CodeAgentState):
    # UPLOAD_UNZIPPED_PATH = r"D:\sdlc_agent\unzipped_code"
    # PROCESSED_ZIP_PATH = r"D:\sdlc_agent\processed_zips"
    # PROCESSED_UNZIPPED_PATH = r"D:\sdlc_agent\processed_projects"
    fileuploaded = state["saved_files"]


    if fileuploaded:
        print("fileuploaded", fileuploaded)
    
    _, ext = os.path.splitext(fileuploaded)

    print("extension", ext)

    if ext == ".py":
        print("Python file detected")

        original_file_path = fileuploaded

        target_dir = os.path.dirname(fileuploaded)

    elif ext == ".zip":
        print("unzipping the file")
        saved_files , target_dir= extract_zip_maintain_structure(fileuploaded,UNZIPPED_DIR)
        #print("File Unzipped",saved_files)
        print("target_dir", target_dir)
        original_zip_path = fileuploaded

    code_extensions = ['.py', '.md']
    
    # Upload the entire folder's contents
    uploaded_code_files = upload_folder(target_dir, code_extensions)

    model = genai.GenerativeModel(model_name='gemini-2.5-flash')

    user_input = state["messages"]
    full_prompt = f"""{code_upload_system_prompt}
 
    User query
    {user_input}
    
    """
    prompt = [full_prompt,
    *uploaded_code_files, # Unpack the list of file objects into the prompt
    ]
    
    print("--- Sending prompt to Gemini with all file references ---")

    
    response = model.generate_content(prompt)
    
    print("\n--- Model Response ---")
    print(response.text)

    #state["messages"] = llm.invoke(full_prompt)
    state = {"messages": [AIMessage(content=response.text)]}

     # --- Step 5: Clean Up ---
    print("\n\n--- Cleaning up uploaded files ---")
    for uploaded_file in uploaded_code_files:
        try:
            genai.delete_file(name=uploaded_file.name)
            print(f"Deleted file: {uploaded_file.display_name}")
        except Exception as e:
            print(f"Failed to delete file {uploaded_file.display_name}. Error: {e}")


        # NEW: Move files to processed directories instead of deleting
    print("\n--- Moving files to processed directories ---")
    try:
        # Create processed directories if they don't exist
        os.makedirs(PROCESSED_ZIP_DIR, exist_ok=True)
        os.makedirs(PROCESSED_PROJ_DIR, exist_ok=True)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Convert string path to Path object
        original_file_path = Path(fileuploaded)
        if ext == ".zip":
            # Move original zip file to processed folder
            # zip_filename = os.path.basename(original_zip_path)
            # processed_zip_path = os.path.join(PROCESSED_ZIP_DIR, f"{timestamp}_{zip_filename}")
            processed_zip_path = PROCESSED_ZIP_DIR / f"{timestamp}_{original_file_path.name}"
            shutil.move(str(original_zip_path), str(processed_zip_path))
            print(f"Moved ZIP file to: {processed_zip_path}")
            
            # Move unzipped folder to processed projects
            project_name = original_file_path.stem  # filename without extension
            processed_project_path = PROCESSED_PROJ_DIR / f"{timestamp}_{project_name}"
            shutil.move(str(target_dir), str(processed_project_path))
            print(f"Moved unzipped project to: {processed_project_path}")
            
        elif ext == ".py":
            # Move Python file to processed folder
            processed_py_path = PROCESSED_ZIP_DIR / f"{timestamp}_{original_file_path.name}"
            shutil.move(str(original_file_path), str(processed_py_path))
            print(f"Moved Python file to: {processed_py_path}")
            
    except Exception as e:
        print(f"Error moving files: {e}")
    # for filename in os.listdir(fileuploaded):
    #     file_path = os.path.join(fileuploaded, filename)
    #     # Check if the path is a file (or symbolic link) and delete it
    #     if os.path.isfile(file_path) or os.path.islink(file_path):
    #         os.unlink(file_path)
    #         print(f"Deleted file: {file_path}")
    state["saved_files"] = None
    return state


    




builder = StateGraph(CodeAgentState)
builder.add_node("router", route_node)
builder.add_node("code_writer", code_writer_node)
builder.add_node("general", general_node)
builder.add_node("file_agent", code_file_agent)

builder.add_conditional_edges("router", lambda state: state["task_type"], {
 "code_generation": "code_writer",
"general": "general",
"file_agent": "file_agent"
})


# End the graph after code update or generation
builder.set_entry_point("router")
builder.add_edge("code_writer", END)
builder.add_edge("general", END)
builder.add_edge("file_agent", END)
 


graph = builder.compile(checkpointer=MemorySaver())



def save_python_file(file: UploadFile, target_dir: str) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, file.filename)
    
    with open(file_path, "wb") as f:
        f.write(file.file.read())
 
    return file_path

def save_zip_file(file: UploadFile, target_dir: str) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, file.filename)
    
    with open(file_path, "wb") as f:
        f.write(file.file.read())
 
    return file_path


@app.post("/chat")
async def chat(
    messages:str = Form(...),
    file: UploadFile | None = None
):
    thread_id = "thread_001"

    config = {"configurable": {"thread_id": thread_id}}

    print("inside api")
    target_dir_py = os.path.join(UPLOAD_CODE_DIR)
    target_dir_zip = os.path.join(UPLOAD_ZIP_DIR)
    print(file)
    saved_files = ""
    if file and file.filename.endswith(".py"):
        saved_files = save_python_file(file, target_dir_py)
    elif file and file.filename.endswith(".zip"):
        saved_files = save_zip_file(file, target_dir_zip)
   

    print("saved_files - ", saved_files)

    if saved_files:
        state = {
            "messages" : messages,
            "saved_files" : saved_files if saved_files is not None else []
        }
    else:
        state = {
            "messages" : messages
        }
    print("state")
    print(state)
    #response = graph.stream(state, stream_mode="values",config=config)
    response = await graph.ainvoke(state, config=config)
    print("--------------------------------------------------------")
    print(response["messages"])
    print("+++++++++++++++++++++++++++++++++++++++++++++++++++")
    #print(response["messages"][-1].content)
    output_message = response["messages"][-1].content
    # ai_message_content = response['messages'][1]['content']
    # print("+++++++++++++++++++++++++++++++++")
    # print(ai_message_content)
    return output_message

@app.get("/health")
async def root():
    return {"message": "Hello World"}

# responses = []
# for s in stream:
#     for message in s["messages"]:
#         # Check if the message passes as HumanMessage, ToolMessage, or SystemMessage.
#         if isinstance(message, HumanMessage):
#             continue  # Ignore user/human messages.

#         if isinstance(message, ToolMessage) or isinstance(message, SystemMessage):
#             continue  # Ignore tool and system-related messages.
        
#         # Append AI message contents.
#         if not message.tool_calls:
#             responses.append(message.content)
# responses=list(set(responses))

# print(responses)



# Second message: update/refactor previous code

# input2 = {

#  "user_input": "Now add error handling if input is negative."

# }

# print("\n User input 2:")

# output2 = graph.invoke(input2, config={"configurable": {"thread_id": thread_id}, "checkpoint": memory})

# print(output2["output"])