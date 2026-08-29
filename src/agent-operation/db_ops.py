import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../canvas-sync"))
from db import get_connection
from utils import now_iso


def insert_file_record(
    canvas_file_id: str,
    file_path: str,
    manifest_path: str,
    db_path: str,
) -> dict:
    """
    将文件信息写入 files 表，并将 manifest 中该条目 status 更新为 inserted。
    两步操作原子执行：数据库写入失败时 manifest 不更新。

    幂等性：canvas_file_id 已存在时直接返回成功，不重复插入。

    Args:
        canvas_file_id: Canvas 文件 id。
        file_path:      文件在正式存储区的完整路径（move_file 返回的 final_path）。
        manifest_path:  manifest.json 的文件路径。
        db_path:        SQLite 文件路径。

    Returns:
        dict，包含：
            success (bool)
            file_id (int)       插入成功后的数据库自增 id
            error_code (str)    失败时填写，取值：
                                source_not_found / course_not_found / db_error
    """
    from . import manifest as mf

    entry = mf.find_entry(manifest_path, canvas_file_id)
    if entry is None:
        return {"success": False, "file_id": None, "error_code": "source_not_found"}

    conn = get_connection(db_path)
    try:
        course_row = conn.execute(
            "SELECT id FROM courses WHERE id = ?", (entry["course_id"],)
        ).fetchone()
        if course_row is None:
            return {"success": False, "file_id": None, "error_code": "course_not_found"}

        existing = conn.execute(
            "SELECT id FROM files WHERE canvas_file_id = ?", (canvas_file_id,)
        ).fetchone()
        if existing:
            mf.update_status(manifest_path, canvas_file_id, "inserted")
            return {"success": True, "file_id": existing["id"], "error_code": None}

        cursor = conn.execute("""
            INSERT INTO files (
                canvas_file_id, course_id, term_id, filename, file_path,
                week_number, file_size, canvas_updated_at, published_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        """, (
            canvas_file_id,
            entry["course_id"],
            entry["term_id"],
            entry["filename"],
            file_path,
            entry.get("week_number"),
            entry.get("file_size"),
            entry.get("canvas_updated_at"),
            entry.get("published_at"),
        ))
        conn.commit()
        file_id = cursor.lastrowid
    except sqlite3.Error:
        conn.rollback()
        return {"success": False, "file_id": None, "error_code": "db_error"}
    finally:
        conn.close()

    mf.update_status(manifest_path, canvas_file_id, "inserted")
    return {"success": True, "file_id": file_id, "error_code": None}


def update_task_status(task_id: str, status: str, db_path: str) -> dict:
    """
    更新 tasks 表中指定任务的 status 字段。

    Args:
        task_id:  tasks.id。
        status:   新状态，取值：pending / completed / dismissed。
        db_path:  SQLite 文件路径。

    Returns:
        dict，包含：
            success (bool)
            error_code (str)    失败时填写，取值：
                                invalid_status / task_not_found / db_error
    """
    valid = {"pending", "completed", "dismissed"}
    if status not in valid:
        return {"success": False, "error_code": "invalid_status"}

    conn = get_connection(db_path)
    try:
        result = conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), task_id)
        )
        conn.commit()
        if result.rowcount == 0:
            return {"success": False, "error_code": "task_not_found"}
    except sqlite3.Error:
        conn.rollback()
        return {"success": False, "error_code": "db_error"}
    finally:
        conn.close()

    return {"success": True, "error_code": None}


def get_course_info(course_code: str, term_id: str, db_path: str) -> dict | None:
    """
    查询课程基本信息，用于处理文件前确认课程存在。

    Args:
        course_code: 课程代码，如 CS544。
        term_id:     学期 id，如 25F。
        db_path:     SQLite 文件路径。

    Returns:
        包含 course_id / course_code / term_id / name /
        syllabus_ref / syllabus_parsed_at 的 dict；找不到返回 None。
    """
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT id, course_code, term_id, name, syllabus_ref, syllabus_parsed_at
               FROM courses WHERE course_code = ? AND term_id = ?""",
            (course_code, term_id)
        ).fetchone()
    finally:
        conn.close()

    return dict(row) if row else None


def get_week_range(
    course_code: str,
    term_id: str,
    week_number: int,
    week_config_path: str,
) -> dict | None:
    """
    根据 file/week.config 返回指定 week 的日期区间。
    week 配置按 term 粒度，course_code 参数当前保留供未来扩展使用。

    Args:
        course_code:      课程代码（保留参数，当前未使用）。
        term_id:          学期 id，如 25F。
        week_number:      week 编号，1 起始。
        week_config_path: week.config 文件路径。

    Returns:
        包含 week_number / start_date / end_date 的 dict；
        term_id 不存在或 week_number 超出范围时返回 None。
    """
    import json
    from datetime import datetime, timedelta

    with open(week_config_path, "r") as f:
        config = json.load(f)

    term_cfg = config.get(term_id)
    if term_cfg is None:
        return None

    total_weeks = term_cfg.get("total_weeks", 16)
    if week_number < 1 or week_number > total_weeks:
        return None

    week1_start = datetime.fromisoformat(term_cfg["week1_start"])
    start = week1_start + timedelta(weeks=week_number - 1)
    end = start + timedelta(days=6)

    return {
        "week_number": week_number,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
    }
