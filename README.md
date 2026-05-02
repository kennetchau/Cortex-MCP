# MCP Server

A modular Model Context Protocol (MCP) server built with FastAPI, providing tools for LLM interactions.

## Features

- **Modular Architecture** - Each tool is easily maintainable
- **File Operations** - Read, write, append, replace, and insert text in files
- **Web Search** - Search the live web using DDGS
- **URL Fetching** - Scrape and summarize web content
- **Command Execution** - Run shell commands in a sandboxed environment
- **Strict Isolation** - Bubblewrap sandbox with disposable `/tmp`

## Tools

| Tool | Description |
|------|-------------|
| `today` | Get today's date and time |
| `add` | Add two numbers together |
| `web_search` | Search the live web for information |
| `fetch_content` | Scrape a URL and extract content |
| `list_files` | List files and directories |
| `read_file` | Read files with line-based windowing |
| `write_file` | Write text to files |
| `append_to_file` | Append text to existing files |
| `replace_in_file` | Find and replace text in files |
| `insert_after_marker` | Insert text after a marker line |
| `run_command` | Execute shell commands in sandbox |

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd mcp_server

# Install dependencies using uv
uv sync
```

## Usage

```bash
# Start the server
python main.py
```

The server will start on `http://0.0.0.0:8000`.

## Architecture

```
mcp_server/
├── main.py              # FastAPI application entry point
├── tools.json           # Tool definitions (JSON schema)
└── tools/               # Tool implementations
    ├── __init__.py      # Exports all handlers
    ├── add.py           # Math operations
    ├── today.py         # Date/time
    ├── web_search.py    # Web search
    ├── fetch_content.py # URL scraping
    ├── files.py         # All file operations
    └── run_command.py   # Shell command execution
```

## Sandbox Configuration

The `run_command` tool uses bubblewrap for isolation:

- **Read-only root filesystem** (`--ro-bind / /`)
- **Disposable temp directory** (`--tmpfs /tmp`)
- **Writable resources directory** (`--bind resources resources`)
- **DNS resolution enabled** (no `/etc` restriction)

## Disclaimer

This project was developed with assistance from **Qwen 3.6**, a large language model by Alibaba Group's Tongyi Lab. While AI assisted in code generation, documentation, and refactoring, all technical decisions and final implementations were reviewed and validated by the human developer.

## License

MIT License
