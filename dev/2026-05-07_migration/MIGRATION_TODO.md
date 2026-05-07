# Migration Status — projects table refactor

## ✅ COMPLETED
- Database schema migrated: `project TEXT` → `project_id INTEGER REFERENCES projects(id)`
  - Tables updated: context, issues, project_changes, context_aliases, issues_migrate
  - New `projects` table created with 5 entries (BIGS_home_page, mcp-server, bob, todo, job-app)
  - FTS5 rebuilt without `content=` param (manual trigger sync via subqueries)
  - 3 backward-compat views created: context_with_project, project_changes_with_project, issues_with_project
- `_detect_project_id()` updated: now returns int (looks up name in projects table)
- `_resolve_key()` updated: uses `project_id` instead of `project`
- `_init_db()` rewritten: detects migrated vs legacy schema, handles both
- `_resolve_project_id()` helper added with race condition handling
- Migration script saved at `tools/migrate_projects.py`
- Project change + issue tracked: `projects-table-migration`

## ✅ ALL HANDLERS UPDATED (100% complete)
All handler functions now use `project_id` in SQL queries and resolve project names to IDs.

### Context Handlers
- `handle_store_context` — resolves project name→ID, INSERT uses `project_id` column
- `handle_query_context` — resolves project name→ID, FTS filter and list mode use `project_id`, SELECT JOINs projects table for name display
- `handle_clear_context` — resolves project name→ID, DELETE uses `project_id`
- `handle_add_context_alias` — resolves project name→ID, canonical/alias lookups use `project_id`, INSERT uses `project_id`
- `handle_list_projects` — uses backward-compat views (`context_with_project`, `project_changes_with_project`)

### Project Change Handlers
- `handle_add_project_change` — resolves project name→ID, uses `project_id` in WHERE and INSERT
- `handle_add_change_step` — resolves project name→ID before lookup
- `handle_list_project_changes` — resolves project name→ID, SELECT JOINs projects table for name display
- `handle_get_change_history` — resolves project name→ID before lookup
- `handle_search_project_changes` — resolves project name→ID, FTS queries JOIN projects table for name display

### Issues Handlers
- `handle_store_issue` — resolves project name→ID, uses `project_id` in WHERE and INSERT
- `handle_query_issues` — resolves project name→ID, SELECT JOINs projects table for name display
- `handle_update_issue_status` — resolves project name→ID, uses `project_id` in WHERE
- `handle_list_issues` — resolves project name→ID, SELECT JOINs projects table for name display
- `handle_update_issue_project` — resolves both old and new project names to IDs, UPDATE uses `project_id`

## ✅ Testing Checklist (12/12 PASS)

| # | Test | Status |
|---|------|--------|
| 1 | `_detect_project_id()` returns integer ID | ✅ PASS |
| 2 | `store_context` works with project name → resolves to ID | ✅ PASS |
| 3 | `query_context` FTS search returns correct results | ✅ PASS |
| 4 | `query_context` list mode shows project names via view | ✅ PASS |
| 5 | `add_context_alias` stores alias with correct project_id | ✅ PASS |
| 6 | `list_projects` returns clean list from projects table directly | ✅ PASS |
| 7 | `store_issue` / `update_issue_status` work correctly | ✅ PASS |
| 8 | `update_issue_project` changes project_id properly | ✅ PASS |
| 9 | `get_change_history` finds changes by project_id + key | ✅ PASS |
| 10 | FTS search across all entries still functions | ✅ PASS |
| 11 | No regressions in existing behavior | ✅ PASS |
| 12 | Post-migration integrity checks all pass (0 orphans) | ✅ PASS |

## Key files
- `/home/ken/Documents/Coding/llm_workspace/mcp_copy/tools/sqlite_store.py` — main file being edited
- `/home/ken/Documents/Coding/llm_workspace/mcp_copy/MIGRATION_PROJECT_TABLE.md` — migration plan
- `/home/ken/Documents/Coding/llm_workspace/mcp_copy/.mcp_cache/context.db` — database (already migrated)
