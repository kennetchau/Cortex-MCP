"""
SQLite knowledge store for persistent LLM context.

Provides tools for storing and retrieving project-specific information
across sessions. Uses SQLite with FTS5 for full-text search.

Project identification:
  1. pyproject.toml[project].name (primary — Python-focused)
  2. git remote origin URL (fallback)
  3. directory name (last resort)
"""

import re
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
    """Detect project identity and resolve to ID.
    
    Priority:
      1. pyproject.toml[project].name (Python-native, structured)
      2. git remote origin URL (extract repo name)
      3. directory name (last resort)
    
    Returns integer project_id by looking up the detected name in the projects table.
    """
    # First detect the name using the old logic
    detected_name = None
    try:
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
                with open(pyproject, "rb") as f:
                    config = tomllib.load(f)
                detected_name = config.get("project", {}).get("name")
            except Exception:
                pass
    
        if not detected_name:
            from config import BASE_DIR
            result = run_command(["git", "remote", "get-url", "origin"], cwd=BASE_DIR, timeout=3)
            if result.strip():
                url = result.strip()
                detected_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        
        if not detected_name:
            from config import BASE_DIR
            detected_name = str(BASE_DIR.name)
    except Exception:
        detected_name = "unknown-project"
    
    # Now resolve name → ID via projects table
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    _init_db(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE name = ?", (detected_name,))
    row = cursor.fetchone()
    project_id = row[0] if row else None
    conn.close()
    
    if project_id is None:
        # Create it for backward compat
        return _resolve_project_id(detected_name)
    
    return project_id


def _resolve_project_id(project_name):
    """Resolve project name to ID. Creates if doesn't exist."""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    _init_db(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
    row = cursor.fetchone()
    if row:
        conn.close()
        return row[0]
    try:
        cursor.execute("INSERT INTO projects (name) VALUES (?)", (project_name,))
        last_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return last_id
    except sqlite3.IntegrityError:
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        last_id = cursor.fetchone()[0]
        conn.close()
        return last_id


def _init_db(conn: sqlite3.Connection):
    """Initialize database schema if tables don't exist.
    
    Handles both legacy schema (project TEXT) and migrated schema (project_id INTEGER).
    After migration, checks for projects table and project_id columns to determine mode.
    """
    cursor = conn.cursor()
    
    # Check if we've already migrated (projects table exists with data)
    cursor.execute("SELECT COUNT(*) FROM projects")
    has_projects_table = cursor.fetchone()[0] > 0
    
    if has_projects_table:
        _init_migrated_schema(cursor, conn)
    else:
        _init_legacy_schema(cursor, conn)
    
    conn.commit()


def _init_legacy_schema(cursor, conn):
    """Initialize the original schema (pre-migration)."""
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
    
    # Alias table
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
    
    # FTS virtual table
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS context_fts 
        USING fts5(key, content, project, content='context', content_rowid='id')
    """)
    
    # Triggers
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
    
    # Project Changes Tables
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

    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS project_changes_fts 
        USING fts5(summary, project, content='project_changes', content_rowid='id')
    """)

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
    
    # Issues Tables
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        cursor.execute("DROP INDEX IF EXISTS sqlite_autoindex_issues_1")
    except Exception:
        pass

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS issue_change_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_id, change_id)
        )
    """)


def _init_migrated_schema(cursor, conn):
    """Initialize schema for post-migration state."""
    # Ensure projects table exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # context_fts (without content= param — manual trigger sync)
    cursor.execute("DROP TABLE IF EXISTS context_fts")
    for t in ["context_fts_config", "context_fts_data", "context_fts_docsize", "context_fts_idx"]:
        try: cursor.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception: pass
    
    cursor.execute("""
        CREATE VIRTUAL TABLE context_fts USING fts5(key, content, project_name)
    """)
    
    # Repopulate FTS from existing data
    cursor.execute("""
        INSERT OR IGNORE INTO context_fts(rowid, key, content, project_name)
        SELECT c.id, c.key, c.content, p.name
        FROM context c
        JOIN projects p ON c.project_id = p.id
    """)
    
    # Recreate triggers
    cursor.execute("DROP TRIGGER IF EXISTS context_ai")
    cursor.execute("DROP TRIGGER IF EXISTS context_ad")
    cursor.execute("DROP TRIGGER IF EXISTS context_au")
    
    cursor.execute("""
        CREATE TRIGGER context_ai AFTER INSERT ON context
        BEGIN
            INSERT INTO context_fts(rowid, key, content, project_name)
            VALUES (new.id, new.key, new.content, 
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """)
    cursor.execute("""
        CREATE TRIGGER context_ad AFTER DELETE ON context
        BEGIN
            INSERT INTO context_fts(context_fts, rowid, key, content, project_name)
            VALUES ('delete', old.id, old.key, old.content,
                    (SELECT name FROM projects WHERE id = old.project_id));
        END
    """)
    cursor.execute("""
        CREATE TRIGGER context_au AFTER UPDATE ON context
        BEGIN
            INSERT INTO context_fts(context_fts, rowid, key, content, project_name)
            VALUES ('delete', old.id, old.key, old.content,
                    (SELECT name FROM projects WHERE id = old.project_id));
            INSERT INTO context_fts(rowid, key, content, project_name)
            VALUES (new.id, new.key, new.content,
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """)
    
    # project_changes_fts
    cursor.execute("DROP TABLE IF EXISTS project_changes_fts")
    for t in ["project_changes_fts_config", "project_changes_fts_data", "project_changes_fts_docsize", "project_changes_fts_idx"]:
        try: cursor.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception: pass
    
    cursor.execute("""
        CREATE VIRTUAL TABLE project_changes_fts USING fts5(summary, project_name)
    """)
    
    cursor.execute("DROP TRIGGER IF EXISTS pc_ai")
    cursor.execute("DROP TRIGGER IF EXISTS pc_ad")
    cursor.execute("DROP TRIGGER IF EXISTS pc_au")
    
    cursor.execute("""
        CREATE TRIGGER pc_ai AFTER INSERT ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(rowid, summary, project_name)
            VALUES (new.id, new.summary, 
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """)
    cursor.execute("""
        CREATE TRIGGER pc_ad AFTER DELETE ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(project_changes_fts, rowid, summary, project_name)
            VALUES ('delete', old.id, old.summary,
                    (SELECT name FROM projects WHERE id = old.project_id));
        END
    """)
    cursor.execute("""
        CREATE TRIGGER pc_au AFTER UPDATE ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(project_changes_fts, rowid, summary, project_name)
            VALUES ('delete', old.id, old.summary,
                    (SELECT name FROM projects WHERE id = old.project_id));
            INSERT INTO project_changes_fts(rowid, summary, project_name)
            VALUES (new.id, new.summary,
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """)
    
    # Create backward-compat views if they don't exist
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS context_with_project AS
        SELECT c.id, c.key, c.content, c.updated_at, p.name as project
        FROM context c
        JOIN projects p ON c.project_id = p.id
    """)
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS project_changes_with_project AS
        SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at, pc.updated_at, p.name as project
        FROM project_changes pc
        JOIN projects p ON pc.project_id = p.id
    """)
    cursor.execute("""
        CREATE VIEW IF NOT EXISTS issues_with_project AS
        SELECT i.id, i.key, i.status, i.title, i.description, i.fixed_in_commit, i.created_at, i.updated_at, p.name as project
        FROM issues i
        JOIN projects p ON i.project_id = p.id
    """)


def _fts_quote(term: str) -> str:
    """Quote a term for FTS5 MATCH so special chars (like -) are treated as literals."""
    return f'"{term}"'


def _resolve_key(conn: sqlite3.Connection, key: str, project_id: int):
    """Resolve a key to its canonical context entry.
    
    Returns (context_id, is_alias) or (None, False) if not found.
    Lookup order: canonical key first → aliases second.
    """
    cursor = conn.cursor()
    
    # Step 1: Check canonical key
    cursor.execute(
        "SELECT id FROM context WHERE key = ? AND project_id = ?",
        (key, project_id)
    )
    row = cursor.fetchone()
    if row:
        return row[0], False
    
    # Step 2: Check aliases
    cursor.execute(
        "SELECT context_id FROM context_aliases WHERE alias_key = ? AND project_id = ?",
        (key, project_id)
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
    project_name = args.get("project", None)

    if not key or not content:
        return _tool_response(request_id, "Error: 'key' and 'content' are required.")

    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project_name and project_name != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_name
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        # Resolve key → canonical context entry
        context_id, is_alias = _resolve_key(conn, key, resolved_project_id)

        if context_id:
            cursor.execute(
                "UPDATE context SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content, context_id)
            )
            target_label = f"'{key}'" + (" (via alias)" if is_alias else "")
            action = "Updated"
        else:
            cursor.execute(
                "INSERT INTO context (key, content, project_id) VALUES (?, ?, ?)",
                (key, content, resolved_project_id)
            )
            action = "Stored"
            target_label = f"'{key}'"

        conn.commit()
        conn.close()

        return _tool_response(request_id, f"{action} context {target_label} for project '{resolved_project_name}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"store_context failed: {e}")
        return _tool_response(request_id, f"Error storing context: {str(e)}")


async def handle_query_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Query stored context using keyword search or direct key lookup.
    
    When called with a specific key, returns that entry's full content.
    When called with a keyword, performs FTS5 search and returns matching entries.
    When called with no keyword and no key (list mode), returns keys + title previews
    (first ## heading) instead of full content — use this to discover entries before fetching details.
    """
    project_arg = args.get("project", None)
    keyword = args.get("keyword", "")
    key = args.get("key", None)
    limit = args.get("limit", 20)
    sort_by = args.get("sort_by", "updated_at")  # "updated_at" or "key"
    
    # Resolve project ID: explicit value → scoped; missing → cross-project search
    if project_arg and project_arg != "default":
        resolved_project_name = project_arg
        cross_project = False
    elif project_arg is None or project_arg == "":
        cross_project = True
    else:
        resolved_project_name = None
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
        
        # Resolve project name to ID (only when scoped)
        resolved_project_id = None
        if not cross_project:
            if resolved_project_name:
                cursor.execute("SELECT id FROM projects WHERE name = ?", (resolved_project_name,))
                row = cursor.fetchone()
                if not row:
                    conn.close()
                    return _tool_response(request_id, f"Project '{resolved_project_name}' not found.")
                resolved_project_id = row[0]
            else:
                resolved_project_id = _detect_project_id()
        
        results = []
        
        if keyword:
            # Full-text search — scoped to project or global
            # Wrap in double quotes so FTS5 treats special chars (like -) as literals
            # e.g., "mcp-server" won't be parsed as "mcp -server" (exclude server)
            quoted_keyword = f'"{keyword}"'
            if cross_project:
                cursor.execute(
                    f"""SELECT p.name as project, c.key, c.content, c.updated_at 
                        FROM context c 
                        JOIN context_fts f ON c.id = f.rowid
                        JOIN projects p ON c.project_id = p.id
                        WHERE context_fts MATCH ?
                        ORDER BY c.{sort_by} DESC
                        LIMIT ?""",
                    (quoted_keyword, limit)
                )
            else:
                cursor.execute(
                    f"""SELECT c.key, c.content, c.updated_at, p.name as project
                        FROM context c 
                        JOIN context_fts f ON c.id = f.rowid
                        JOIN projects p ON c.project_id = p.id
                        WHERE context_fts MATCH ? AND c.project_id = ?
                        ORDER BY c.{sort_by} DESC
                        LIMIT ?""",
                    (quoted_keyword, resolved_project_id, limit)
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
            context_id, is_alias = _resolve_key(conn, key, resolved_project_id)
            if context_id:
                cursor.execute(
                    "SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id WHERE c.id = ?",
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
            # Returns keys + extracted titles (first ## heading) instead of full content
            # so the caller can decide which entries to fetch in detail
            sort_clause = f"ORDER BY c.{sort_by} DESC" if sort_by == "key" else "ORDER BY c.updated_at DESC"
            if cross_project:
                cursor.execute(
                    f"SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id {sort_clause} LIMIT ?",
                    (limit,)
                )
            else:
                cursor.execute(
                    f"SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id WHERE c.project_id = ? {sort_clause} LIMIT ?",
                    (resolved_project_id, limit)
                )
            rows = cursor.fetchall()
            for row in rows:
                # Extract first ## heading as a title preview
                content = row[2] or ""
                match = re.search(r'^##\s+(.+)$', content, re.MULTILINE)
                title = match.group(1).strip() if match else None
                entry = {"project": row[0], "key": row[1], "updated_at": row[3]}
                if title:
                    entry["title"] = title
                results.append(entry)
        
        conn.close()
        
        if not results:
            if cross_project:
                return _tool_response(request_id, "No context found.")
            return _tool_response(request_id, f"No context found for project '{resolved_project_name}'.")
        
        # Use title (list mode) or content (search/lookup mode) for display
        formatted = "\n\n".join([
            f"Project: {r['project']}\nKey: {r['key']}\nUpdated: {r['updated_at']}\nTitle: {r.get('title', 'N/A')}\nContent: {r.get('content', 'N/A')}" + (f"\nMatched via: {r['matched_via']}" if "matched_via" in r else "")
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

    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project_arg and project_arg != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_arg,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_arg}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_arg
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        if key:
            # Resolve key → canonical id (handles aliases via CASCADE DELETE)
            context_id, is_alias = _resolve_key(conn, key, resolved_project_id)
            if context_id:
                cursor.execute("DELETE FROM context WHERE id = ?", (context_id,))
                label = f"'{key}'" + (" (via alias)" if is_alias else "")
                deleted = cursor.rowcount
            else:
                deleted = 0
                label = f"'{key}'"
        else:
            cursor.execute(
                "DELETE FROM context WHERE project_id = ?",
                (resolved_project_id,)
            )
            deleted = cursor.rowcount
            label = f"all entries"

        conn.commit()
        conn.close()

        return _tool_response(request_id, f"Cleared {deleted} context entry/entries for project '{resolved_project_name}' ({label}).")
        
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
        
        # Query both context and project_changes tables via views, merge and deduplicate
        cursor.execute("""
            SELECT project, MAX(updated_at) as last_updated FROM context_with_project GROUP BY project
            UNION ALL
            SELECT project, MAX(created_at) as last_updated FROM project_changes_with_project GROUP BY project
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
    
    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project and project != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        # Check canonical key exists
        cursor.execute(
            "SELECT id FROM context WHERE key = ? AND project_id = ?",
            (context_key, resolved_project_id)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return _tool_response(request_id, f"Context key '{context_key}' not found for project '{resolved_project_name}'.")

        context_id = row[0]

        # Check alias doesn't already exist
        cursor.execute(
            "SELECT id FROM context_aliases WHERE alias_key = ? AND project_id = ?",
            (alias_name, resolved_project_id)
        )
        if cursor.fetchone():
            conn.close()
            return _tool_response(request_id, f"Alias '{alias_name}' already exists for project '{resolved_project_name}'.")

        # Insert alias
        cursor.execute(
            "INSERT INTO context_aliases (context_id, alias_key, project_id) VALUES (?, ?, ?)",
            (context_id, alias_name, resolved_project_id)
        )
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Added alias '{alias_name}' → '{context_key}' for project '{resolved_project_name}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"add_context_alias failed: {e}")
        return _tool_response(request_id, f"Error adding alias: {str(e)}")

# ─── Project Change Tracking Handlers ──────────────────────────────

async def handle_add_project_change(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Record a new project change entry."""
    project_name = args.get("project", "")
    key = args.get("key", "")
    change_type = args.get("change_type", "other")
    summary = args.get("summary", "")
    
    if not project_name or not key or not change_type or not summary:
        return _tool_response(request_id, "Error: 'project', 'key', 'change_type', and 'summary' are required.")
    
    valid_types = ('bugfix', 'refactor', 'feature', 'milestone', 'config', 'other')
    if change_type not in valid_types:
        return _tool_response(request_id, f"Error: 'change_type' must be one of: {', '.join(valid_types)}")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Resolve project name to ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Project '{project_name}' not found.")
        project_id = row[0]
        
        # Check for duplicate (project, key)
        cursor.execute(
            "SELECT id FROM project_changes WHERE project_id = ? AND key = ?",
            (project_id, key)
        )
        if cursor.fetchone():
            conn.close()
            return _tool_response(request_id, f"Change '{key}' already exists for project '{project_name}'.")
        
        cursor.execute(
            "INSERT INTO project_changes (project_id, key, change_type, summary) VALUES (?, ?, ?, ?)",
            (project_id, key, change_type, summary)
        )
        conn.commit()
        conn.close()
        
        return _tool_response(request_id, f"Added change '{key}' to project '{project_name}' ({change_type}).")
        
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

        # Resolve project name to ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project,))
        proj_row = cursor.fetchone()
        if not proj_row:
            conn.close()
            return _tool_response(request_id, f"Project '{project}' not found.")
        project_id = proj_row[0]
        
        # Find the change
        cursor.execute(
            "SELECT id FROM project_changes WHERE project_id = ? AND key = ?",
            (project_id, change_key)
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
    project_name = args.get("project", "")
    change_type = args.get("change_type", None)
    date_from = args.get("date_from", None)
    date_to = args.get("date_to", None)
    
    if not project_name:
        return _tool_response(request_id, "Error: 'project' is required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Resolve project name to ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Project '{project_name}' not found.")
        project_id = row[0]
        
        query = "SELECT pc.key, pc.change_type, pc.summary, pc.created_at, p.name as project FROM project_changes pc JOIN projects p ON pc.project_id = p.id WHERE pc.project_id = ?"
        params = [project_id]
        
        if change_type:
            query += " AND pc.change_type = ?"
            params.append(change_type)
        if date_from:
            query += " AND pc.created_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND pc.created_at <= ?"
            params.append(date_to)
        
        query += " ORDER BY pc.created_at DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return _tool_response(request_id, f"No changes found for project '{project_name}'.")
        
        formatted = "\n".join([
            f"- [{row[1]}] {row[0]} — {row[2]} (created: {row[3]})"
            for row in rows
        ])
        
        return _tool_response(request_id, f"Project changes for '{project_name}' ({len(rows)}):\n{formatted}")
        
    except Exception as e:
        if logger:
            logger.error(f"list_project_changes failed: {e}")
        return _tool_response(request_id, f"Error listing project changes: {str(e)}")


async def handle_get_change_history(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Get full history for one change including timeline steps."""
    change_key = args.get("change_key", "")
    project_name = args.get("project", "")
    
    if not change_key or not project_name:
        return _tool_response(request_id, "Error: 'change_key' and 'project' are required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Resolve project name to ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Project '{project_name}' not found.")
        project_id = row[0]
        
        # Get the change
        cursor.execute(
            "SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at FROM project_changes pc WHERE pc.project_id = ? AND pc.key = ?",
            (project_id, change_key)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Change '{change_key}' not found for project '{project_name}'.")
        
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
    project_name = args.get("project", None)
    change_type = args.get("change_type", None)
    
    if not query_text:
        return _tool_response(request_id, "Error: 'query' is required.")
    
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()
        
        # Resolve project name to ID if provided
        project_id = None
        if project_name:
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            project_id = row[0]
        
        # FTS search on summary — quote to handle special chars like - in project names
        quoted_query = _fts_quote(query_text)
        if project_id and change_type:
            cursor.execute(
                """SELECT p.name as project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN projects p ON pc.project_id = p.id
                   JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                   WHERE project_changes_fts MATCH ? AND pc.project_id = ? AND pc.change_type = ?
                   ORDER BY pcf.rank""",
                (quoted_query, project_id, change_type)
            )
        elif project_id:
            cursor.execute(
                """SELECT p.name as project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN projects p ON pc.project_id = p.id
                   JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                   WHERE project_changes_fts MATCH ? AND pc.project_id = ?
                   ORDER BY pcf.rank""",
                (quoted_query, project_id)
            )
        else:
            cursor.execute(
                """SELECT p.name as project, pc.key, pc.change_type, pc.summary, pc.created_at
                   FROM project_changes pc
                   JOIN projects p ON pc.project_id = p.id
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
    project_name = args.get("project", None)
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

    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project_name and project_name != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_name
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        # Check if issue already exists
        cursor.execute(
            "SELECT id FROM issues WHERE project_id = ? AND key = ?",
            (resolved_project_id, key)
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
                INSERT INTO issues (project_id, key, status, title, description, fixed_in_commit)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (resolved_project_id, key, status, title, description, fixed_in_commit))
            action = "Stored"

        conn.commit()
        conn.close()

        return _tool_response(request_id, f"{action} issue '{key}' for project '{resolved_project_name}' (status: {status}).")

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
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        resolved_project_id = None
        resolved_project_name = None
        if project_arg and project_arg != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_arg,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_arg}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_arg
        elif project_arg is None or project_arg == "":
            pass  # Cross-project query
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        query = """
            SELECT p.name as project, i.key, i.status, i.title, i.description, 
                   i.fixed_in_commit, i.created_at,
                   (SELECT COUNT(*) FROM issue_change_links WHERE issue_id = i.id) as related_changes
            FROM issues i
            JOIN projects p ON i.project_id = p.id
            WHERE 1=1
        """
        params = []

        if resolved_project_id:
            query += " AND i.project_id = ?"
            params.append(resolved_project_id)
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
            if resolved_project_name:
                filters.append(f"project='{resolved_project_name}'")
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
            + (f"\n  Desc: {row[4][:300]}{'...' if len(row[4]) > 300 else ''}" if row[4] else "")
            for row in rows
        ])

        return _tool_response(request_id, f"Issues ({len(rows)}):\n{formatted}")

    except Exception as e:
        if logger:
            logger.error(f"query_issues failed: {e}")
        return _tool_response(request_id, f"Error querying issues: {str(e)}")

async def handle_get_issue_details(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Get full details for a single issue by key and project."""
    project_name = args.get("project", None)
    key = args.get("key", "")

    if not key or not project_name:
        return _tool_response(request_id, "Error: 'project' and 'key' are required.")

    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        # Resolve project ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Project '{project_name}' not found.")
        resolved_project_id = row[0]

        # Fetch full issue details
        cursor.execute(
            """SELECT i.key, i.status, i.title, i.description, i.fixed_in_commit, 
                      i.created_at, i.updated_at, p.name as project,
                      (SELECT COUNT(*) FROM issue_change_links WHERE issue_id = i.id) as related_changes
               FROM issues i JOIN projects p ON i.project_id = p.id
               WHERE i.project_id = ? AND i.key = ?""",
            (resolved_project_id, key)
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return _tool_response(request_id, f"Issue '{key}' not found in project '{project_name}'.")

        result = (
            f"=== Issue: {row[0]} ===\n"
            f"Project: {row[7]}\n"
            f"Status: {row[1]}\n"
            f"Title: {row[2]}\n"
            f"Created: {row[5]}\n"
            f"Updated: {row[6]}\n"
            + (f"Fixed in commit: {row[4]}\n" if row[4] else "")
            + f"Related changes: {row[8]}\n\n"
            + (f"Description:\n{row[3]}" if row[3] else "No description.")
        )

        return _tool_response(request_id, result)

    except Exception as e:
        if logger:
            logger.error(f"get_issue_details failed: {e}")
        return _tool_response(request_id, f"Error fetching issue details: {str(e)}")

async def handle_update_issue_status(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Update the status of an existing issue."""
    project_name = args.get("project", None)
    key = args.get("key", "")
    new_status = args.get("status", None)

    if not key or not new_status:
        return _tool_response(request_id, "Error: 'key' and 'status' are required.")

    valid_statuses = ("open", "closed", "not-relevant")
    if new_status not in valid_statuses:
        return _tool_response(request_id, f"Error: 'status' must be one of: {', '.join(valid_statuses)}")

    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project_name and project_name != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_name
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        # Find the issue
        cursor.execute(
            "SELECT id, status FROM issues WHERE project_id = ? AND key = ?",
            (resolved_project_id, key)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return _tool_response(request_id, f"Issue '{key}' not found for project '{resolved_project_name}'.")

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

    # Resolve project name to ID
    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        if project_arg and project_arg != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_arg,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{project_arg}' not found.")
            resolved_project_id = row[0]
            resolved_project_name = project_arg
        elif project_arg is None or project_arg == "":
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]
        else:
            resolved_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_project_id,))
            resolved_project_name = cursor.fetchone()[0]

        query = "SELECT p.name as project, i.key, i.status, i.title, i.created_at FROM issues i JOIN projects p ON i.project_id = p.id WHERE i.project_id = ?"
        params = [resolved_project_id]

        if status_filter:
            query += " AND i.status = ?"
            params.append(status_filter)

        query += " ORDER BY i.created_at DESC"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return _tool_response(request_id, f"No issues found for project '{resolved_project_name}'.")

        formatted = "\n".join([
            f"- [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows
        ])

        return _tool_response(request_id, f"Issues for '{resolved_project_name}' ({len(rows)}):\n{formatted}")

    except Exception as e:
        if logger:
            logger.error(f"list_issues failed: {e}")
        return _tool_response(request_id, f"Error listing issues: {str(e)}")

async def handle_update_issue_project(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Change the project ownership of an existing issue."""
    old_project_name = args.get("project", None)
    new_project_name = args.get("new_project", "")
    key = args.get("key", "")

    if not key or not new_project_name:
        return _tool_response(request_id, "Error: 'key' and 'new_project' are required.")

    try:
        db_path = _get_db_path()
        conn = sqlite3.connect(db_path)
        _init_db(conn)
        cursor = conn.cursor()

        # Resolve old project ID
        if old_project_name and old_project_name != "default":
            cursor.execute("SELECT id FROM projects WHERE name = ?", (old_project_name,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                return _tool_response(request_id, f"Project '{old_project_name}' not found.")
            resolved_old_project_id = row[0]
        else:
            resolved_old_project_id = _detect_project_id()
            cursor.execute("SELECT name FROM projects WHERE id = ?", (resolved_old_project_id,))
            old_project_name = cursor.fetchone()[0]

        # Resolve new project ID
        cursor.execute("SELECT id FROM projects WHERE name = ?", (new_project_name,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return _tool_response(request_id, f"Project '{new_project_name}' not found.")
        new_project_id = row[0]

        # Find the issue
        cursor.execute(
            "SELECT id FROM issues WHERE project_id = ? AND key = ?",
            (resolved_old_project_id, key)
        )
        row = cursor.fetchone()

        if not row:
            conn.close()
            return _tool_response(request_id, f"Issue '{key}' not found in project '{old_project_name}'.")

        issue_id = row[0]

        # Update the project
        cursor.execute(
            "UPDATE issues SET project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_project_id, issue_id)
        )
        conn.commit()
        conn.close()

        return _tool_response(request_id, f"Issue '{key}' moved from '{old_project_name}' → '{new_project_name}'.")

    except Exception as e:
        if logger:
            logger.error(f"update_issue_project failed: {e}")
        return _tool_response(request_id, f"Error updating issue project: {str(e)}")
