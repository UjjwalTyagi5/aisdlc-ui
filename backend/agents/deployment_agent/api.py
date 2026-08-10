#*********************************************************************************/FAST-API/***********************************************************************************
import os
import asyncio
from fastapi import FastAPI, Form , UploadFile, File
from agents.planning import app as planning_app
from dotenv import load_dotenv
import google.generativeai as genai
from langchain_core.messages import HumanMessage, SystemMessage ,ToolMessage, SystemMessage
from uuid import uuid4
from config import shared
from typing import List
import shared



load_dotenv()
SYS_MESSAGE = """
You are a highly specialised agent that operates as a state machine. You're preffered mode of action is to use the tools you have been provided
You can answer in text and also correct mistakes in generated content.
You can answer directly any query that is not related to documents or files.
When updating content DO NOT invent information or change formatting.
You MUST Maintain consistency with the language of the generated content.
You MUST not infinitely Loop.
YOU MUST Follow these core instructions very carefully:
-WHEN USING UPDATE MAKE SURE ALL THE REQUIRED CONTEXT IS COMPILED CAREFULLY
-DO NOT REUPLOAD OR REUSE A TOOL WHEN IT DID NOT ERROR OR THE USER DID NOT EXPLICITLY ASK FOR SOMETHING THAT NEEDS A TOOL.
-DO NOT REUSE DELETE FILE IF IT ERRORS.
-ONLY SAVE TO DOCX WHEN EXPLICITLY ASKED TO SAVE IT.
-ONLY CLEANUP WHEN THE USER ASKS FOR IT.
-WHEN THE USER ASKS FOR AN UPDATE, MAKE SURE TO INCLUDE ALL THEIR HISTORICAL NEEDS AND REFERENCE THE CORRECT CONTENT.
-FOR a general query or update YOU MUST curate the query as an LLM prompt before calling the tool
-WHEN YOU CAN USE A TOOL YOU MUST USE A TOOL
-PROVIDE A APOLOGY RESPONSE FOR QUERIES THAT ARE NOT RELATED TO DOCUMENT PROCESSING
-YOU CAN ANSWER SIMPLE GREETINGS FROM THE USER
-ANY GENERATED CONTENT MUST BE INCLUDED IN YOUR RESPONSE TO THE USER

Follow this strict procedure:
1. Analyze the User's request to identify any local file paths mentioned or any previous content referenced.
2. Your first action MUST be to call the `upload_file` tool for every local file required.
3. Wait for the `upload_file` tool to return a file name reference
4. Your next action must be to call the appropriate processing tool you MUST pass the file name references you received from the prevoius step.
5. If the user asks for cleanup, You must call `delete_file`, using the same file name reference.
6. Between each query provide the an interactive response to user, if any content was generated you MUST include that in your response.
7. If asked to save a file save it only to outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary) accordingly.
8. For Saving a file double check that the output path is of the format outputs/file.docx where file is one of (brd, pdd, mom, risk_register, user_stories, summary)
You MUST Remember the Core instructions
"""
# counter=0
# output_doc=""
api_app = FastAPI()

# def print_stream(stream):
#     responses = []
#     for s in stream:
#         message = s["messages"][-1]
#         if isinstance(message, tuple):
#             responses.append(str(message))
#         else:
#             responses.append(str(message))
#     return responses

def print_stream(stream):
    responses = []
    for s in stream:
        for message in s["messages"]:
            # Check if the message passes as HumanMessage, ToolMessage, or SystemMessage.
            if isinstance(message, HumanMessage):
                continue  # Ignore user/human messages.

            if isinstance(message, ToolMessage) or isinstance(message, SystemMessage):
                continue  # Ignore tool and system-related messages.

            if message.tool_calls:
                print(message,"COT")
            
            # Append AI message contents.
            if not message.tool_calls:
                responses.append(message.content)

    # n=len(responses)

    return responses

 
@api_app.post("/chat-dep/")
async def chat(
    session_id: str = Form(...),
    user_message: str = Form(...),
    uploaded_files: List[UploadFile] = File(None)  # Accept multiple files
):
    print(uploaded_files,"user message")
    input_directory=shared.input_dir
    file_names = []


    # /ounter = uuid4().hex
    global prev_session_id
    if shared.prev_session_id=="":
       shared.prev_session_id=session_id
    first_message = False
    if shared.prev_session_id!=session_id:
        first_message = True
        config = {"configurable": {"thread_id": shared.prev_session_id}}
        state = {"messages": [HumanMessage(content="cleanup if needed")]}
        planning_app.invoke(state,config=config)
        shared.prev_session_id = session_id

    config = {"configurable": {"thread_id": session_id}}
    state = {"messages": [SystemMessage(content=SYS_MESSAGE)], "file_handles": {}}

    os.makedirs(input_directory, exist_ok=True)
    if first_message:
        state = {"messages" : [SystemMessage(content=SYS_MESSAGE)] + [HumanMessage(content=user_message)]}
    else:
        state = {"messages": [HumanMessage(content=user_message)]}
    # Process files if uploaded
    if uploaded_files:
        for uploaded_file in uploaded_files:
            base_name, extension = os.path.splitext(uploaded_file.filename)
            new_file_name = f"{base_name}_{session_id}{extension}"
            file_path = os.path.join(input_directory, new_file_name)
            file_names.append(file_path)

            # Write the file content to the input directory
            with open(file_path, "wb") as file:
                file.write(await uploaded_file.read())

    # Update state messages incorporating all uploaded files
        upload_messages = ", ".join(file_names)
        print(upload_messages,"uploaded_message")
        state = {"messages": state["messages"] + [HumanMessage(content=f"please use the following files {upload_messages}")]}
        print(state,"#####")
        #planning_app.stream(state, stream_mode="values", config=config)

    print(state)
    responses = print_stream(planning_app.stream(state, stream_mode="values", config=config))

    response_data = {
        "conversation_id": session_id,
        "responses": responses[-1],
        "output_filename":shared.output_file
    }
    
    shared.output_file=""

    # Handle new chat session
    # if session_id!="":
    #     state = {"messages": [HumanMessage(content="cleanup if needed")]}
    #     cleanup_responses = print_stream(planning_app.stream(state, stream_mode="values", config=config))

    # Process user message

    print("*******")
    print(response_data)
    return response_data