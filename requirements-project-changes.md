# Context Store — Audit & Requirements

## Audit Date: May 5, 2026

---

## Current State

### Architecture
- **Database:** SQLite at `.mcp_cache/context.db`
- **Schema:** Two independent systems sharing one DB
  - `context` table — flat key-value pairs (static reference info)
  - `project_changes` + `project_change_details` tables — timeline-based change tracking
- **Search:** FTS5 virtual tables synced via triggers (ai/ad/au)
- **Project ID resolution:** pyproject.toml → git remote origin URL → directory name

### Tool Inventory (11 handlers in sqlite_store.py)

| Handler | Purpose | Parameters |
|---------|---------|------------|
| `handle_store_context` | Upsert context entry | key, content, project |
| `handle_query_context` | Keyword search, key lookup, or list-all | project, keyword, key |
| `handle_clear_context` | Delete single entry or all for a project | key, project |
| `handle_list_projects` | List projects with last update timestamps | — |
| `handle_add_context_alias` | Register alternate name for existing entry | context_key, alias_name, project |
| `handle_add_project_change` | Record new change entry | project, key, change_type, summary |
| `handle_add_change_step` | Add timeline step to a change | change_key, project, step, date, details?, files_changed? |
| `handle_list_project_changes` | Filterable listing of changes | project, change_type?, date_from?, date_to? |
| `handle_get_change_history` | Full history with timeline steps | change_key, project |
| `handle_search_project_changes` | FTS search across summaries | query, project?, change_type? |

### Known Projects (as of audit)
- **mcp-server** — Architecture docs, module breakdowns, known issues
- **job-app** — Job applications, resume file paths, tech match notes
- **todo** — Task tracking

---

## Defects (Fixed ✅)

### D1: `list_projects()` only queries context table — FIXED
**Fix applied:** UNION with `project_changes` table, deduplicate by project keeping latest timestamp.  
**Result:** Projects tracked only via changes now appear in the list.

### D2: No cross-project search in `query_context` — FIXED
**Fix applied:** When project is None or empty, searches all projects. Results include project field.  
**Result:** Global keyword search across all stored context now works.

### D3: Inconsistent API between query tools — FIXED
**Fix applied:** Added `limit` and `sort_by` params to `query_context`. Defaults: limit=20, sort_by="updated_at". Also supports sort_by="key".  
**Result:** Both query tools now have consistent pagination and sorting capabilities.

---

## Improvements (Should Fix)

### I1: Unstructured content — no metadata fields
**Problem:** Content is plain text. Filtering by status/type requires parsing.  
**Example:** Todo entry has "Status: done" embedded in markdown text.  
**Proposed schema addition:**
```sql
ALTER TABLE context ADD COLUMN status TEXT DEFAULT 'active';
-- Values: active, pending, completed, archived, deprecated
```
**Benefit:** Instant filtering without content parsing.

### I2: DB_PATH singleton re-initializes every connection
**Location:** `_get_db_path()` function  
**Problem:** Module-level `DB_PATH = None` cached, but each handler opens a new `sqlite3.connect()`.  
**Impact:** Minor overhead under concurrent requests. SQLite handles it gracefully but unnecessarily.  
**Fix:** Cache connection object or use connection pooling.

### I3: `_detect_project_id()` re-imports BASE_DIR per call
**Location:** Lines 59, 70 inside fallback chain  
**Impact:** Minor inefficiency — repeated import on each project detection call.  
**Fix:** Import at module level.

### I4: Tool addition requires updating 4 places
**Friction points:**
1. Handler implementation file (e.g., `files.py`)
2. `tools/__init__.py` re-export
3. `main.py` imports + `TOOL_HANDLERS` dict
4. `tools.json` schema definition (300 lines)
**Fix:** Consider auto-discovery or generator script for tools.json.

---

## What's Working Well

- ✅ Schema design is solid — context + aliases + FTS5 triggers
- ✅ Project change tracker with timeline steps well-designed
- ✅ Alias resolution system works correctly (canonical → alias lookup)
- ✅ FTS5 hyphen bug already fixed (`_fts_quote()` helper)
- ✅ Change tracker filtering (change_type, date range) properly implemented
- ✅ Cascade delete on project_changes removes all related steps
- ✅ UNIQUE constraints prevent duplicates (key+project, project+key)

---

## Implementation Priority

| # | Item | Type | Status | Effort |
|---|------|------|--------|--------|
| 1 | Fix D1: list_projects() | Defect | ✅ Done | Low |
| 2 | Fix D2: cross-project search | Defect | ✅ Done | Low |
| 3 | Add status column to context | Improvement | Pending | Medium |
| 4 | Align query_context params | Improvement | ✅ Done (merged with D2) | Low |
| 5 | Cache DB connection | Improvement | Pending | Medium |
| 6 | Auto-generate tools.json | Improvement | Pending | Medium |
| 7 | Module-level BASE_DIR import | Improvement | Pending | Trivial |
