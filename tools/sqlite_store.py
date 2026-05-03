"""
SQLite knowledge store for persistent LLM context.

Provides tools for storing and retrieving project-specific information
across sessions. Uses SQLite with FTS5 for full-text search.

Project identification:
  1. pyproject.toml[project].name (primary — Python-focused)
  2. git remote origin URL (fallback)
  3. directory name (last resort)
"""

import sqlite3
from pathlib import Path


# Database location — stored in workspace root
DB_PATH = None  # Set dynamically based on BASE_DIR


def _get_db_path():
    """Get the database path, creating it if needed."""
    global DB_PATH
    if DB_PATH is None:
        from config import BASE_DIR
        cache_dir = BASE_DIR / ".mcp_cache"
        cache_dir.mkdir(exist_ok=True)
        DB_PATH = cache_dir / "context.db"
    return DB_PATH


def _detect_project_id():
    """Detect project identity with fallback chain.
    
    Priority:
      1. pyproject.toml[project].name (Python-native, structured)
      2. git remote origin URL (extract repo name)
      3. directory name (last resort)
    """
    try:
        # 1. Try pyproject.toml first
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
                with open(pyproject, "rb") as f:
                    config = tomllib.load(f)
                name = config.get("project", {}).get("name")
                if name:
                    return name
            except Exception:
                pass
    
    except Exception:
        pass
    
    # 2. Fallback to git remote
    try:
        from config import BASE_DIR
        result = run_command(["git", "remote", "get-url", "origin"], cwd=BASE_DIR, timeout=3)
        if result.strip():
            url = result.strip()
            repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
            return repo_name
    except Exception:
        pass
    
    # 3. Last resort: directory name
    try:
        from config import BASE_DIR
        return BASE_DIR.name
    except Exception:
        return "unknown-project"


def _init_db(conn: sqlite3.Connection):
    """Initialize database schema if tables don't exist."""
    cursor = conn.cursor()
    
    # Main context table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            project TEXT
        )
    """)
    
    # Full-text search virtual table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS context_fts 
        USING fts5(key, content, project, content='context', content_rowid='id')
    """)
    
    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS context_ai AFTER INSERT ON context
        BEGIN
            INSERT INTO context_fts(rowid, key, content, project)
            VALUES (new.id, new.key, new.content, new.project);
        END
    """)
    
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS context_ad AFTER DELETE ON context
        BEGIN
            INSERT INTO context_fts(context_fts, rowid, key, content, project)
            VALUES ('delete', old.id, old.key, old.content, old.project);
        END
    """)
    
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS context_au AFTER UPDATE ON context
        BEGIN
            INSERT INTO context_fts(context_fts, rowid, key, content, project)
            VALUES ('delete', old.id, old.key, old.content, old.project);
            INSERT INTO context_fts(rowid, key, content, project)
            VALUES (new.id, new.key, new.content, new.project);
        END
    """)
    
    conn.commit()


# ─── Tool Handlers ───────────────────────────────────────────────

async def handle_store_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Store or update project context in SQLite knowledge base."""
    key = args.get("key", "")
    content = args.get("content", "")
    project = args.get("project", None)
    
    if not key or not content:
        return _tool_response(request_id, "Error: 'key' and 'content' are required.")
    
    # Resolve project ID
    if project and project != "default":
        resolved_project = project
    else:
        resolved_project = _detect_project_id()
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Check if key exists for this project
        cursor.execute(
            "SELECT id FROM context WHERE key = ? AND project = ?",
            (key, resolved_project)
        )
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute(
                "UPDATE context SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ? AND project = ?",
                (content, key, resolved_project)
            )
            action = "Updated"
        else:
            cursor.execute(
                "INSERT INTO context (key, content, project) VALUES (?, ?, ?)",
                (key, content, resolved_project)
            )
            action = "Stored"
        
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"{action} context '{key}' for project '{resolved_project}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"store_context failed: {e}")
        return _tool_response(request_id, f"Error storing context: {str(e)}")


async def handle_query_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Query stored context using keyword search or direct key lookup."""
    project_arg = args.get("project", None)
    keyword = args.get("keyword", "")
    key = args.get("key", None)
    
    # Resolve project ID
    if project_arg and project_arg != "default":
        resolved_project = project_arg
    else:
        resolved_project = _detect_project_id()
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        results = []
        
        if keyword:
            # Full-text search scoped to project
            cursor.execute(
                """SELECT c.key, c.content, c.updated_at 
                   FROM context c 
                   JOIN context_fts f ON c.id = f.rowid 
                   WHERE context_fts MATCH ? AND c.project = ?""",
                (keyword, resolved_project)
            )
            rows = cursor.fetchall()
            for row in rows:
                results.append({
                    "key": row[0],
                    "content": row[1],
                    "updated_at": row[2]
                })
        elif key:
            # Direct lookup
            cursor.execute(
                "SELECT key, content, updated_at FROM context WHERE key = ? AND project = ?",
                (key, resolved_project)
            )
            row = cursor.fetchone()
            if row:
                results.append({
                    "key": row[0],
                    "content": row[1],
                    "updated_at": row[2]
                })
        else:
            # List all keys for this project
            cursor.execute(
                "SELECT key, updated_at FROM context WHERE project = ? ORDER BY updated_at DESC",
                (resolved_project,)
            )
            rows = cursor.fetchall()
            for row in rows:
                results.append({"key": row[0], "updated_at": row[1]})
        
        conn.close()
        
        if not results:
            return _tool_response(request_id, f"No context found for project '{resolved_project}'.")
        
        formatted = "\n\n".join([
            f"Key: {r['key']}\nUpdated: {r['updated_at']}\nContent:\n{r.get('content', 'N/A')}"
            for r in results[:20]  # Limit to 20 results
        ])
        
        return _tool_response(request_id, formatted)
        
    except Exception as e:
        if logger:
            logger.error(f"query_context failed: {e}")
        return _tool_response(request_id, f"Error querying context: {str(e)}")


async def handle_clear_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Clear stored context entries."""
    project_arg = args.get("project", None)
    key = args.get("key", None)
    
    # Resolve project ID
    if project_arg and project_arg != "default":
        resolved_project = project_arg
    else:
        resolved_project = _detect_project_id()
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        if key:
            cursor.execute(
                "DELETE FROM context WHERE key = ? AND project = ?",
                (key, resolved_project)
            )
        else:
            cursor.execute(
                "DELETE FROM context WHERE project = ?",
                (resolved_project,)
            )
        
        deleted = cursor.rowcount
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Cleared {deleted} context entry/entries for project '{resolved_project}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"clear_context failed: {e}")
        return _tool_response(request_id, f"Error clearing context: {str(e)}")


async def handle_list_projects(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """List all known projects in the context store."""
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT DISTINCT project, MAX(updated_at) as last_updated FROM context GROUP BY project ORDER BY last_updated DESC"
        )
        rows = cursor.fetchall()
        
        conn.close()
        
        if not rows:
            return _tool_response(request_id, "No context entries found. Use 'store_context' to add entries.")
        
        formatted = "\n".join([
            f"- {row[0]} (last updated: {row[1]})" for row in rows
        ])
        
        return _tool_response(request_id, f"Known projects ({len(rows)}):\n{formatted}")
        
    except Exception as e:
        if logger:
            logger.error(f"list_projects failed: {e}")
        return _tool_response(request_id, f"Error listing projects: {str(e)}")
