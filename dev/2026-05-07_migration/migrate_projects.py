"""Migration: Add projects table with FK constraints."""
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/home/ken/Documents/Coding/llm_workspace/.mcp_cache/context.db")


def migrate():
    """Migrate project TEXT columns to project_id INTEGER FK."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("BEGIN")
        
        # ── Phase 1: Create projects table ──────────────────────────────
        print("Phase 1: Creating projects table...")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        for table in ["context", "context_aliases", "project_changes", "issues", "issues_migrate"]:
            try:
                conn.execute(f"""
                    INSERT OR IGNORE INTO projects (name)
                    SELECT DISTINCT project FROM {table} WHERE project IS NOT NULL
                """)
            except Exception as e:
                print(f"  Warning: could not populate from {table}: {e}")
        
        count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        print(f"  Created {count} project entries.")
        
        # ── Phase 2: Migrate tables using recreate approach ─────────────
        print("\nPhase 2: Migrating tables...")
        
        # Drop FTS triggers first
        for trigger in ["context_ai", "context_ad", "context_au", "pc_ai", "pc_ad", "pc_au"]:
            try:
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            except Exception:
                pass
        
        # Drop FTS tables
        conn.execute("DROP TABLE IF EXISTS context_fts")
        conn.execute("DROP TABLE IF EXISTS project_changes_fts")
        
        for table, idx_name, idx_cols in [
            ("context", "idx_context_key_project", "(key, project_id)"),
            ("project_changes", "idx_pc_key_project", "(key, project_id)"),
            ("issues", None, None),
            ("context_aliases", None, None),
        ]:
            print(f"  Migrating {table}...")
            
            # Get current columns (excluding 'project')
            cursor = conn.execute(f"PRAGMA table_info({table})")
            cols = [row[1] for row in cursor.fetchall() if row[1] != 'project']
            col_defs = ", ".join([f"{c} TEXT" if c in ('key', 'content', 'alias_key', 'summary', 'step', 'details', 'files_changed', 'description', 'fixed_in_commit', 'name') 
                                  else f"{c} INTEGER" if c == 'id' or c == 'context_id' or c == 'change_id' or c == 'issue_id'
                                  else f"{c} TIMESTAMP DEFAULT CURRENT_TIMESTAMP" if c in ('updated_at', 'created_at', 'linked_at')
                                  else f"{c} DATE" if c == 'date'
                                  else f"{c} TEXT"  # default to TEXT for anything we haven't categorized
                                  for c in cols])
            
            # Create new table without project column
            conn.execute(f"""
                CREATE TABLE {table}_new ({col_defs}, project_id INTEGER REFERENCES projects(id))
            """)
            
            # Copy data, resolving project name to ID
            col_list = ", ".join(cols + ['project_id'])
            sub_select = ", ".join([f"{table}.{c}" if c != 'project_id' 
                                     else f"(SELECT id FROM projects WHERE projects.name = {table}.project)" 
                                     for c in (cols + ['project_id'])])
            
            conn.execute(f"""
                INSERT INTO {table}_new ({col_list})
                SELECT {sub_select} FROM {table}
            """)
            
            # Drop old table and rename new one
            conn.execute(f"DROP TABLE {table}")
            conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            
            # Recreate UNIQUE index if applicable
            if idx_name:
                try:
                    conn.execute(f"CREATE UNIQUE INDEX {idx_name} ON {table}{idx_cols}")
                except Exception as e:
                    print(f"    Warning creating index: {e}")
        
        # Migrate issues_migrate the same way
        try:
            print("  Migrating issues_migrate...")
            cursor = conn.execute("PRAGMA table_info(issues_migrate)")
            cols = [row[1] for row in cursor.fetchall() if row[1] != 'project']
            col_defs = ", ".join([f"{c} TEXT" if c in ('key', 'alias_key', 'summary', 'step', 'details', 'files_changed', 'description', 'fixed_in_commit', 'name') 
                                  else f"{c} INTEGER" if c == 'id' or c == 'context_id' or c == 'change_id' or c == 'issue_id'
                                  else f"{c} TIMESTAMP DEFAULT CURRENT_TIMESTAMP" if c in ('updated_at', 'created_at', 'linked_at')
                                  else f"{c} DATE" if c == 'date'
                                  else f"{c} TEXT"
                                  for c in cols])
            
            conn.execute(f"""
                CREATE TABLE issues_migrate_new ({col_defs}, project_id INTEGER REFERENCES projects(id))
            """)
            
            col_list = ", ".join(cols + ['project_id'])
            sub_select = ", ".join([f"im.{c}" if c != 'project_id' 
                                     else f"(SELECT id FROM projects WHERE projects.name = im.project)" 
                                     for c in (cols + ['project_id'])])
            
            conn.execute(f"""
                INSERT INTO issues_migrate_new ({col_list})
                SELECT {sub_select} FROM issues_migrate im
            """)
            
            conn.execute("DROP TABLE issues_migrate")
            conn.execute("ALTER TABLE issues_migrate_new RENAME TO issues_migrate")
        except Exception as e:
            print(f"    issues_migrate skipped: {e}")
        
        # ── Phase 3: Rebuild FTS5 ───────────────────────────────────────
        print("\nPhase 3: Rebuilding FTS5 indexes...")
        
        # context_fts
        print("  Creating context_fts...")
        conn.execute("DROP TABLE IF EXISTS context_fts")
        for t in ["context_fts_config", "context_fts_data", "context_fts_docsize", "context_fts_idx"]:
            try: conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception: pass
        
        # Note: no content='context' param — we manage sync via triggers manually
        conn.execute("""
            CREATE VIRTUAL TABLE context_fts USING fts5(
                key, content, project_name
            )
        """)
        print("  Inserting into context_fts...")
        conn.execute("""
            INSERT INTO context_fts(rowid, key, content, project_name)
            SELECT c.id, c.key, c.content, p.name
            FROM context c
            JOIN projects p ON c.project_id = p.id
        """)
        
        conn.execute("""
            CREATE TRIGGER context_ai AFTER INSERT ON context
            BEGIN
                INSERT INTO context_fts(rowid, key, content, project_name)
                VALUES (new.id, new.key, new.content, 
                        (SELECT name FROM projects WHERE id = new.project_id));
            END
        """)
        conn.execute("""
            CREATE TRIGGER context_ad AFTER DELETE ON context
            BEGIN
                INSERT INTO context_fts(context_fts, rowid, key, content, project_name)
                VALUES ('delete', old.id, old.key, old.content,
                        (SELECT name FROM projects WHERE id = old.project_id));
            END
        """)
        conn.execute("""
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
        print("  Rebuilt context_fts.")
        
        # project_changes_fts
        conn.execute("DROP TABLE IF EXISTS project_changes_fts")
        for t in ["project_changes_fts_config", "project_changes_fts_data", "project_changes_fts_docsize", "project_changes_fts_idx"]:
            try: conn.execute(f"DROP TABLE IF EXISTS {t}")
            except Exception: pass
        
        conn.execute("""
            CREATE VIRTUAL TABLE project_changes_fts USING fts5(
                summary, project_name
            )
        """)
        conn.execute("""
            INSERT INTO project_changes_fts(rowid, summary, project_name)
            SELECT pc.id, pc.summary, p.name
            FROM project_changes pc
            JOIN projects p ON pc.project_id = p.id
        """)
        
        conn.execute("""
            CREATE TRIGGER pc_ai AFTER INSERT ON project_changes
            BEGIN
                INSERT INTO project_changes_fts(rowid, summary, project_name)
                VALUES (new.id, new.summary, 
                        (SELECT name FROM projects WHERE id = new.project_id));
            END
        """)
        conn.execute("""
            CREATE TRIGGER pc_ad AFTER DELETE ON project_changes
            BEGIN
                INSERT INTO project_changes_fts(project_changes_fts, rowid, summary, project_name)
                VALUES ('delete', old.id, old.summary,
                        (SELECT name FROM projects WHERE id = old.project_id));
            END
        """)
        conn.execute("""
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
        print("  Rebuilt project_changes_fts.")
        
        # ── Phase 4: Create views ───────────────────────────────────────
        print("\nPhase 4: Creating views...")
        
        conn.execute("""
            CREATE VIEW IF NOT EXISTS context_with_project AS
            SELECT c.id, c.key, c.content, c.updated_at, p.name as project
            FROM context c
            JOIN projects p ON c.project_id = p.id
        """)
        
        conn.execute("""
            CREATE VIEW IF NOT EXISTS project_changes_with_project AS
            SELECT pc.id, pc.key, pc.change_type, pc.summary, pc.created_at, pc.updated_at, p.name as project
            FROM project_changes pc
            JOIN projects p ON pc.project_id = p.id
        """)
        
        conn.execute("""
            CREATE VIEW IF NOT EXISTS issues_with_project AS
            SELECT i.id, i.key, i.status, i.title, i.description, i.fixed_in_commit, i.created_at, i.updated_at, p.name as project
            FROM issues i
            JOIN projects p ON i.project_id = p.id
        """)
        print("  Created 3 views.")
        
        # ── Phase 6: Integrity checks ───────────────────────────────────
        print("\nPhase 6: Running integrity checks...")
        
        checks = [
            ("orphaned_context", "SELECT COUNT(*) FROM context WHERE project_id NOT IN (SELECT id FROM projects)"),
            ("orphaned_issues", "SELECT COUNT(*) FROM issues WHERE project_id NOT IN (SELECT id FROM projects)"),
            ("orphaned_aliases", "SELECT COUNT(*) FROM context_aliases WHERE project_id NOT IN (SELECT id FROM projects)"),
            ("orphaned_changes", "SELECT COUNT(*) FROM project_changes WHERE project_id NOT IN (SELECT id FROM projects)"),
        ]
        
        all_clean = True
        for name, sql in checks:
            count = conn.execute(sql).fetchone()[0]
            status = "✓" if count == 0 else "✗"
            print(f"  {status} {name}: {count}")
            if count > 0:
                all_clean = False
        
        fts_count = conn.execute("SELECT COUNT(*) FROM context_fts").fetchone()[0]
        base_count = conn.execute("SELECT COUNT(*) FROM context").fetchone()[0]
        print(f"  {'✓' if fts_count == base_count else '✗'} FTS sync: {fts_count} vs {base_count}")
        
        if not all_clean or fts_count != base_count:
            conn.rollback()
            print("\nIntegrity check failed — rolled back.")
            return False
        
        conn.commit()
        print("\n✅ Migration complete!")
        return True
        
    except Exception as e:
        conn.rollback()
        print(f"\n❌ Migration failed, rolled back: {e}")
        raise


if __name__ == "__main__":
    success = migrate()
    sys.exit(0 if success else 1)
