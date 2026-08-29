import sqlite3
import os


def get_connection(db_path: str) -> sqlite3.Connection:
    """
    返回一个 SQLite 连接，启用外键约束，row_factory 设为 sqlite3.Row。

    Args:
        db_path: SQLite 文件路径。

    Returns:
        sqlite3.Connection
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    """
    初始化数据库，创建所有表和索引（如果不存在）。
    幂等操作，重复调用安全。

    Args:
        db_path: SQLite 文件路径。
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS terms (
            id          TEXT PRIMARY KEY,
            name        TEXT,
            start_at    TEXT,
            end_at      TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS courses (
            id                  TEXT PRIMARY KEY,
            term_id             TEXT NOT NULL REFERENCES terms(id),
            course_code         TEXT NOT NULL,
            name                TEXT,
            syllabus_ref        TEXT,
            syllabus_parsed_at  TEXT,
            canvas_updated_at   TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id                      TEXT PRIMARY KEY,
            course_id               TEXT NOT NULL REFERENCES courses(id),
            title                   TEXT,
            description             TEXT,
            has_explicit_due        INTEGER NOT NULL DEFAULT 0,
            due_at_earliest         TEXT,
            due_at_latest           TEXT,
            source_type             TEXT NOT NULL,
            source_document         TEXT,
            points_possible         REAL,
            submission_types        TEXT,
            canvas_assignment_id    TEXT,
            confidence              REAL,
            is_recurring            INTEGER NOT NULL DEFAULT 0,
            recurrence_note         TEXT,
            status                  TEXT NOT NULL DEFAULT 'pending',
            created_at              TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS files (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            canvas_file_id      TEXT UNIQUE NOT NULL,
            course_id           TEXT NOT NULL REFERENCES courses(id),
            term_id             TEXT NOT NULL REFERENCES terms(id),
            filename            TEXT,
            file_path           TEXT,
            week_number         INTEGER,
            file_size           INTEGER,
            canvas_updated_at   TEXT,
            published_at        TEXT,
            status              TEXT NOT NULL DEFAULT 'active',
            error_reason        TEXT,
            created_at          TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sync_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id           TEXT REFERENCES courses(id),
            sync_type           TEXT NOT NULL,
            provider            TEXT,
            status              TEXT NOT NULL,
            error_message       TEXT,
            tasks_added         INTEGER DEFAULT 0,
            tasks_updated       INTEGER DEFAULT 0,
            tasks_skipped       INTEGER DEFAULT 0,
            tasks_ai_resolved   INTEGER DEFAULT 0,
            ai_resolution_log   TEXT,
            started_at          TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_course_id ON tasks(course_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_due_at_earliest ON tasks(due_at_earliest);
        CREATE INDEX IF NOT EXISTS idx_tasks_due_at_latest ON tasks(due_at_latest);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_source_type ON tasks(source_type);
        CREATE INDEX IF NOT EXISTS idx_tasks_canvas_assignment_id ON tasks(canvas_assignment_id);
        CREATE INDEX IF NOT EXISTS idx_courses_term_id ON courses(term_id);
        CREATE INDEX IF NOT EXISTS idx_courses_course_code ON courses(course_code);
        CREATE INDEX IF NOT EXISTS idx_files_canvas_file_id ON files(canvas_file_id);
        CREATE INDEX IF NOT EXISTS idx_files_course_id ON files(course_id);
    """)

    conn.commit()
    conn.close()
