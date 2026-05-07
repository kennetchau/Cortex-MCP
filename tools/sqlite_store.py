"""
SQLite knowledge store for persistent LLM context.

Provides tools for storing and retrieving project-specific information
across sessions. Uses SQLite with FTS5 for full-text search.

Architecture:
  - Connection pooling via _db() context manager
  - Schema initialized once per session (not per query)
  - Single _resolve_project_id() helper eliminates duplication
  - All triggers use explicit DELETE+INSERT (avoids FTS5 'delete' syntax bug)
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Tuple


# ─── Database Connection Management ──────────────────────────────────────────

def _get_db_path(base_dir: Path) -> Path:
    """Get the database path, creating parent directory if needed."""
    cache_dir = base_dir / ".mcp_cache"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / "context.db"


@contextmanager
def _db(db_path: Path, timeout: float = 10.0):
    """Context manager for database connections with WAL mode enabled."""
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Schema Definitions ──────────────────────────────────────────────────────

LEGACY_SCHEMA = {
    "context": """
        CREATE TABLE IF NOT EXISTS context (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            content TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            project TEXT,
            UNIQUE(key, project)
        )
    """,
    "context_aliases": """
        CREATE TABLE IF NOT EXISTS context_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id INTEGER NOT NULL REFERENCES context(id) ON DELETE CASCADE,
            alias_key TEXT NOT NULL,
            project TEXT NOT NULL
        )
    """,
    "context_fts": """
        CREATE VIRTUAL TABLE IF NOT EXISTS context_fts 
        USING fts5(key, content, project, content='context', content_rowid='id')
    """,
    "project_changes": """
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
    """,
    "project_change_details": """
        CREATE TABLE IF NOT EXISTS project_change_details (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
            step TEXT NOT NULL,
            date DATE NOT NULL,
            details TEXT,
            files_changed TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "project_changes_fts": """
        CREATE VIRTUAL TABLE IF NOT EXISTS project_changes_fts 
        USING fts5(summary, project, content='project_changes', content_rowid='id')
    """,
}

LEGACY_TRIGGERS = {
    "context_ai": """
        CREATE TRIGGER IF NOT EXISTS context_ai AFTER INSERT ON context
        BEGIN
            INSERT INTO context_fts(rowid, key, content, project)
            VALUES (new.id, new.key, new.content, new.project);
        END
    """,
    "context_ad": """
        CREATE TRIGGER IF NOT EXISTS context_ad AFTER DELETE ON context
        BEGIN
            DELETE FROM context_fts WHERE rowid = old.id;
        END
    """,
    "context_au": """
        CREATE TRIGGER IF NOT EXISTS context_au AFTER UPDATE ON context
        BEGIN
            DELETE FROM context_fts WHERE rowid = old.id;
            INSERT INTO context_fts(rowid, key, content, project)
            VALUES (new.id, new.key, new.content, new.project);
        END
    """,
    "pc_ai": """
        CREATE TRIGGER IF NOT EXISTS pc_ai AFTER INSERT ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(rowid, summary, project)
            VALUES (new.id, new.summary, new.project);
        END
    """,
    "pc_ad": """
        CREATE TRIGGER IF NOT EXISTS pc_ad AFTER DELETE ON project_changes
        BEGIN
            DELETE FROM project_changes_fts WHERE rowid = old.id;
        END
    """,
    "pc_au": """
        CREATE TRIGGER IF NOT EXISTS pc_au AFTER UPDATE ON project_changes
        BEGIN
            DELETE FROM project_changes_fts WHERE rowid = old.id;
            INSERT INTO project_changes_fts(rowid, summary, project)
            VALUES (new.id, new.summary, new.project);
        END
    """,
}

MIGRATED_SCHEMA = {
    "projects": """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "issues": """
        CREATE TABLE IF NOT EXISTS issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL REFERENCES projects(id),
            key TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('open', 'closed', 'not-relevant')),
            title TEXT NOT NULL,
            description TEXT,
            fixed_in_commit TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "issue_change_links": """
        CREATE TABLE IF NOT EXISTS issue_change_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
            change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
            linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(issue_id, change_id)
        )
    """,
}

# Migrated triggers use explicit DELETE+INSERT to avoid FTS5 'delete' syntax bug
MIGRATED_TRIGGERS = {
    "context_ai": """
        CREATE TRIGGER context_ai AFTER INSERT ON context
        BEGIN
            INSERT INTO context_fts(rowid, key, content, project_name)
            VALUES (new.id, new.key, new.content, 
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """,
    "context_ad": """
        CREATE TRIGGER context_ad AFTER DELETE ON context
        BEGIN
            DELETE FROM context_fts WHERE rowid = old.id;
        END
    """,
    "context_au": """
        CREATE TRIGGER context_au AFTER UPDATE ON context
        BEGIN
            DELETE FROM context_fts WHERE rowid = old.id;
            INSERT INTO context_fts(rowid, key, content, project_name)
            VALUES (new.id, new.key, new.content,
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """,
    "pc_ai": """
        CREATE TRIGGER pc_ai AFTER INSERT ON project_changes
        BEGIN
            INSERT INTO project_changes_fts(rowid, summary, project_name)
            VALUES (new.id, new.summary, 
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """,
    "pc_ad": """
        CREATE TRIGGER pc_ad AFTER DELETE ON project_changes
        BEGIN
            DELETE FROM project_changes_fts WHERE rowid = old.id;
        END
    """,
    "pc_au": """
        CREATE TRIGGER pc_au AFTER UPDATE ON project_changes
        BEGIN
            DELETE FROM project_changes_fts WHERE rowid = old.id;
            INSERT INTO project_changes_fts(rowid, summary, project_name)
            VALUES (new.id, new.summary,
                    (SELECT name FROM projects WHERE id = new.project_id));
        END
    """,
}

# Views for backward compatibility
VIEWS = {
    "context_with_project": """
        CREATE VIEW IF NOT EXISTS context_with_project AS
        SELECT c.id, c.key, c.content, c.updated_at, p.name as project
        FROM context c
        JOIN projects p ON c.project_id = p.id
    """,
    "project_changes_with_project": """
        CREATE VIEW IF NOT EXISTS project_changes_with_project AS
        SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at, pc.updated_at, p.name as project
        FROM project_changes pc
        JOIN projects p ON pc.project_id = p.id
    """,
    "issues_with_project": """
        CREATE VIEW IF NOT EXISTS issues_with_project AS
        SELECT i.id, i.key, i.status, i.title, i.description, i.fixed_in_commit, i.created_at, i.updated_at, p.name as project
        FROM issues i
        JOIN projects p ON i.project_id = p.id
    """,
}


# ─── Schema Initialization ───────────────────────────────────────────────────

def _init_db(conn: sqlite3.Connection, base_dir: Path):
    """Initialize database schema. Checks if migration has occurred."""
    cursor = conn.cursor()
    
    # Check if we've already migrated (projects table exists with data)
    try:
        cursor.execute("SELECT COUNT(*) FROM projects")
        has_projects_table = cursor.fetchone()[0] > 0
    except Exception:
        has_projects_table = False
    
    if has_projects_table:
        _ensure_migrated_schema(cursor, conn)
    else:
        _ensure_legacy_schema(cursor)
    
    conn.commit()


def _ensure_legacy_schema(cursor):
    """Ensure legacy schema tables and triggers exist."""
    for sql in LEGACY_SCHEMA.values():
        cursor.execute(sql)
    for sql in LEGACY_TRIGGERS.values():
        cursor.execute(sql)
    
    # Issues tables (legacy uses TEXT project column)
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


def _ensure_migrated_schema(cursor, conn):
    """Ensure migrated schema exists. Drops and rebuilds FTS tables on startup."""
    for sql in MIGRATED_SCHEMA.values():
        cursor.execute(sql)
    
    # Recreate context_fts (drops existing to ensure clean state)
    cursor.execute("DROP TABLE IF EXISTS context_fts")
    for t in ["context_fts_config", "context_fts_data", "context_fts_docsize", "context_fts_idx"]:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception:
            pass
    cursor.execute("CREATE VIRTUAL TABLE context_fts USING fts5(key, content, project_name)")
    
    # Repopulate FTS from existing data
    cursor.execute("""
        INSERT OR IGNORE INTO context_fts(rowid, key, content, project_name)
        SELECT c.id, c.key, c.content, p.name
        FROM context c
        JOIN projects p ON c.project_id = p.id
    """)
    
    # Recreate triggers (always recreate to pick up any fixes)
    for name, sql in MIGRATED_TRIGGERS.items():
        cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
        cursor.execute(sql)
    
    # Recreate project_changes_fts
    cursor.execute("DROP TABLE IF EXISTS project_changes_fts")
    for t in ["project_changes_fts_config", "project_changes_fts_data", 
              "project_changes_fts_docsize", "project_changes_fts_idx"]:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {t}")
        except Exception:
            pass
    cursor.execute("CREATE VIRTUAL TABLE project_changes_fts USING fts5(summary, project_name)")
    
    for name, sql in MIGRATED_TRIGGERS.items():
        if name.startswith("pc_"):
            cursor.execute(f"DROP TRIGGER IF EXISTS {name}")
            cursor.execute(sql)
    
    # Create backward-compat views
    for sql in VIEWS.values():
        cursor.execute(sql)


# ─── Project Resolution ──────────────────────────────────────────────────────

def _detect_project_name(base_dir: Path) -> str:
    """Detect project name using priority chain."""
    try:
        pyproject = base_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib
                with open(pyproject, "rb") as f:
                    config = tomllib.load(f)
                detected = config.get("project", {}).get("name")
                if detected:
                    return detected
            except Exception:
                pass
        
        from config import BASE_DIR
        result = run_command(["git", "remote", "get-url", "origin"], cwd=BASE_DIR, timeout=3)
        if result.strip():
            url = result.strip()
            return url.rstrip("/").split("/")[-1].replace(".git", "")
        
        return str(base_dir.name)
    except Exception:
        return "unknown-project"


def _resolve_project_id(conn: sqlite3.Connection, project_name: Optional[str], 
                        detect_default: bool = True) -> Tuple[Optional[int], Optional[str]]:
    """Resolve project name to (id, name). Returns (None, None) for cross-project queries.
    
    Args:
        conn: Database connection
        project_name: Explicit project name, or None for auto-detection
        detect_default: If True and no name given, auto-detect from pyproject.toml
    
    Returns:
        (project_id, project_name) tuple, or (None, None) for cross-project
    """
    cursor = conn.cursor()
    
    # Cross-project query
    if project_name is None or project_name == "" or project_name == "default":
        if detect_default and project_name is not None:
            pass  # Fall through to detection below
        else:
            return None, None
    
    # Explicit project name provided
    if project_name and project_name != "default":
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        row = cursor.fetchone()
        if not row:
            return None, project_name  # Project doesn't exist yet
        return row[0], project_name
    
    # Auto-detect
    detected = _detect_project_name(conn.execute("PRAGMA database_list").fetchone()[2] if False else Path("."))
    cursor.execute("SELECT id FROM projects WHERE name = ?", (detected,))
    row = cursor.fetchone()
    if row:
        return row[0], detected
    
    # Create if doesn't exist
    try:
        cursor.execute("INSERT INTO projects (name) VALUES (?)", (detected,))
        return cursor.lastrowid, detected
    except sqlite3.IntegrityError:
        cursor.execute("SELECT id FROM projects WHERE name = ?", (detected,))
        return cursor.fetchone()[0], detected


# ─── Key Resolution ──────────────────────────────────────────────────────────

def _resolve_key(conn: sqlite3.Connection, key: str, project_id: int) -> Tuple[Optional[int], bool]:
    """Resolve a key to its canonical context entry.
    
    Returns (context_id, is_alias) or (None, False) if not found.
    Lookup order: canonical key first → aliases second.
    """
    cursor = conn.cursor()
    
    # Check canonical key
    cursor.execute("SELECT id FROM context WHERE key = ? AND project_id = ?", (key, project_id))
    row = cursor.fetchone()
    if row:
        return row[0], False
    
    # Check aliases
    cursor.execute(
        "SELECT context_id FROM context_aliases WHERE alias_key = ? AND project_id = ?",
        (key, project_id)
    )
    row = cursor.fetchone()
    if row:
        return row[0], True
    
    return None, False


# ─── FTS Utilities ───────────────────────────────────────────────────────────

def _fts_quote(term: str) -> str:
    """Quote a term for FTS5 MATCH so special chars (like -) are treated as literals."""
    return f'"{term}"'


# ─── Tool Handlers ───────────────────────────────────────────────────────────

async def handle_store_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Store or update project context in SQLite knowledge base."""
    key = args.get("key", "")
    content = args.get("content", "")
    project_name = args.get("project", None)

    if not key or not content:
        return _tool_response(request_id, "Error: 'key' and 'content' are required.")

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_name)
            if project_id is None:
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            
            context_id, is_alias = _resolve_key(conn, key, project_id)
            
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
                    (key, content, project_id)
                )
                action = "Stored"
                target_label = f"'{key}'"
            
            conn.commit()
        
        return _tool_response(request_id, f"{action} context {target_label} for project '{resolved_name}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"store_context failed: {e}")
        return _tool_response(request_id, f"Error storing context: {str(e)}")


async def handle_query_context(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Query stored context using keyword search or direct key lookup.
    
    When called with a specific key, returns that entry's full content.
    When called with a keyword, performs FTS5 search and returns matching entries.
    When called with no keyword and no key (list mode), returns keys + title previews.
    """
    project_arg = args.get("project", None)
    keyword = args.get("keyword", "")
    key = args.get("key", None)
    limit = args.get("limit", 20)
    sort_by = args.get("sort_by", "updated_at")
    
    valid_sorts = ("updated_at", "key")
    if sort_by not in valid_sorts:
        return _tool_response(request_id, f"Error: 'sort_by' must be one of: {', '.join(valid_sorts)}")
    
    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path, timeout=10) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_arg, detect_default=False)
            cross_project = project_id is None
            
            results = []
            
            if keyword:
                quoted_keyword = _fts_quote(keyword)
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
                        (quoted_keyword, project_id, limit)
                    )
                
                for row in cursor.fetchall():
                    results.append({
                        "project": row[0] if cross_project else row[3],
                        "key": row[1] if cross_project else row[0],
                        "content": row[2] if cross_project else row[1],
                        "updated_at": row[3] if cross_project else row[2],
                    })
                    
            elif key:
                context_id, is_alias = _resolve_key(conn, key, project_id)
                if context_id:
                    cursor.execute(
                        "SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id WHERE c.id = ?",
                        (context_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        result = {
                            "project": row[0], "key": row[1],
                            "content": row[2], "updated_at": row[3]
                        }
                        if is_alias:
                            result["matched_via"] = f"alias '{key}'"
                        results.append(result)
                        
            else:
                sort_clause = f"ORDER BY c.{sort_by} DESC" if sort_by == "key" else "ORDER BY c.updated_at DESC"
                if cross_project:
                    cursor.execute(
                        f"SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id {sort_clause} LIMIT ?",
                        (limit,)
                    )
                else:
                    cursor.execute(
                        f"SELECT p.name as project, c.key, c.content, c.updated_at FROM context c JOIN projects p ON c.project_id = p.id WHERE c.project_id = ? {sort_clause} LIMIT ?",
                        (project_id, limit)
                    )
                
                for row in cursor.fetchall():
                    content = row[2] or ""
                    match = re.search(r'^##\s+(.+)$', content, re.MULTILINE)
                    title = match.group(1).strip() if match else None
                    entry = {"project": row[0], "key": row[1], "updated_at": row[3]}
                    if title:
                        entry["title"] = title
                    results.append(entry)
        
        if not results:
            if cross_project:
                return _tool_response(request_id, "No context found.")
            return _tool_response(request_id, f"No context found for project '{resolved_name}'.")
        
        formatted = "\n\n".join([
            f"Project: {r['project']}\nKey: {r['key']}\nUpdated: {r['updated_at']}\nTitle: {r.get('title', 'N/A')}\nContent: {r.get('content', 'N/A')}" + 
            (f"\nMatched via: {r['matched_via']}" if "matched_via" in r else "")
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

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_arg)
            if project_id is None and project_arg:
                return _tool_response(request_id, f"Project '{project_arg}' not found.")
            
            if key:
                context_id, is_alias = _resolve_key(conn, key, project_id)
                if context_id:
                    cursor.execute("DELETE FROM context WHERE id = ?", (context_id,))
                    label = f"'{key}'" + (" (via alias)" if is_alias else "")
                    deleted = cursor.rowcount
                else:
                    deleted = 0
                    label = f"'{key}'"
            else:
                cursor.execute("DELETE FROM context WHERE project_id = ?", (project_id,))
                deleted = cursor.rowcount
                label = "all entries"
            
            conn.commit()
        
        return _tool_response(request_id, f"Cleared {deleted} context entry/entries for project '{resolved_name}' ({label}).")
        
    except Exception as e:
        if logger:
            logger.error(f"clear_context failed: {e}")
        return _tool_response(request_id, f"Error clearing context: {str(e)}")


async def handle_list_projects(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """List all known projects in the context store."""
    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT project, MAX(updated_at) as last_updated FROM context_with_project GROUP BY project
                UNION ALL
                SELECT project, MAX(created_at) as last_updated FROM project_changes_with_project GROUP BY project
            """)
            all_rows = cursor.fetchall()
            
            project_map = {}
            for project, updated_at in all_rows:
                if project not in project_map or (updated_at and project_map[project] and updated_at > project_map[project]):
                    project_map[project] = updated_at
            
            rows = sorted(project_map.items(), key=lambda x: x[1] or "", reverse=True)
        
        if not rows:
            return _tool_response(request_id, "No context entries found. Use 'store_context' to add entries.")
        
        formatted = "\n".join([f"- {row[0]} (last updated: {row[1]})" for row in rows])
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
    
    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project)
            if project_id is None and project:
                return _tool_response(request_id, f"Project '{project}' not found.")
            
            cursor.execute(
                "SELECT id FROM context WHERE key = ? AND project_id = ?",
                (context_key, project_id)
            )
            if not cursor.fetchone():
                return _tool_response(request_id, f"Context key '{context_key}' not found for project '{resolved_name}'.")
            
            cursor.execute(
                "SELECT id FROM context_aliases WHERE alias_key = ? AND project_id = ?",
                (alias_name, project_id)
            )
            if cursor.fetchone():
                return _tool_response(request_id, f"Alias '{alias_name}' already exists for project '{resolved_name}'.")
            
            # Find the actual context_id
            cursor.execute("SELECT id FROM context WHERE key = ? AND project_id = ?", (context_key, project_id))
            context_id = cursor.fetchone()[0]
            
            cursor.execute(
                "INSERT INTO context_aliases (context_id, alias_key, project_id) VALUES (?, ?, ?)",
                (context_id, alias_name, project_id)
            )
            conn.commit()
        
        return _tool_response(request_id, f"Added alias '{alias_name}' → '{context_key}' for project '{resolved_name}'.")
        
    except Exception as e:
        if logger:
            logger.error(f"add_context_alias failed: {e}")
        return _tool_response(request_id, f"Error adding alias: {str(e)}")


# ─── Project Change Tracking Handlers ────────────────────────────────────────

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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path, timeout=10) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            project_id = row[0]
            
            cursor.execute(
                "SELECT id FROM project_changes WHERE project_id = ? AND key = ?",
                (project_id, key)
            )
            if cursor.fetchone():
                return _tool_response(request_id, f"Change '{key}' already exists for project '{project_name}'.")
            
            cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM project_changes WHERE project_id = ?", (project_id,))
            new_id = cursor.fetchone()[0]
            
            cursor.execute(
                "INSERT INTO project_changes (id, project_id, key, change_type, summary) VALUES (?, ?, ?, ?, ?)",
                (new_id, project_id, key, change_type, summary)
            )
            conn.commit()
        
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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project,))
            proj_row = cursor.fetchone()
            if not proj_row:
                return _tool_response(request_id, f"Project '{project}' not found.")
            project_id = proj_row[0]
            
            cursor.execute(
                "SELECT id FROM project_changes WHERE project_id = ? AND key = ?",
                (project_id, change_key)
            )
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Change '{change_key}' not found for project '{project}'.")
            
            change_id = row[0]
            if change_id is None:
                cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM project_changes WHERE project_id = ?", (project_id,))
                change_id = cursor.fetchone()[0]
                cursor.execute("UPDATE project_changes SET id = ? WHERE key = ? AND id IS NULL", (change_id, change_key))
                conn.commit()
            
            cursor.execute(
                "INSERT INTO project_change_details (change_id, step, date, details, files_changed) VALUES (?, ?, ?, ?, ?)",
                (change_id, step, date, details, files_changed)
            )
            conn.commit()
        
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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path, timeout=10) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path, timeout=10) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            project_id = row[0]
            
            cursor.execute(
                "SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at FROM project_changes pc WHERE pc.project_id = ? AND pc.key = ?",
                (project_id, change_key)
            )
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Change '{change_key}' not found for project '{project_name}'.")
            
            change_id, key, change_type, summary, created_at = row
            
            cursor.execute(
                "SELECT step, date, details, files_changed FROM project_change_details WHERE change_id = ? ORDER BY date ASC",
                (change_id,)
            )
            steps = cursor.fetchall()
        
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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path, timeout=10) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id = None
            if project_name:
                cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
                row = cursor.fetchone()
                if not row:
                    return _tool_response(request_id, f"Project '{project_name}' not found.")
                project_id = row[0]
            
            quoted_query = _fts_quote(query_text)
            
            base_query = """
                SELECT p.name as project, pc.key, pc.change_type, pc.summary, pc.created_at
                FROM project_changes pc
                JOIN projects p ON pc.project_id = p.id
                JOIN project_changes_fts pcf ON pc.id = pcf.rowid
                WHERE project_changes_fts MATCH ?
            """
            params = [quoted_query]
            
            if project_id and change_type:
                base_query += " AND pc.project_id = ? AND pc.change_type = ?"
                params.extend([project_id, change_type])
            elif project_id:
                base_query += " AND pc.project_id = ?"
                params.append(project_id)
            
            base_query += " ORDER BY pcf.rank"
            
            cursor.execute(base_query, params)
            rows = cursor.fetchall()
        
        if not rows:
            return _tool_response(request_id, f"No changes found matching '{query_text}'.")
        
        formatted = "\n".join([
            f"- [{row[0]}] [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows[:20]
        ])
        
        return _tool_response(request_id, f"Search results for '{query_text}' ({len(rows)} matches):\n{formatted}")
        
    except Exception as e:
        if logger:
            logger.error(f"search_project_changes failed: {e}")
        return _tool_response(request_id, f"Error searching project changes: {str(e)}")


# ─── Issues Handlers ────────────────────────────────────────────────────────

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

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_name)
            if project_id is None and project_name:
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            
            cursor.execute(
                "SELECT id FROM issues WHERE project_id = ? AND key = ?",
                (project_id, key)
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
                """, (project_id, key, status, title, description, fixed_in_commit))
                action = "Stored"

            conn.commit()

        return _tool_response(request_id, f"{action} issue '{key}' for project '{resolved_name}' (status: {status}).")

    except Exception as e:
        if logger:
            logger.error(f"store_issue failed: {e}")
        return _tool_response(request_id, f"Error storing issue: {str(e)}")


async def handle_query_issues(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """Query issues with optional filters by project, status, or key."""
    project_arg = args.get("project", None)
    status_filter = args.get("status", None)
    key_filter = args.get("key", None)

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_arg, detect_default=False)
            
            query = """
                SELECT p.name as project, i.key, i.status, i.title, i.description, 
                       i.fixed_in_commit, i.created_at,
                       (SELECT COUNT(*) FROM issue_change_links WHERE issue_id = i.id) as related_changes
                FROM issues i
                JOIN projects p ON i.project_id = p.id
                WHERE 1=1
            """
            params = []

            if project_id:
                query += " AND i.project_id = ?"
                params.append(project_id)
            if status_filter:
                query += " AND i.status = ?"
                params.append(status_filter)
            if key_filter:
                query += " AND i.key = ?"
                params.append(key_filter)

            query += " ORDER BY i.created_at DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            filters = []
            if resolved_name:
                filters.append(f"project='{resolved_name}'")
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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Project '{project_name}' not found.")
            resolved_project_id = row[0]

            cursor.execute(
                """SELECT i.key, i.status, i.title, i.description, i.fixed_in_commit, 
                          i.created_at, i.updated_at, p.name as project,
                          (SELECT COUNT(*) FROM issue_change_links WHERE issue_id = i.id) as related_changes
                   FROM issues i JOIN projects p ON i.project_id = p.id
                   WHERE i.project_id = ? AND i.key = ?""",
                (resolved_project_id, key)
            )
            row = cursor.fetchone()

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

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_name)
            if project_id is None and project_name:
                return _tool_response(request_id, f"Project '{project_name}' not found.")

            cursor.execute(
                "SELECT id, status FROM issues WHERE project_id = ? AND key = ?",
                (project_id, key)
            )
            row = cursor.fetchone()

            if not row:
                return _tool_response(request_id, f"Issue '{key}' not found for project '{resolved_name}'.")

            old_status = row[1]
            issue_id = row[0]

            cursor.execute(
                "UPDATE issues SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_status, issue_id)
            )
            conn.commit()

        return _tool_response(request_id, f"Issue '{key}' status changed: '{old_status}' → '{new_status}'.")

    except Exception as e:
        if logger:
            logger.error(f"update_issue_status failed: {e}")
        return _tool_response(request_id, f"Error updating issue status: {str(e)}")


async def handle_list_issues(request_id: str, args: dict, _tool_response, logger=None, **kwargs) -> dict:
    """List all issues with optional filters by project and status."""
    project_arg = args.get("project", None)
    status_filter = args.get("status", None)

    try:
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            project_id, resolved_name = _resolve_project_id(conn, project_arg)
            if project_id is None:
                return _tool_response(request_id, f"No issues found for project '{resolved_name}'.")

            query = "SELECT p.name as project, i.key, i.status, i.title, i.created_at FROM issues i JOIN projects p ON i.project_id = p.id WHERE i.project_id = ?"
            params = [project_id]

            if status_filter:
                query += " AND i.status = ?"
                params.append(status_filter)

            query += " ORDER BY i.created_at DESC"

            cursor.execute(query, params)
            rows = cursor.fetchall()

        if not rows:
            return _tool_response(request_id, f"No issues found for project '{resolved_name}'.")

        formatted = "\n".join([
            f"- [{row[2]}] {row[1]} — {row[3]} (created: {row[4]})"
            for row in rows
        ])

        return _tool_response(request_id, f"Issues for '{resolved_name}' ({len(rows)}):\n{formatted}")

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
        from config import BASE_DIR
        db_path = _get_db_path(BASE_DIR)
        
        with _db(db_path) as conn:
            _init_db(conn, BASE_DIR)
            cursor = conn.cursor()
            
            # Resolve old project ID
            if old_project_name and old_project_name != "default":
                cursor.execute("SELECT id FROM projects WHERE name = ?", (old_project_name,))
                row = cursor.fetchone()
                if not row:
                    return _tool_response(request_id, f"Project '{old_project_name}' not found.")
                resolved_old_project_id = row[0]
                old_project_name_display = old_project_name
            else:
                detected = _detect_project_name(Path("."))
                cursor.execute("SELECT id FROM projects WHERE name = ?", (detected,))
                row = cursor.fetchone()
                if row:
                    resolved_old_project_id = row[0]
                else:
                    return _tool_response(request_id, f"Could not detect project.")
                old_project_name_display = detected

            # Resolve new project ID
            cursor.execute("SELECT id FROM projects WHERE name = ?", (new_project_name,))
            row = cursor.fetchone()
            if not row:
                return _tool_response(request_id, f"Project '{new_project_name}' not found.")
            new_project_id = row[0]

            # Find the issue
            cursor.execute(
                "SELECT id FROM issues WHERE project_id = ? AND key = ?",
                (resolved_old_project_id, key)
            )
            row = cursor.fetchone()

            if not row:
                return _tool_response(request_id, f"Issue '{key}' not found in project '{old_project_name_display}'.")

            issue_id = row[0]

            # Update the project
            cursor.execute(
                "UPDATE issues SET project_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_project_id, issue_id)
            )
            conn.commit()

        return _tool_response(request_id, f"Issue '{key}' moved from '{old_project_name_display}' → '{new_project_name}'.")

    except Exception as e:
        if logger:
            logger.error(f"update_issue_project failed: {e}")
        return _tool_response(request_id, f"Error updating issue project: {str(e)}")
