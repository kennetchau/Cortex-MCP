"""Configuration for MCP server."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# Base directory for all tool operations
BASE_DIR = Path(os.environ.get("MCP_BASE_DIR", "/home/ken/Documents/Coding/llm_workspace"))
