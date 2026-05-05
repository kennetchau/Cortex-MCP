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
            project TEXT,
            UNIQUE(key, project)
        )
    """)
    
    # Alias table — maps alternate names to canonical context entries
    # Migration: check if table exists (handles upgrades from pre-alias versions)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='context_aliases'")
    if not cursor.fetchone():
        cursor.execute("""
            CREATE TABLE context_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context_id INTEGER NOT NULL REFERENCES context(id) ON DELETE CASCADE,
                alias_key TEXT NOT NULL,
                project TEXT NOT NULL
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

def _resolve_key(conn: sqlite3.Connection, key: str, project: str):
    """Resolve a key to its canonical context entry.
    
    Returns (context_id, is_alias) or (None, False) if not found.
    Lookup order: canonical key first → aliases second.
    """
    cursor = conn.cursor()
    
    # Step 1: Check canonical key
    cursor.execute(
        "SELECT id FROM context WHERE key = ? AND project = ?",
        (key, project)
    )
    row = cursor.fetchone()
    if row:
        return row[0], False
    
    # Step 2: Check aliases
    cursor.execute(
        "SELECT context_id FROM context_aliases WHERE alias_key = ? AND project = ?",
        (key, project)
    )
    row = cursor.fetchone()
    if row:
        return row[0], True
    
    return None, False


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
        
        # Resolve key → canonical context entry
        context_id, is_alias = _resolve_key(conn, key, resolved_project)
        
        if context_id:
            cursor.execute(
                "UPDATE context SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content, context_id)
            )
            target_label = f"'{key}'" + (" (via alias)" if is_alias else "")
            action = "Updated"
        else:
            cursor.execute(
                "INSERT INTO context (key, content, project) VALUES (?, ?, ?)",
                (key, content, resolved_project)
            )
            action = "Stored"
            target_label = f"'{key}'"
        
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"{action} context {target_label} for project '{resolved_project}'.")
        
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
            # Resolve key → canonical entry (checks aliases too)
            context_id, is_alias = _resolve_key(conn, key, resolved_project)
            if context_id:
                cursor.execute(
                    "SELECT key, content, updated_at FROM context WHERE id = ?",
                    (context_id,)
                )
                row = cursor.fetchone()
                if row:
                    result = {
                        "key": row[0],
                        "content": row[1],
                        "updated_at": row[2]
                    }
                    if is_alias:
                        result["matched_via"] = f"alias '{key}'"
                    results.append(result)
        else:
            # List all entries for this project (include content)
            cursor.execute(
                "SELECT key, content, updated_at FROM context WHERE project = ? ORDER BY updated_at DESC",
                (resolved_project,)
            )
            rows = cursor.fetchall()
            for row in rows:
                results.append({"key": row[0], "content": row[1], "updated_at": row[2]})
        
        conn.close()
        
        if not results:
            return _tool_response(request_id, f"No context found for project '{resolved_project}'.")
        
        formatted = "\n\n".join([
            f"Key: {r['key']}\nUpdated: {r['updated_at']}\nContent:\n{r.get('content', 'N/A')}" + (f"\nMatched via: {r['matched_via']}" if "matched_via" in r else "")
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
            # Resolve key → canonical id (handles aliases via CASCADE DELETE)
            context_id, is_alias = _resolve_key(conn, key, resolved_project)
            if context_id:
                cursor.execute("DELETE FROM context WHERE id = ?", (context_id,))
                label = f"'{key}'" + (" (via alias)" if is_alias else "")
                deleted = cursor.rowcount
            else:
                deleted = 0
                label = f"'{key}'"
        else:
            cursor.execute(
                "DELETE FROM context WHERE project = ?",
                (resolved_project,)
            )
            deleted = cursor.rowcount
            label = f"all entries"
        
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Cleared {deleted} context entry/entries for project '{resolved_project}' ({label}).")
        
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

async def handle_add_context_alias(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Add an alternate name (alias) for an existing context entry."""
    context_key = args.get("context_key", "")
    alias_name = args.get("alias_name", "")
    project = args.get("project", None)
    
    if not context_key or not alias_name:
        return _tool_response(request_id, "Error: 'context_key' and 'alias_name' are required.")
    
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
        
        # Check canonical key exists
        cursor.execute(
            "SELECT id FROM context WHERE key = ? AND project = ?",
            (context_key, resolved_project)
        )
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return _tool_response(request_id, f"Context key '{context_key}' not found for project '{resolved_project}'.")
        
        context_id = row[0]
        
        # Check alias doesn't already exist
        cursor.execute(
            "SELECT id FROM context_aliases WHERE alias_key = ? AND project = ?",
            (alias_name, resolved_project)
        )
        if cursor.fetchone():
            conn.close()
            return _tool_response(request_id, f"Alias '{alias_name}' already exists for project '{resolved_project}'.")
        
        # Insert alias
        cursor.execute(
            "INSERT INTO context_aliases (context_id, alias_key, project) VALUES (?, ?, ?)",
            (context_id, alias_name, resolved_project)
        )
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Added alias '{alias_name}' → '{context_key}' for project '{resolved_project}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"add_context_alias failed: {e}")
        return _tool_response(request_id, f"Error adding alias: {str(e)}")
