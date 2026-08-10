import os
import shutil
import time
from langgraph.graph import StateGraph, END
from typing import TypedDict, Literal, Optional
from langchain_core.runnables import Runnable
from langchain_core.messages import AIMessage, HumanMessage, AnyMessage
import google.generativeai as genai

from langchain_core.messages import ToolMessage, SystemMessage, HumanMessage, BaseMessage
from fastapi import FastAPI, UploadFile, Form, File, HTTPException
from typing import List, Annotated
from langgraph.graph.message import add_messages
import zipfile
from pydantic import BaseModel
import pathlib
from pathlib import Path

from config.websocket_utils import set_websocket_context
import sys, inspect

from config import sdlcSettings
esett = sdlcSettings()
# def create_directory():
#     """
#     Create a directory if it does not exist.
#     """
#     # ── Base paths ───────────────────────────────────────────────
#     BASE_DIR = Path(__file__).resolve().parent            # directory where this file lives
#     DATA_DIR = BASE_DIR / "data"                          # keeps everything under ./data

#     UPLOAD_ZIP_DIR       = DATA_DIR / "uploaded_zip"
#     UPLOAD_CODE_DIR      = DATA_DIR / "uploaded_code"
#     UNZIPPED_DIR         = DATA_DIR / "unzipped"
#     PROCESSED_ZIP_DIR    = DATA_DIR / "processed" / "zips"
#     PROCESSED_PROJ_DIR   = DATA_DIR / "processed" / "projects"

#     # make sure all needed directories exist
#     for d in (
#         UPLOAD_ZIP_DIR,
#         UPLOAD_CODE_DIR,
#         UNZIPPED_DIR,
#         PROCESSED_ZIP_DIR,
#         PROCESSED_PROJ_DIR,
#     ):
#         d.mkdir(parents=True, exist_ok=True)

#     return UPLOAD_ZIP_DIR, UPLOAD_CODE_DIR, UNZIPPED_DIR, PROCESSED_ZIP_DIR, PROCESSED_PROJ_DIR


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
    #print("Extracted all the file from Zip file")
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
    #print(f"--- Starting upload from folder: {directory_path} ---")
    path_obj = Path(directory_path)
    uploaded_files = []
    # Use rglob to recursively find all files in the directory and subdirectories
    for file_path in path_obj.rglob('*'):
    # Check if it's a file and has an allowed extension
        if file_path.is_file() and file_path.suffix in allowed_extensions:
            #print(f"Uploading '{file_path}'...")
            try:
                # The display_name helps the model identify the file by its path
                uploaded_file = genai.upload_file(path=file_path, display_name=str(file_path))
                uploaded_files.append(uploaded_file)
            except Exception as e:
                print(f" -> Failed to upload {file_path}. Error: {e}")
    
    # print(f"--- Finished upload. {len(uploaded_files)} files uploaded. ---\n")
    # print(f"--- Finished upload. {uploaded_files} files uploaded. ---\n")
    return uploaded_files



def save_python_file(file: UploadFile, target_dir: str) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, file.filename)
    
    with open(file_path, "wb") as f:
        f.write(file.file.read())
 
    return file_path

def save_zip_file(file: UploadFile, target_dir: str) -> List[str]:
    os.makedirs(target_dir, exist_ok=True)
    file_path = os.path.join(target_dir, file.filename)
    print(file_path,"hiiiiiiiiiiiiiiiiiiiiii")
    with open(file_path, "wb") as f:
        f.write(file.file.read())
 
    return file_path
