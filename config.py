"""Configuration for MCP server."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root (if it exists)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

# Base directory for all tool operations
# Falls back to the project root if not set in .env or environment
_config_dir = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("MCP_BASE_DIR", _config_dir))
