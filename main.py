import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
# Import tools from the tools package
from tools import (
    handle_today,
    handle_add,
    handle_web_search,
    handle_fetch_content,
    handle_list_files,
    handle_read_file,
    handle_write_file,
    handle_append_to_file,
    handle_replace_in_file,
    handle_insert_after_marker,
    handle_search_files,
    handle_delete_file,
    handle_remove_directory,
    handle_run_command,
    handle_md_to_pdf,
)
from tools.web_research import scrape_and_summarize

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load tool definitions from JSON config
TOOLS_PATH = Path(__file__).parent / "tools.json"
with open(TOOLS_PATH, "r", encoding="utf-8") as f:
    TOOLS = json.load(f)

# Helper: consistent MCP tool response format
def _tool_response(request_id: str, text: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": text}]}
    })

# Tool dispatcher map
TOOL_HANDLERS = {
    "add": handle_add,
    "today": handle_today,
    "web_search": handle_web_search,
    "fetch_content": handle_fetch_content,
    "list_files": handle_list_files,
    "read_file": handle_read_file,
    "write_file": handle_write_file,
    "append_to_file": handle_append_to_file,
    "replace_in_file": handle_replace_in_file,
    "insert_after_marker": handle_insert_after_marker,
    "search_files": handle_search_files,
    "delete_file": handle_delete_file,
    "remove_directory": handle_remove_directory,
    "run_command": handle_run_command,
    "md_to_pdf": handle_md_to_pdf,
}

# Isolated tool dispatcher
async def handle_tool_call(request_id: str, name: str, args: dict) -> JSONResponse:
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Tool '{name}' not found"}
        })

    # Pass handler-specific dependencies
    kwargs = {"_tool_response": _tool_response, "logger": logger}
    return await handler(request_id, args, **kwargs)

@app.api_route("/mcp", methods=["GET", "POST", "OPTIONS"])
async def handle_mcp(request: Request):
    if request.method == "OPTIONS":
        return JSONResponse(content="OK")

    if request.method != "POST":
        return JSONResponse({"status": "active"})

    try:
        body = await request.json()
    except json.JSONDecodeError as e:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": f"Parse error: {str(e)}"}
        }, status_code=400)

    method = body.get("method")
    request_id = body.get("id")

    match method:
        case "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "llama-web-bridge", "version": "1.1.0"}
                }
            })

        case "tools/list":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": TOOLS}
            })

        case "tools/call":
            params = body.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {})
            return await handle_tool_call(str(request_id), name, args)

        case _:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method '{method}' not found"}
            })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
