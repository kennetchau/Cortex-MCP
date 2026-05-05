# Project Changes Tracking System — Requirements

## 1. Problem Statement

The current context store uses a flat key-value model (`context` table with `key`, `content`, `project`). This works well for static documentation but doesn't support tracking **time-based events** across projects.

Kenneth needs to:
- Record significant changes made to each project (bug fixes, refactors, features, milestones)
- Track the timeline/history of each change (steps taken, files affected, dates)
- Query changes by project, type, date range
- Maintain a personal changelog without manual note-taking

Currently, all project knowledge is stored as text blobs. There's no structured way to answer questions like:
- "What bugfixes did I ship last week?"
- "How did we fix the Flexmonster rendering issue in Bob?"
- "What changed in the MCP server between sessions?"

---

## 2. Goals

| Goal | Priority |
|------|----------|
| Track what changed in each project over time | Must Have |
| Store detailed step-by-step history per change | Must Have |
| Query changes by project, type, or date range | Must Have |
| Non-destructive — existing `context` table unchanged | Must Have |
| Simple schema migration from existing DB | Should Have |
| Searchable via FTS for natural language queries | Nice to Have |

---

## 3. Functional Requirements

### FR-1: Record a New Change
- Create a new change entry with: project name, key identifier, change type, summary
- Change types: `bugfix`, `refactor`, `feature`, `milestone`, `config`, `other`
- Key must be unique within a project (e.g., `mcp-fix-context-list`, `bob-config-refactor`)

### FR-2: Log Timeline Steps
- Each change can have multiple timeline steps
- Each step records: stage description, date, details, files affected
- Steps are ordered chronologically
- Cascade delete: removing a change removes all its steps

### FR-3: Query Changes
- List all changes for a specific project, ordered by date
- Filter by change type (`bugfix`, `refactor`, etc.)
- Filter by date range
- Full-text search across summaries (scoped to project if specified)

### FR-4: View Full History
- Join main change table with timeline steps
- Display complete audit trail for any single change
- Show company/role-like metadata if applicable

### FR-5: Update Existing Changes
- Add new timeline steps to existing changes
- Update summary or metadata without breaking history

---

## 4. Schema Design

### Table: `project_changes`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Internal ID |
| `project` | TEXT | NOT NULL | Project identifier (e.g., "mcp-server", "bob") |
| `key` | TEXT | NOT NULL | Human-readable sub-key within project (e.g., "fix-context-list") |
| `change_type` | TEXT | NOT NULL | Category: bugfix/refactor/feature/milestone/config/other |
| `summary` | TEXT | NOT NULL | One-line description of what changed |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When the change was first recorded |
| `updated_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last modification time |

**Unique Constraint:**
- UNIQUE `(project, key)` — prevents duplicate changes per project

**Indexes:**
- `idx_project_changes_project` on `(project)`
- `idx_project_changes_type` on `(change_type)`
- `idx_project_changes_created` on `(created_at)`

### Table: `project_change_details`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Internal ID |
| `change_id` | INTEGER | NOT NULL → REFERENCES `project_changes(id)` ON DELETE CASCADE | Parent change |
| `step` | TEXT | NOT NULL | Stage name (e.g., "identified root cause", "implemented fix", "tested") |
| `date` | DATE | NOT NULL | Date this step occurred |
| `details` | TEXT | | Detailed description of what happened |
| `files_changed` | TEXT | | Comma-separated file paths or JSON array |
| `created_at` | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | When this step was logged |

**Indexes:**
- `idx_change_details_change_id` on `(change_id)`
- `idx_change_details_date` on `(date)`

---

## 5. Tool Surface (New MCP Tools)

| Tool Name | Purpose | Key Arguments |
|-----------|---------|---------------|
| `add_project_change` | Record a new change entry | `project`, `key`, `change_type`, `summary` |
| `add_change_step` | Add a timeline step to a change | `change_key`, `project`, `step`, `date`, `details`, `files_changed` |
| `list_project_changes` | List changes for a project | `project`, optional `change_type`, optional `date_from`, `date_to` |
| `get_change_history` | Get full history for one change | `change_key`, `project` |
| `search_project_changes` | FTS search across all changes | `query`, optional `project`, optional `change_type` |

---

## 6. Migration Plan

1. **Check if tables exist** — `_init_db()` runs `SELECT name FROM sqlite_master WHERE type='table' AND name IN ('project_changes', 'project_change_details')`
2. **Create tables** if they don't exist
3. **No data migration needed** — existing `context` table stays untouched
4. **Backwards compatible** — all existing tools continue to work unchanged

---

## 7. Non-Functional Requirements

| Requirement | Detail |
|-------------|--------|
| **Performance** | Queries should return in <100ms for typical datasets (<1000 changes) |
| **Data Integrity** | Foreign keys enforced, cascade deletes on change removal |
| **Extensibility** | Schema allows adding columns later (e.g., PR URL, reviewer, commit hash) |
| **Human Readable** | Keys and summaries should be clear without context |
| **Audit Trail** | Every step is timestamped; no overwrites of historical data |

---

## 8. Example Usage

### Record a change:
```python
add_project_change(
    project="mcp-server",
    key="fix-context-list",
    change_type="bugfix",
    summary="Fixed query_context returning N/A for list-all queries"
)
```

### Add timeline steps:
```python
add_change_step(
    change_key="fix-context-list",
    project="mcp-server",
    step="identified root cause",
    date="2026-05-05",
    details="SELECT missing content column in list-all branch",
    files_changed="tools/sqlite_store.py"
)

add_change_step(
    change_key="fix-context-list",
    project="mcp-server",
    step="tested & deployed",
    date="2026-05-05",
    details="curl test passed, pushed to origin/llm_copy",
    files_changed="tools/sqlite_store.py"
)
```

### Query results:
```sql
-- All bugfixes across all projects this week
SELECT project, key, summary, created_at 
FROM project_changes 
WHERE change_type = 'bugfix' AND created_at >= DATE('now', '-7 days')
ORDER BY created_at DESC;
```

---

## 9. Decisions Made

1. **Key uniqueness** — Composite UNIQUE `(project, key)`. Different projects can share the same sub-key (e.g., both "mcp-server" and "bob" can have "fix-timing").
2. **Reverts / changes** — New entry, never delete. Preserve full history so everything is traceable. If something was reverted, that's a new change entry documenting the revert.
3. **FTS scope** — Search only `summary` by default. Can optionally scope to a specific project in queries.
4. **Git integration** — Nice to have but not required. Manual entry is the primary workflow. Git data can be added later as an enhancement.
