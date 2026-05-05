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
    
    # ─── Project Changes Tables ──────────────────────────────────────

    # Main changes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS project_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            key TEXT NOT NULL,
            change_type TEXT NOT NULL CHECK(change_type IN ('bugfix', 'refactor', 'feature', 'milestone', 'config', 'other')),
            summary TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project, key)
        )
    """)

    # Timeline details table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS project_change_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
            step TEXT NOT NULL,
            date DATE NOT NULL,
            details TEXT,
            files_changed TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # FTS virtual table for searching summaries
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS project_changes_fts 
        USING fts5(summary, project, content='project_changes', content_rowid='id')
    """)

    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS pc_ai AFTER INSERT ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(rowid, summary, project)
            VALUES (new.id, new.summary, new.project);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS pc_ad AFTER DELETE ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(project_changes_fts, rowid, summary, project)
            VALUES ('delete', old.id, old.summary, old.project);
        END
    """)

    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS pc_au AFTER UPDATE ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(project_changes_fts, rowid, summary, project)
            VALUES ('delete', old.id, old.summary, old.project);
            INSERT INTO project_changes_fts(rowid, summary, project)
            VALUES (new.id, new.summary, new.project);
        END
    """)
    
    # ─── Issues Tables ────────────────────────────────────────────────

    # Main issues table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open', 'closed', 'not-relevant')),
            title TEXT NOT NULL,
            description TEXT,
            fixed_in_commit TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project, key)
        )
    """)

    # Junction table — many-to-many between issues and project changes
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS issue_change_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_id, change_id)
        )
    """)
    
    conn.commit()


def _fts_quote(term: str) -> str:
    """Quote a term for FTS5 MATCH so special chars (like -) are treated as literals."""
    return f'"{term}"'


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
    limit = args.get("limit", 20)
    sort_by = args.get("sort_by", "updated_at")  # "updated_at" or "key"
    
    # Resolve project ID: explicit value → scoped; missing → cross-project search
    if project_arg and project_arg != "default":
        resolved_project = project_arg
        cross_project = False
    elif project_arg is None or project_arg == "":
        cross_project = True
    else:
        resolved_project = _detect_project_id()
        cross_project = False
    
    # Validate sort parameter
    valid_sorts = ("updated_at", "key")
    if sort_by not in valid_sorts:
        return _tool_response(request_id, f"Error: 'sort_by' must be one of: {', '.join(valid_sorts)}")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        results = []
        
        if keyword:
            # Full-text search — scoped to project or global
            # Wrap in double quotes so FTS5 treats special chars (like -) as literals
            # e.g., "mcp-server" won't be parsed as "mcp -server" (exclude server)
            quoted_keyword = f'"{keyword}"'
            if cross_project:
                cursor.execute(
                    f"""SELECT c.project, c.key, c.content, c.updated_at 
                        FROM context c 
                        JOIN context_fts f ON c.id = f.rowid 
                        WHERE context_fts MATCH ?
                        ORDER BY c.{sort_by} DESC
                        LIMIT ?""",
                    (quoted_keyword, limit)
                )
            else:
                cursor.execute(
                    f"""SELECT c.project, c.key, c.content, c.updated_at 
                        FROM context c 
                        JOIN context_fts f ON c.id = f.rowid 
                        WHERE context_fts MATCH ? AND c.project = ?
                        ORDER BY c.{sort_by} DESC
                        LIMIT ?""",
                    (quoted_keyword, resolved_project, limit)
                )
            rows = cursor.fetchall()
            for row in rows:
                results.append({
                    "project": row[0],
                    "key": row[1],
                    "content": row[2],
                    "updated_at": row[3]
                })
        elif key:
            # Resolve key → canonical entry (checks aliases too)
            context_id, is_alias = _resolve_key(conn, key, resolved_project)
            if context_id:
                cursor.execute(
                    "SELECT project, key, content, updated_at FROM context WHERE id = ?",
                    (context_id,)
                )
                row = cursor.fetchone()
                if row:
                    result = {
                        "project": row[0],
                        "key": row[1],
                        "content": row[2],
                        "updated_at": row[3]
                    }
                    if is_alias:
                        result["matched_via"] = f"alias '{key}'"
                    results.append(result)
        else:
            # List all entries — scoped to project or global
            sort_clause = f"ORDER BY c.{sort_by} DESC" if sort_by == "key" else "ORDER BY c.updated_at DESC"
            if cross_project:
                cursor.execute(
                    f"SELECT c.project, c.key, c.content, c.updated_at FROM context c {sort_clause} LIMIT ?",
                    (limit,)
                )
            else:
                cursor.execute(
                    f"SELECT c.project, c.key, c.content, c.updated_at FROM context c WHERE c.project = ? {sort_clause} LIMIT ?",
                    (resolved_project, limit)
                )
            rows = cursor.fetchall()
            for row in rows:
                results.append({"project": row[0], "key": row[1], "content": row[2], "updated_at": row[3]})
        
        conn.close()
        
        if not results:
            if cross_project:
                return _tool_response(request_id, "No context found.")
            return _tool_response(request_id, f"No context found for project '{resolved_project}'.")
        
        formatted = "\n\n".join([
            f"Project: {r['project']}\nKey: {r['key']}\nUpdated: {r['updated_at']}\nContent:\n{r.get('content', 'N/A')}" + (f"\nMatched via: {r['matched_via']}" if "matched_via" in r else "")
            for r in results
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
        
        # Query both context and project_changes tables, merge and deduplicate
        cursor.execute("""
            SELECT project, MAX(updated_at) as last_updated FROM context GROUP BY project
            UNION ALL
            SELECT project, MAX(created_at) as last_updated FROM project_changes GROUP BY project
        """)
        all_rows = cursor.fetchall()
        
        # Deduplicate by project, keeping the latest timestamp
        project_map = {}
        for project, updated_at in all_rows:
            if project not in project_map or (updated_at and project_map[project] and updated_at > project_map[project]):
                project_map[project] = updated_at
        
        rows = [(proj, ts) for proj, ts in project_map.items()]
        rows.sort(key=lambda x: x[1] or "", reverse=True)
        
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

# ─── Project Change Tracking Handlers ──────────────────────────────

async def handle_add_project_change(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Record a new project change entry."""
    project = args.get("project", "")
    key = args.get("key", "")
    change_type = args.get("change_type", "other")
    summary = args.get("summary", "")
    
    if not project or not key or not change_type or not summary:
        return _tool_response(request_id, "Error: 'project', 'key', 'change_type', and 'summary' are required.")
    
    valid_types = ('bugfix', 'refactor', 'feature', 'milestone', 'config', 'other')
    if change_type not in valid_types:
        return _tool_response(request_id, f"Error: 'change_type' must be one of: {', '.join(valid_types)}")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Check for duplicate (project, key)
        cursor.execute(
            "SELECT id FROM project_changes WHERE project = ? AND key = ?",
            (project, key)
        )
        if cursor.fetchone():
            conn.close()
            return _tool_response(request_id, f"Change '{key}' already exists for project '{project}'.")
        
        cursor.execute(
            "INSERT INTO project_changes (project, key, change_type, summary) VALUES (?, ?, ?, ?)",
            (project, key, change_type, summary)
        )
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Added change '{key}' to project '{project}' ({change_type}).")
        
    except Exception as e:
        if logger:
            logger.error(f"add_project_change failed: {e}")
        return _tool_response(request_id, f"Error adding project change: {str(e)}")


async def handle_add_change_step(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Add a timeline step to an existing project change."""
    change_key = args.get("change_key", "")
    project = args.get("project", "")
    step = args.get("step", "")
    date = args.get("date", "")
    details = args.get("details", "")
    files_changed = args.get("files_changed", "")
    
    if not change_key or not project or not step or not date:
        return _tool_response(request_id, "Error: 'change_key', 'project', 'step', and 'date' are required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Find the change
        cursor.execute(
            "SELECT id FROM project_changes WHERE project = ? AND key = ?",
            (project, change_key)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Change '{change_key}' not found for project '{project}'.")
        
        change_id = row[0]
        
        cursor.execute(
            "INSERT INTO project_change_details (change_id, step, date, details, files_changed) VALUES (?, ?, ?, ?, ?)",
            (change_id, step, date, details, files_changed)
        )
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Added step '{step}' to change '{change_key}' in '{project}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"add_change_step failed: {e}")
        return _tool_response(request_id, f"Error adding change step: {str(e)}")


async def handle_list_project_changes(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """List changes for a project with optional filters."""
    project = args.get("project", "")
    change_type = args.get("change_type", None)
    date_from = args.get("date_from", None)
    date_to = args.get("date_to", None)
    
    if not project:
        return _tool_response(request_id, "Error: 'project' is required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        query = "SELECT id, key, change_type, summary, created_at FROM project_changes WHERE project = ?"
        params = [project]
        
        if change_type:
            query += " AND change_type = ?"
            params.append(change_type)
        if date_from:
            query += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND created_at <= ?"
            params.append(date_to)
        
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return _tool_response(request_id, f"No changes found for project '{project}'.")
        
        formatted = "\n".join([
            f"- [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows
        ])
        
        return _tool_response(request_id, f"Project changes for '{project}' ({len(rows)}):\n{formatted}")
        
    except Exception as e:
        if logger:
            logger.error(f"list_project_changes failed: {e}")
        return _tool_response(request_id, f"Error listing project changes: {str(e)}")


async def handle_get_change_history(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Get full history for one change including timeline steps."""
    change_key = args.get("change_key", "")
    project = args.get("project", "")
    
    if not change_key or not project:
        return _tool_response(request_id, "Error: 'change_key' and 'project' are required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Get the change
        cursor.execute(
            "SELECT id, key, change_type, summary, created_at FROM project_changes WHERE project = ? AND key = ?",
            (project, change_key)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Change '{change_key}' not found for project '{project}'.")
        
        change_id, key, change_type, summary, created_at = row
        
        # Get timeline steps
        cursor.execute(
            "SELECT step, date, details, files_changed FROM project_change_details WHERE change_id = ? ORDER BY date ASC",
            (change_id,)
        )
        steps = cursor.fetchall()
        conn.close()
        
        formatted = f"Change: {key}\nType: {change_type}\nSummary: {summary}\nCreated: {created_at}\n\nTimeline:\n"
        for step_name, step_date, step_details, step_files in steps:
            formatted += f"\n  [{step_date}] {step_name}"
            if step_details:
                formatted += f"\n    {step_details}"
            if step_files:
                formatted += f"\n    Files: {step_files}"
        
        return _tool_response(request_id, formatted)
        
    except Exception as e:
        if logger:
            logger.error(f"get_change_history failed: {e}")
        return _tool_response(request_id, f"Error getting change history: {str(e)}")


async def handle_search_project_changes(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """FTS search across project changes."""
    query_text = args.get("query", "")
    project = args.get("project", None)
    change_type = args.get("change_type", None)
    
    if not query_text:
        return _tool_response(request_id, "Error: 'query' is required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # FTS search on summary — quote to handle special chars like - in project names
        quoted_query = _fts_quote(query_text)
        if project and change_type:
            cursor.execute(
                """SELECT pc.project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                   WHERE project_changes_fts MATCH ? AND pc.project = ? AND pc.change_type = ?
                   ORDER BY pcf.rank""",
                (quoted_query, project, change_type)
            )
        elif project:
            cursor.execute(
                """SELECT pc.project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                   WHERE project_changes_fts MATCH ? AND pc.project = ?
                   ORDER BY pcf.rank""",
                (quoted_query, project)
            )
        else:
            cursor.execute(
                """SELECT pc.project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                   WHERE project_changes_fts MATCH ?
                   ORDER BY pcf.rank""",
                (quoted_query,)
            )
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return _tool_response(request_id, f"No changes found matching '{query_text}'.")
        
        formatted = "\n".join([
            f"- [{row[0]}] [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows[:20]  # Limit to 20 results
        ])
        
        return _tool_response(request_id, f"Search results for '{query_text}' ({len(rows)} matches):\n{formatted}")
        
    except Exception as e:
        if logger:
            logger.error(f"search_project_changes failed: {e}")
        return _tool_response(request_id, f"Error searching project changes: {str(e)}")

# ─── Issues Handlers ────────────────────────────────────────────────

async def handle_store_issue(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Store or update an issue in the issues table."""
    project = args.get("project", None)
    key = args.get("key", "")
    status = args.get("status", "open")
    title = args.get("title", "")
    description = args.get("description", "")
    fixed_in_commit = args.get("fixed_in_commit", None)

    if not key or not title:
        return _tool_response(request_id, "Error: 'key' and 'title' are required.")

    valid_statuses = ("open", "closed", "not-relevant")
    if status not in valid_statuses:
        return _tool_response(request_id, f"Error: 'status' must be one of: {', '.join(valid_statuses)}")

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

        # Check if issue already exists
        cursor.execute(
            "SELECT id FROM issues WHERE project = ? AND key = ?",
            (resolved_project, key)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE issues 
                SET status = ?, title = ?, description = ?, fixed_in_commit = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, title, description, fixed_in_commit, existing[0]))
            action = "Updated"
        else:
            cursor.execute("""
                INSERT INTO issues (project, key, status, title, description, fixed_in_commit)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (resolved_project, key, status, title, description, fixed_in_commit))
            action = "Stored"

        conn.commit()
        conn.close()

        return _tool_response(request_id, f"{action} issue '{key}' for project '{resolved_project}' (status: {status}).")

    except Exception as e:
        if logger:
            logger.error(f"store_issue failed: {e}")
        return _tool_response(request_id, f"Error storing issue: {str(e)}")


async def handle_query_issues(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Query issues with optional filters by project, status, or key."""
    project_arg = args.get("project", None)
    status_filter = args.get("status", None)
    key_filter = args.get("key", None)

    # Resolve project ID
    if project_arg and project_arg != "default":
        resolved_project = project_arg
    elif project_arg is None or project_arg == "":
        resolved_project = None  # Cross-project query
    else:
        resolved_project = _detect_project_id()

    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        query = """
            SELECT i.project, i.key, i.status, i.title, i.description, 
                   i.fixed_in_commit, i.created_at,
                   (SELECT COUNT(*) FROM issue_change_links WHERE issue_id = i.id) as related_changes
            FROM issues i
            WHERE 1=1
        """
        params = []

        if resolved_project:
            query += " AND i.project = ?"
            params.append(resolved_project)
        if status_filter:
            query += " AND i.status = ?"
            params.append(status_filter)
        if key_filter:
            query += " AND i.key = ?"
            params.append(key_filter)

        query += " ORDER BY i.created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            filters = []
            if resolved_project:
                filters.append(f"project='{resolved_project}'")
            if status_filter:
                filters.append(f"status='{status_filter}'")
            if key_filter:
                filters.append(f"key='{key_filter}'")
            filter_str = ", ".join(filters) if filters else "no filters"
            return _tool_response(request_id, f"No issues found ({filter_str}).")

        formatted = "\n".join([
            f"- [{row[0]}] [{row[2]}] {row[1]} — {row[3]}"
            + (f" (fixed in {row[5]})" if row[5] else "")
            + f" | related changes: {row[7]}"
            for row in rows
        ])

        return _tool_response(request_id, f"Issues ({len(rows)}):\n{formatted}")

    except Exception as e:
        if logger:
            logger.error(f"query_issues failed: {e}")
        return _tool_response(request_id, f"Error querying issues: {str(e)}")


async def handle_update_issue_status(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Update the status of an existing issue."""
    project = args.get("project", None)
    key = args.get("key", "")
    new_status = args.get("status", None)

    if not key or not new_status:
        return _tool_response(request_id, "Error: 'key' and 'status' are required.")

    valid_statuses = ("open", "closed", "not-relevant")
    if new_status not in valid_statuses:
        return _tool_response(request_id, f"Error: 'status' must be one of: {', '.join(valid_statuses)}")

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

        # Find the issue
        cursor.execute(
            "SELECT id, status FROM issues WHERE project = ? AND key = ?",
            (resolved_project, key)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return _tool_response(request_id, f"Issue '{key}' not found for project '{resolved_project}'.")

        old_status = row[1]
        issue_id = row[0]

        cursor.execute(
            "UPDATE issues SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, issue_id)
        )
        conn.commit()
        conn.close()

        return _tool_response(request_id, f"Issue '{key}' status changed: '{old_status}' → '{new_status}'.")

    except Exception as e:
        if logger:
            logger.error(f"update_issue_status failed: {e}")
        return _tool_response(request_id, f"Error updating issue status: {str(e)}")


async def handle_list_issues(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """List all issues with optional filters by project and status."""
    project_arg = args.get("project", None)
    status_filter = args.get("status", None)

    # Resolve project ID
    if project_arg and project_arg != "default":
        resolved_project = project_arg
    elif project_arg is None or project_arg == "":
        resolved_project = _detect_project_id()
    else:
        resolved_project = _detect_project_id()

    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        query = "SELECT id, key, status, title, created_at FROM issues WHERE project = ?"
        params = [resolved_project]

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        query += " ORDER BY created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return _tool_response(request_id, f"No issues found for project '{resolved_project}'.")

        formatted = "\n".join([
            f"- [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows
        ])

        return _tool_response(request_id, f"Issues for '{resolved_project}' ({len(rows)}):\n{formatted}")

    except Exception as e:
        if logger:
            logger.error(f"list_issues failed: {e}")
        return _tool_response(request_id, f"Error listing issues: {str(e)}")
