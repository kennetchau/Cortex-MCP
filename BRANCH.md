# workspace-llm Branch

This branch contains the MCP server adapted for the LLM workspace environment.

## Purpose
- Full sandbox support with configurable base directory
- All tools (file ops, git, uv, web search) working from `<workspace>`
- Git operations functional inside bubblewrap sandbox via HTTPS + PAT auth

## Key Differences from main
| Feature | main | workspace-llm |
|---------|------|---------------|
| Base directory | Hardcoded `resources/` | Configurable via `.env` / `MCP_BASE_DIR` |
| Dependencies | Standard deps | Includes `python-dotenv` |
| Git in sandbox | Read-only `.git` | Full read/write support |
| Tool descriptions | Reference "resources" | Reference "sandbox base" |

## Configuration
Set the sandbox base directory via:
- `.env` file: `MCP_BASE_DIR=/path/to/workspace`
- Environment variable: `export MCP_BASE_DIR=/path/to/workspace`

## Git Authentication
Uses GitHub Personal Access Token (PAT) in remote URL for push/pull without SSH keys.
