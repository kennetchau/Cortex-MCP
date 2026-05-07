# Migration: Add `projects` Table (FK-based project tracking)

## Goal
Replace `project TEXT` columns across all tables with `project_id INTEGER REFERENCES projects(id)` for referential integrity, better indexing, and cleaner alias resolution.

---

## Current Schema (project as TEXT)

| Table | Column | Constraint |
|-------|--------|------------|
| `context` | `project TEXT` | UNIQUE(key, project) |
| `context_aliases` | `alias_key TEXT`, `project TEXT` | FK → context.id |
| `context_fts` (virtual) | `key, content, project` | FTS5 index on all 3 |
| `project_changes` | `project TEXT` | UNIQUE(project, key) |
| `project_change_details` | `change_id → project_changes.id` | CASCADE DELETE |
| `project_changes_fts` (virtual) | `summary, project` | FTS5 index on both |
| `issues` | `project TEXT` | — |
| `issue_change_links` | `issue_id → issues.id`, `change_id → project_changes.id` | UNIQUE(issue_id, change_id) |
| `issues_migrate` | `project TEXT` | Legacy table (may be deprecated) |

**16 handlers** reference `project` as a text column in WHERE clauses, JOIN conditions, or INSERT/UPDATE statements.

---

## Target Schema

```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- All existing tables: project TEXT → project_id INTEGER REFERENCES projects(id)
-- FTS5 tables: drop project column, add project_name for search
-- Views: reconstruct old behavior by joining projects.name
```

### New Tables
- **`projects`** — Canonical project registry (id + name). UNIQUE(name) creates implicit B-tree index for O(log n) lookups.

### Modified Tables
- **`context`** — `project TEXT` → `project_id INTEGER REFERENCES projects(id)`
- **`context_aliases`** — `project TEXT` → `project_id INTEGER`; keep `alias_key TEXT`
- **`project_changes`** — `project TEXT` → `project_id INTEGER`
- **`project_change_details`** — No change (already references change_id)
- **`issues`** — `project TEXT` → `project_id INTEGER`
- **`issue_change_links`** — No change (already references issue_id and change_id)
- **`issues_migrate`** — Migrate same as `issues`, or document as deprecated if unused

### FTS5 Changes
- **`context_fts`** — Drop `project` column, add `project_name TEXT` column for searchable project names
- **`project_changes_fts`** — Drop `project` column, add `project_name TEXT` column

### New Views (reconstruct old behavior)
- **`context_with_project`** — JOIN context + projects → returns (id, key, content, project, updated_at)
- **`project_changes_with_project`** — JOIN project_changes + projects → returns (id, key, change_type, summary, project, created_at)
- **`issues_with_project`** — JOIN issues + projects → returns (id, key, status, title, project, ...)

---

## Migration Steps

### Phase 1: Schema Preparation

#### Step 1.1: Create `projects` table
```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### Step 1.2: Populate from existing data
Extract all distinct project names from the tables that have `project` columns:
```sql
-- Insert distinct project names (UPSERT to avoid duplicates)
INSERT OR IGNORE INTO projects (name) SELECT DISTINCT project FROM context WHERE project IS NOT NULL;
INSERT OR IGNORE INTO projects (name) SELECT DISTINCT project FROM context_aliases WHERE project IS NOT NULL;
INSERT OR IGNORE INTO projects (name) SELECT DISTINCT project FROM project_changes WHERE project IS NOT NULL;
INSERT OR IGNORE INTO projects (name) SELECT DISTINCT project FROM issues WHERE project IS NOT NULL;
INSERT OR IGNORE INTO projects (name) SELECT DISTINCT project FROM issues_migrate WHERE project IS NOT NULL;
```

### Phase 2: Migrate Tables

#### Step 2.1: Migrate `context` table
```sql
ALTER TABLE context ADD COLUMN project_id INTEGER REFERENCES projects(id);

UPDATE context 
SET project_id = (SELECT id FROM projects WHERE projects.name = context.project)
WHERE project IS NOT NULL;

-- Update UNIQUE constraint: drop old, create new with project_id
DROP INDEX IF EXISTS sqlite_autoindex_context_1;
CREATE UNIQUE INDEX idx_context_key_project ON context(key, project_id);

-- Drop old column
ALTER TABLE context DROP COLUMN project;
```

#### Step 2.2: Migrate `context_aliases` table
```sql
ALTER TABLE context_aliases ADD COLUMN project_id INTEGER REFERENCES projects(id);

UPDATE context_aliases 
SET project_id = (SELECT id FROM projects WHERE projects.name = context_aliases.project)
WHERE project IS NOT NULL;

ALTER TABLE context_aliases DROP COLUMN project;
```

#### Step 2.3: Migrate `project_changes` table
```sql
ALTER TABLE project_changes ADD COLUMN project_id INTEGER REFERENCES projects(id);

UPDATE project_changes 
SET project_id = (SELECT id FROM projects WHERE projects.name = project_changes.project)
WHERE project IS NOT NULL;

-- Update UNIQUE constraint
DROP INDEX IF EXISTS sqlite_autoindex_project_changes_1;
CREATE UNIQUE INDEX idx_pc_key_project ON project_changes(key, project_id);

ALTER TABLE project_changes DROP COLUMN project;
```

#### Step 2.4: Migrate `issues` table
```sql
ALTER TABLE issues ADD COLUMN project_id INTEGER REFERENCES projects(id);

UPDATE issues 
SET project_id = (SELECT id FROM projects WHERE projects.name = issues.project)
WHERE project IS NOT NULL;

ALTER TABLE issues DROP COLUMN project;
```

#### Step 2.5: Handle `issues_migrate` table
**Decision:** Drop and recreate `issues_migrate` with new schema if still needed; otherwise document as deprecated and remove. This table was used for a prior migration and may be dead code. Check usage before migrating.

### Phase 3: FTS5 Tables

#### Step 3.1: Recreate `context_fts` without project column, add project_name
```sql
DROP TRIGGER IF EXISTS context_ai;
DROP TRIGGER IF EXISTS context_ad;
DROP TRIGGER IF EXISTS context_au;
DROP TABLE context_fts;

CREATE VIRTUAL TABLE context_fts USING fts5(
    key, content, project_name,
    content='context', content_rowid='id'
);

-- Repopulate FTS from migrated data
INSERT INTO context_fts(rowid, key, content, project_name)
SELECT c.id, c.key, c.content, p.name
FROM context c
JOIN projects p ON c.project_id = p.id;

-- Recreate triggers with project_name
CREATE TRIGGER context_ai AFTER INSERT ON context
BEGIN
    INSERT INTO context_fts(rowid, key, content, project_name)
    VALUES (new.id, new.key, new.content, 
            (SELECT name FROM projects WHERE id = new.project_id));
END;

CREATE TRIGGER context_ad AFTER DELETE ON context
BEGIN
    INSERT INTO context_fts(context_fts, rowid, key, content, project_name)
    VALUES ('delete', old.id, old.key, old.content,
            (SELECT name FROM projects WHERE id = old.project_id));
END;

CREATE TRIGGER context_au AFTER UPDATE ON context
BEGIN
    INSERT INTO context_fts(context_fts, rowid, key, content, project_name)
    VALUES ('delete', old.id, old.key, old.content,
            (SELECT name FROM projects WHERE id = old.project_id));
    INSERT INTO context_fts(rowid, key, content, project_name)
    VALUES (new.id, new.key, new.content,
            (SELECT name FROM projects WHERE id = new.project_id));
END;
```

#### Step 3.2: Recreate `project_changes_fts` similarly
Same pattern as Step 3.1 — drop triggers, drop FTS table, recreate with `project_name`, repopulate, recreate triggers.

### Phase 4: Create Views

```sql
CREATE VIEW context_with_project AS
SELECT c.id, c.key, c.content, c.updated_at, p.name as project
FROM context c
JOIN projects p ON c.project_id = p.id;

CREATE VIEW project_changes_with_project AS
SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at, pc.updated_at, p.name as project
FROM project_changes pc
JOIN projects p ON pc.project_id = p.id;

CREATE VIEW issues_with_project AS
SELECT i.id, i.key, i.status, i.title, i.description, i.fixed_in_commit, i.created_at, i.updated_at, p.name as project
FROM issues i
JOIN projects p ON i.project_id = p.id;
```

### Phase 5: Update Python Handlers

Each handler needs to be updated to work with `project_id` instead of `project TEXT`:

| Handler | Before | After |
|---------|--------|-------|
| `_detect_project_id()` | Returns `str` (name) | Returns `int` (ID). Looks up name in `projects` table. |
| `_resolve_key()` | `WHERE key=? AND project=?` | `WHERE key=? AND project_id=?` |
| `handle_store_context` | `INSERT INTO context (key, content, project)` | `INSERT INTO context (key, content, project_id)` |
| `handle_query_context` (FTS) | `WHERE c.project = ?` | `WHERE c.project_id = ?` |
| `handle_query_context` (list) | `WHERE c.project = ?` | `WHERE c.project_id = ?` |
| `handle_clear_context` | `DELETE FROM context WHERE project = ?` | `DELETE FROM context WHERE project_id = ?` |
| `handle_list_projects` | UNION ALL + dedup in Python | `SELECT name, MAX(updated_at) FROM projects GROUP BY name` — much simpler! |
| `handle_add_context_alias` | `INSERT INTO context_aliases (... alias_key, project)` | `INSERT INTO context_aliases (... alias_key, project_id)` |
| `handle_add_project_change` | `INSERT INTO project_changes (project, ...)` | `INSERT INTO project_changes (project_id, ...)` |
| `handle_add_change_step` | `SELECT ... WHERE project=? AND key=?` | `SELECT ... WHERE project_id=? AND key=?` |
| `handle_list_project_changes` | `WHERE project = ?` | `WHERE project_id = ?` |
| `handle_get_change_history` | `WHERE project=? AND key=?` | `WHERE project_id=? AND key=?` |
| `handle_search_project_changes` | `WHERE pc.project = ?` | `WHERE pc.project_id = ?` |
| `handle_store_issue` | `INSERT INTO issues (project, ...)` | `INSERT INTO issues (project_id, ...)` |
| `handle_query_issues` | `WHERE i.project = ?` | `WHERE i.project_id = ?` |
| `handle_update_issue_status` | `WHERE project=? AND key=?` | `WHERE project_id=? AND key=?` |
| `handle_list_issues` | `WHERE project = ?` | `WHERE project_id = ?` |
| `handle_update_issue_project` | `UPDATE issues SET project = ?` | `UPDATE issues SET project_id = ?` |

**Critical: `_detect_project_id()` return type change**
- Currently returns `str`. After migration, returns `int`.
- All callers compare it: `if project != "default"`. This works fine since `"default"` string comparison with int still works in Python.
- But if any code does `resolved_project + " something"` (string concat), that breaks. Need to audit for implicit string assumptions.

### Phase 6: Backward Compatibility Layer

Add a helper to resolve project name → ID transparently:
```python
def _resolve_project_id(conn, project_name):
    """Resolve project name to ID. Creates if doesn't exist."""
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    # Auto-create for backward compat during transition
    try:
        cursor.execute("INSERT INTO projects (name) VALUES (?)", (project_name,))
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        # Another thread created it first — retry lookup
        cursor.execute("SELECT id FROM projects WHERE name = ?", (project_name,))
        return cursor.fetchone()[0]
```

---

## Migration Script Structure

Create `migrate_projects.py` that:
1. Opens connection to `.mcp_cache/context.db`
2. Wraps all steps in a single transaction (`BEGIN` / `COMMIT`)
3. Logs progress at each phase
4. Rolls back on any error
5. Runs post-migration integrity checks

```python
# Pseudocode structure
def migrate():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        print("Phase 1: Creating projects table...")
        run_sql(conn, PHASE_1_SQL)
        
        print("Phase 2: Migrating tables...")
        run_sql(conn, PHASE_2_SQL)
        
        print("Phase 3: Rebuilding FTS5 indexes...")
        run_sql(conn, PHASE_3_SQL)
        
        print("Phase 4: Creating views...")
        run_sql(conn, PHASE_4_SQL)
        
        print("Phase 5: Updating handlers...")
        # Handler updates happen in sqlite_store.py, not as SQL steps
        
        print("Phase 6: Running integrity checks...")
        verify_integrity(conn)
        
        conn.commit()
        print("Migration complete!")
    except Exception as e:
        conn.rollback()
        print(f"Migration failed, rolled back: {e}")
        raise
```

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Data loss during migration | High | Single transaction rollback; test on DB copy first |
| FTS index corruption | Medium | Drop + recreate FTS tables atomically |
| Handler breakage (string→int) | Medium | Audit all callers of `_detect_project_id()` for string operations |
| `issues_migrate` orphaned | Low | Migrate it or document as deprecated |
| `_resolve_key()` alias lookup breaks | Medium | Update to use `project_id` in WHERE clause |

---

## Rollback Plan

All changes wrapped in single transaction. If any step fails:
1. `ROLLBACK` restores database to pre-migration state
2. No partial schema changes persist
3. Keep a backup copy before running (`cp context.db context.db.bak`)

---

## Post-Migration Verification

```sql
-- Verify no orphaned project references
SELECT 'orphaned_context' as check_name, COUNT(*) FROM context WHERE project_id NOT IN (SELECT id FROM projects);
SELECT 'orphaned_issues' as check_name, COUNT(*) FROM issues WHERE project_id NOT IN (SELECT id FROM projects);
SELECT 'orphaned_aliases' as check_name, COUNT(*) FROM context_aliases WHERE project_id NOT IN (SELECT id FROM projects);
SELECT 'orphaned_changes' as check_name, COUNT(*) FROM project_changes WHERE project_id NOT IN (SELECT id FROM projects);
-- All should return 0

-- Verify FTS is populated
SELECT COUNT(*) FROM context_fts;
SELECT COUNT(*) FROM project_changes_fts;
-- Should match row counts in base tables

-- Verify views work
SELECT * FROM context_with_project LIMIT 1;
SELECT * FROM project_changes_with_project LIMIT 1;
SELECT * FROM issues_with_project LIMIT 1;
-- Should return results without errors
```

---

## Notes & Trade-offs

- **FTS triggers use subqueries**: `(SELECT name FROM projects WHERE id = new.project_id)` fires per INSERT/UPDATE. Fine at current scale (<1000 rows). If data grows significantly, denormalize `project_name` directly on base tables instead.
- **Views are transitional**: Handlers query base tables with `project_id` directly. Views exist mainly for ad-hoc queries and backward compatibility during transition — can be removed once handler migration is complete.
- **No schema version table needed**: Migration runs once; `_init_db()` uses `IF NOT EXISTS` so it's safe to call repeatedly.

---

## Testing Checklist

- [ ] `_detect_project_id()` returns integer ID
- [ ] `store_context` works with project name → resolves to ID
- [ ] `query_context` FTS search returns correct results
- [ ] `query_context` list mode shows project names via view
- [ ] `add_context_alias` stores alias with correct project_id
- [ ] `list_projects` returns clean list from projects table directly
- [ ] `store_issue` / `update_issue_status` work correctly
- [ ] `update_issue_project` changes project_id properly
- [ ] `get_change_history` finds changes by project_id + key
- [ ] FTS search across all entries still functions
- [ ] No regressions in existing behavior
- [ ] Post-migration integrity checks all pass (0 orphans)
