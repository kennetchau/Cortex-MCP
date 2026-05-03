# MCP Server

A modular Model Context Protocol (MCP) server built with FastAPI, providing tools for LLM interactions.

## Features

- **Modular Architecture** - Each tool is easily maintainable
- **File Operations** - Read, write, append, replace, insert, and delete files
- **Web Research** - Search the live web (DDGS) and scrape/summarize URLs
- **File Search** - Grep through files with exact match or regex patterns
- **Command Execution** - Run shell commands in a sandboxed environment
- **Markdown to PDF** - Convert Markdown files to styled PDF documents
- **Strict Isolation** - Bubblewrap sandbox with disposable `/tmp`
- **Persistent Context** - SQLite knowledge base with full-text search, project isolation, and alias resolution

## Tools

| Tool | Description |
|------|-------------|
| `today` | Get today's date and time |
| `add` | Add two numbers together |
| `web_search` | Search the live web for information |
| `fetch_content` | Scrape a URL and extract content |
| `list_files` | List files and directories (add `recursive=true` for a full tree view) |
| `read_file` | Read files with line-based windowing |
| `write_file` | Write text to files |
| `append_to_file` | Append text to existing files |
| `replace_in_file` | Find and replace text in files |
| `insert_after_marker` | Insert text after a marker line |
| `search_files` | Search for text patterns within files (exact or regex) |
| `delete_file` | Delete a file from the resources directory |
| `remove_directory` | Remove a directory (with optional recursive flag) |
| `run_command` | Execute shell commands in sandbox |
| `md_to_pdf` | Convert a Markdown file to a styled PDF document |
| `store_context` | Store or update project-specific context in SQLite knowledge base (key resolves via canonical name or alias) |
| `query_context` | Query stored context — supports keyword search (FTS5), direct key lookup, or lists all keys. Keys resolve via canonical name or registered aliases |
| `clear_context` | Clear stored context entries by key or wipe all entries for a project |
| `add_context_alias` | Register an alternate name (alias) for an existing context entry. The alias works transparently in store/query/clear operations |
| `list_projects` | List all known projects with last update timestamps |

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
uv run main.py
```

The server will start on `http://0.0.0.0:8000`.

## Architecture

```
mcp_server/
├── main.py              # FastAPI application entry point
├── tools.json           # Tool definitions (JSON schema)
├── tools/               # MCP tool implementations
│   ├── add.py           # Math operations
│   ├── today.py         # Date/time
│   ├── web_research.py  # Web search + URL scraping
│   ├── files.py         # All file operations
│   ├── run_command.py   # Shell command execution
│   ├── md_to_pdf.py     # Markdown to PDF conversion
│   └── sqlite_store.py  # SQLite knowledge base for persistent context
│ ```
 │ 
 ## Persistent Context Usage
 │ 
 The `sqlite_store` module provides a cross-session knowledge base for LLM context:
 │ 
 ### Storing & Querying Context
```json
// Store a new entry
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"store_context","arguments":{"key":"auth-flow","content":"User logs in with email/password","project":"my-app"}}}

// Query by canonical key
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"query_context","arguments":{"key":"auth-flow","project":"my-app"}}}

// Add an alias — now "login" resolves to the same entry
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"add_context_alias","arguments":{"context_key":"auth-flow","alias_name":"login","project":"my-app"}}}

// Query by alias — returns the same entry, shows "Matched via: alias 'login'"
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"query_context","arguments":{"key":"login","project":"my-app"}}}
```

## Sandbox Configuration

The `run_command` tool uses bubblewrap for isolation:

- **Read-only root filesystem** (`--ro-bind / /`)
- **Disposable temp directory** (`--tmpfs /tmp`)
- **Writable resources directory** (`--bind resources resources`)
- **DNS resolution enabled** (no `/etc` restriction)

## Environment Variables

The sandbox passes specific environment variables into the bubblewrap container:

| Variable | Value | Purpose |
|----------|-------|---------|
| `PATH` | `/usr/local/bin:/usr/bin:<user>/.local/bin` | Includes user-installed tools (e.g., `uv`) |
| `HOME` | `<user>` | User home directory (derived from env or BASE_DIR) |
| `UV_CACHE_DIR` | `/tmp/uv-cache` | Redirects uv's cache (host `~/.cache` is read-only) |

All other environment variables from the parent process are inherited but not explicitly set.

## Disclaimer

This project was developed with assistance from **Qwen 3.6**, a large language model by Alibaba Group's Tongyi Lab. While AI assisted in code generation, documentation, and refactoring, all technical decisions and final implementations were reviewed and validated by the human developer.

## MCP Client Connection

### Endpoint
```
POST http://localhost:8000/mcp
Content-Type: application/json
```

### Supported Methods

| Method | Purpose |
|--------|---------|
| `initialize` | Handshake — returns server info and capabilities |
| `tools/list` | Returns all available tools from `tools.json` |
| `tools/call` | Execute a tool by name with arguments |

### Example: Initialize Connection
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
```

### Example: List Tools
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

### Connecting via llama-cpp-web
Add your MCP server URL (`http://localhost:8000/mcp`) in the settings under **Tools → MCP Servers**.

## License

MIT License
