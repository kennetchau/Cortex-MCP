# MCP Server

A modular Model Context Protocol (MCP) server built with FastAPI, providing tools for LLM interactions in a sandboxed environment.

## Features

- **Modular Architecture** - Each tool lives in its own file under `tools/`
- **File Operations** - Read, write, append, replace, insert, delete files + recursive directory listing
- **Web Research** - Search the live web (DDGS) and scrape/summarize URLs via Trafilatura
- **File Search** - Grep through files with exact match or regex patterns
- **Command Execution** - Run shell commands inside a Bubblewrap sandbox
- **Markdown to PDF** - Convert Markdown files to styled PDFs via WeasyPrint
- **Strict Isolation** - Bubblewrap sandbox with read-only root filesystem and disposable `/tmp`
- **Persistent Context** - SQLite knowledge base with FTS5 full-text search, project isolation, and alias resolution
- **Project Change Tracker** - Structured changelog with timeline steps for tracking bugs, refactors, features, and milestones

## Tools

| Tool | Description |
|------|-------------|
| `today` | Get today's date and time |
| `add` | Add two numbers together |
| `web_search` | Search the live web for information |
| `fetch_content` | Scrape a URL and extract content |
| `list_files` | List files and directories (use `recursive=true` for tree view) |
| `read_file` | Read files with AI-friendly line-based windowing |
| `write_file` | Write text to files |
| `append_to_file` | Append text to existing files |
| `replace_in_file` | Find and replace text in files |
| `insert_after_marker` | Insert text after a marker line |
| `search_files` | Search for text patterns within files (exact or regex) |
| `delete_file` | Delete a file from the sandbox directory |
| `remove_directory` | Remove a directory (with optional recursive flag) |
| `run_command` | Execute shell commands in sandbox |
| `md_to_pdf` | Convert a Markdown file to a styled PDF document |
| `store_context` | Store project-specific context in SQLite knowledge base |
| `query_context` | Query stored context — supports keyword search (FTS5), direct key lookup, or lists all keys |
| `clear_context` | Clear stored context entries by key or wipe all entries for a project |
| `add_context_alias` | Register an alternate name (alias) for an existing context entry |
| `list_projects` | List all known projects with last update timestamps |
| `add_project_change` | Record a new change entry (bugfix, refactor, feature, milestone, config, other) |
| `add_change_step` | Add a timeline step to an existing project change |
| `list_project_changes` | List changes for a project with filters by type and date range |
| `get_change_history` | Get full history for one change including timeline steps |
| `search_project_changes` | FTS search across project change summaries |
| `store_issue` | Store or update an issue with status, title, description, commit link |
| `query_issues` | Query issues filtered by project, status, or key |
| `update_issue_status` | Transition issue status (open → closed / not-relevant) |
| `list_issues` | List all issues for a project, optionally filtered by status |

## Installation

```bash
# Clone the repository
git clone <repository-url>
cd mcp_copy

# Install dependencies using uv
uv sync
```

## Usage

```bash
# Start the server
uv run main.py
```

The server will start on `http://0.0.0.0:8000`.
### Test Mode

```bash
# Start in test mode (port 9000)
uv run main.py --test
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_BASE_DIR` | Project root | Base directory for all tool operations |
| `HOME` | (from env) | Home directory for sandbox configuration |

Create a `.env` file from the example:

```bash
cp .env.example .env
```

## Architecture

The server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) using JSON-RPC 2.0 over HTTP. It exposes **30 tools** organized into logical modules.

### Directory Structure

```
mcp_copy/
├── main.py              # FastAPI app, MCP endpoint (/mcp), tool dispatcher
├── tools.json           # Tool schema definitions (source of truth for client capabilities)
├── config.py            # Configuration — BASE_DIR, env loading
├── tools/               # MCP tool implementations (7 modules)
│   ├── add.py           # Math: add two numbers
│   ├── today.py         # Utility: UTC date/time
│   ├── web_research.py  # Web search (DDGS) + URL scraping (Trafilatura)
│   ├── files.py         # File ops: list, read, write, append, replace, insert, search, delete, remove dir
│   ├── run_command.py   # Shell command execution via Bubblewrap sandbox
│   ├── md_to_pdf.py     # Markdown → PDF conversion via WeasyPrint
│   └── sqlite_store.py  # Persistent context store + project change tracker (FTS5 indexed)
├── .env                 # Environment variables (optional; see `.env.example`)
└── .mcp_cache/          # Persistent SQLite database (context.db)
```

### Key Design Decisions

- **Single MCP endpoint** at `POST /mcp` handling `initialize`, `tools/list`, and `tools/call` methods
- **tools.json as schema source** — clients discover available tools from this file
- **Bubblewrap isolation** for `run_command` — read-only root filesystem, disposable `/tmp`, writable BASE_DIR bind mount
- **SQLite with FTS5** for both context store and project change tracker — full-text search built in
- **Project auto-detection** — pyproject.toml → git remote → directory name fallback chain

## Persistent Context Store

The `sqlite_store` module provides a cross-session knowledge base for LLM context using SQLite with FTS5 full-text search.

### Project Identification

Projects are auto-detected in this priority order:
1. `pyproject.toml[project].name` — Python-native, structured
2. Git remote origin URL — extracts repo name
3. Directory name — last resort fallback

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

## Project Change Tracker

A structured changelog system for tracking work progress, bugs fixed, refactors done, and milestones reached. Each change has timeline steps documenting what was done and when.

### Recording a Change

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"add_project_change","arguments":{"project":"bob","key":"accordion-refactor","change_type":"refactor","summary":"Move accordion from Tabs.js into TablesContainer"}}}
```

### Adding Timeline Steps

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add_change_step","arguments":{"change_key":"accordion-refactor","project":"bob","step":"investigate","date":"2026-05-03","details":"Found Tabs.js uses querySelectorAll at page load — breaks with .map() output","files_changed":"app/static/js/Tabs.js, app/ts/TablesContainer.tsx"}}}
```

### Viewing & Searching Changes

| Tool | Purpose |
|------|---------|
| `list_project_changes` | List changes with filters by type (`bugfix`, `refactor`, `feature`, `milestone`, `config`, `other`) and date range |
| `get_change_history` | Get full history for one change including all timeline steps |
| `search_project_changes` | Full-text search across change summaries (optionally scoped to project or type) |

## Todo Project Pattern
## Issues Tracker

A structured system for tracking bugs and observations with lifecycle status. Issues link to project changes via a many-to-many junction table.

### Storing an Issue

```json
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"store_issue","arguments":{"project":"mcp-server","key":"db-path-race","status":"open","title":"DB_PATH singleton race condition","description":"Module-level DB_PATH=None global causes duplicate connections under concurrent requests."}}}
```

### Updating Status

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"update_issue_status","arguments":{"project":"mcp-server","key":"db-path-race","status":"closed"}}}
```

### Querying Issues

| Tool | Purpose |
|------|---------|
| `query_issues` | Filter by project, status, or exact key; shows related change count |
| `list_issues` | List all issues for a project with optional status filter |

Status values: `open`, `closed`, `not-relevant`. The `issue_change_links` junction table tracks which project changes relate to which issues (many-to-many).


The `todo` project is used as a task list. Use descriptive keys so tasks are findable:

```json
// Store a pending task
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"store_context","arguments":{"key":"review-bob-metrics","content":"Review metrics section in BOB dashboard for accuracy before Friday review","project":"todo"}}}

// Mark as done
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"store_context","arguments":{"key":"review-bob-metrics","content":"DONE: Reviewed metrics, flagged 3 inconsistencies in citizenship data","project":"todo"}}}
```

## Sandbox Configuration

The `run_command` tool uses Bubblewrap for isolation:

- **Read-only root filesystem** (`--ro-bind / /`)
- **Disposable temp directory** (`--tmpfs /tmp`)
- **Writable resources directory** (`--bind resources resources`)
- **DNS resolution enabled** (no `/etc` restriction)

### Environment Variables Passed to Sandbox

| Variable | Value | Purpose |
|----------|-------|---------|
| `PATH` | `/usr/local/bin:/usr/bin:<user>/.local/bin` | Includes user-installed tools (e.g., `uv`) |
| `HOME` | `<user>` | User home directory (derived from env or BASE_DIR) |
| `UV_CACHE_DIR` | `/tmp/uv-cache` | Redirects uv's cache (host `~/.cache` is read-only) |

All other environment variables from the parent process are inherited but not explicitly set.

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

## Disclaimer

This project was developed with assistance from **Qwen 3.6**, a large language model by Alibaba Group's Tongyi Lab. While AI assisted in code generation, documentation, and refactoring, all technical decisions and final implementations were reviewed and validated by the human developer.

## License

MIT License
