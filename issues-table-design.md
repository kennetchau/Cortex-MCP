# Issues Table Design — MCP Server Context Store

**Date:** 2026-05-06  
**Status:** Planning  
**Author:** Kenneth (via Axiom)

---

## Problem Statement

Known issues currently live as unstructured markdown blobs in the `context` table. This means:

- No way to query by status (open/closed/not-relevant)
- No proper lifecycle tracking for bugs
- No traceability between issues and the changes that fix them
- Manual parsing required to extract issue metadata

## Solution Overview

Add a dedicated `issues` table with structured fields, plus a junction table for many-to-many linking to `project_changes`. This gives us:

- Structured storage with status lifecycle
- Queryable by project, key, and status
- Full traceability: issue → change(s) and change → issue(s)
- Separation of concerns: issues have lifecycle; project_changes are events; context is static facts

---

## Database Schema

### New Tables

#### `issues`

```sql
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
);
```

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Internal identifier |
| `project` | TEXT | NOT NULL | Project identifier (e.g., "mcp-server", "job-app") |
| `key` | TEXT | NOT NULL | Human-readable sub-key (e.g., "db-path-race") |
| `status` | TEXT | NOT NULL, CHECK | Issue state: `open`, `closed`, `not-relevant` |
| `title` | TEXT | NOT NULL | Short one-line summary |
| `description` | TEXT | nullable | Detailed problem statement or notes |
| `fixed_in_commit` | TEXT | nullable | Git commit hash where this was resolved |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the issue was first recorded |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modification time |

#### `issue_change_links` (Junction Table)

```sql
CREATE TABLE IF NOT EXISTS issue_change_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    change_id INTEGER NOT NULL REFERENCES project_changes(id) ON DELETE CASCADE,
    linked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(issue_id, change_id)
);
```

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Internal identifier |
| `issue_id` | INTEGER | FK → issues.id, CASCADE | The issue being linked |
| `change_id` | INTEGER | FK → project_changes.id, CASCADE | The project change related to the issue |
| `linked_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the link was created |
| **UNIQUE(issue_id, change_id)** | — | Ensures no duplicate links | |

### Why a Junction Table?

A direct foreign key on `project_changes` would be one-to-many (one issue per change). A junction table enables:

- **One issue → multiple changes**: Partial fix across commits, regression fixes, reopened issues
- **One change → multiple issues**: A single refactor or deployment that resolves several bugs
- **Audit trail**: `linked_at` timestamp shows when the relationship was documented

---

## New Tool Handlers (4 handlers in sqlite_store.py)

All follow the existing handler pattern: extract args → resolve project → connect DB → `_init_db()` → execute → format response.

### 1. `handle_store_issue`

Store or update an issue record.

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | Yes | Project identifier |
| `key` | string | Yes | Human-readable sub-key |
| `status` | string | Yes | One of: open, closed, not-relevant |
| `title` | string | Yes | Short summary |
| `description` | string | No | Detailed description |
| `fixed_in_commit` | string | No | Git commit hash if already fixed |

**Behavior:**
- If `project + key` exists → UPDATE (set updated_at)
- Otherwise → INSERT
- Returns confirmation with resolved project name

### 2. `handle_query_issues`

Query issues with optional filters.

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | No | Filter by project (auto-detects if omitted) |
| `status` | string | No | Filter by status (open/closed/not-relevant) |
| `key` | string | No | Exact key lookup |

**Behavior:**
- Builds WHERE clause dynamically based on provided params
- Returns formatted list with project, key, status, title
- Includes related change count if links exist

### 3. `handle_update_issue_status`

Update the status of an existing issue.

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | Yes | Project identifier |
| `key` | string | Yes | Human-readable sub-key |
| `status` | string | Yes | New status (open/closed/not-relevant) |

**Behavior:**
- Validates issue exists for given project+key
- Updates status and updated_at timestamp
- Returns old → new status transition (e.g., "open" → "closed")

### 4. `handle_list_issues`

List all issues with optional filters. Similar to `list_project_changes`.

**Parameters:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `project` | string | No | Filter by project |
| `status` | string | No | Filter by status |

**Behavior:**
- Defaults to auto-detect project if not specified
- Orders by created_at DESC
- Returns formatted summary with counts

---

## Wiring Changes Required

### Files to Modify

#### 1. `sqlite_store.py` (~+150 lines)

- Add `issues` and `issue_change_links` table DDL in `_init_db()` after the `project_changes_fts` triggers section (before line 200 `conn.commit()`)
- Append 4 new handler functions after `handle_search_project_changes` (after line 807)

#### 2. `tools/__init__.py` (+4 imports, +4 __all__ entries)

Add to existing sqlite_store import block:
```python
handle_store_issue,
handle_query_issues,
handle_update_issue_status,
handle_list_issues,
```

Add to `__all__` list at end of file.

#### 3. `main.py` (+4 imports, +4 TOOL_HANDLERS entries)

Add to `from tools import (...)` block (after existing sqlite_store handlers).

Add to `TOOL_HANDLERS` dict (after `search_project_changes`).

#### 4. `tools.json` (+ ~60 lines)

Insert 4 new tool definitions before the closing `]` (before line 301). Each follows the same JSON schema pattern as existing tools (e.g., `add_project_change`).

---

## Migration Plan

### Source Data

The `known-issues` context entry (project: mcp-server) contains 10 items:

**Fixed (4):**
1. FTS5 hyphen bug → commit `be01a7f`
2. list_projects() incomplete → commit `78adc13`
3. No cross-project search → commit `78adc13`
4. Inconsistent query API → commit `78adc13`

**Open (6):**
5. DB_PATH singleton race
6. _detect_project_id() re-imports BASE_DIR
7. run_command HOME detection fragility
8. tools.json out of sync risk
9. No MCP stdio transport
10. CORS allow_origins non-compliant

### Migration Steps

1. Query the `context` table for the `known-issues` record in the `mcp-server` project
2. Parse each numbered item from the markdown content
3. Extract structured fields:
   - `key`: derived from item description (e.g., "fts5-hyphen-bug", "db-path-race")
   - `title`: short summary line
   - `description`: full text of the item
   - `status`: "closed" (under Fixed section) or "open" (under Open section)
   - `fixed_in_commit`: extract commit hash if present
4. INSERT into `issues` table
5. For fixed items with known commits, look up corresponding `project_changes` records and create links in `issue_change_links`
6. Update the `known-issues` context entry to note it has been migrated and points to the new system

---

## Future Enhancements (Out of Scope)

- FTS5 search on issue titles/descriptions (add `issues_fts` virtual table + triggers)
- Auto-linking: when a change is added with a `related_issue_key` param, automatically create the junction link
- Issue aging alerts (e.g., open issues older than 30 days)
- Export issues as markdown/JSON report
