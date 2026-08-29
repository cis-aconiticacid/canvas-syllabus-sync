import sqlite3
import os
from canvasapi import Canvas
from utils import normalize_course_code, now_iso


def sync_courses(canvas: Canvas, conn: sqlite3.Connection, syllabus_store_root: str) -> None:
    """
    从 Canvas 拉取所有活跃课程，写入本地 courses 表，并将 syllabus 原文保存到本地文件系统。

    - syllabus 原文保存路径：{syllabus_store_root}/{term_id}/{course_code}.html
    - 新课程：插入
    - 已有课程且 canvas_updated_at 有变化：更新
    - 无变化：跳过
    - 找不到对应 term_id 的课程：静默跳过（应先调用 sync_terms）

    Args:
        canvas:               已初始化的 canvasapi Canvas 对象。
        conn:                 已打开的 SQLite 连接。
        syllabus_store_root:  syllabus 文件的根目录，如 "file/storage"。
    """
    cursor = conn.cursor()

    courses = canvas.get_courses(
        enrollment_state="active",
        include=["term", "syllabus_body"]
    )

    for course in courses:
        term = getattr(course, "term", None)
        if term is None:
            continue

        # term_id 从 terms 表里找对应的自定义 id
        term_name = term.get("name", "")
        term_row = cursor.execute(
            "SELECT id FROM terms WHERE name = ?", (term_name,)
        ).fetchone()
        if term_row is None:
            continue  # term 还没同步，跳过（不应该发生，terms 先同步）
        term_id = term_row["id"]

        course_id = str(course.id)
        course_code = normalize_course_code(getattr(course, "course_code", "") or "")
        name = getattr(course, "name", "")
        canvas_updated_at = getattr(course, "updated_at", None)
        syllabus_body = getattr(course, "syllabus_body", None)

        # syllabus 原文写到本地
        syllabus_ref = None
        if syllabus_body:
            syllabus_dir = os.path.join(syllabus_store_root, term_id)
            os.makedirs(syllabus_dir, exist_ok=True)
            syllabus_path = os.path.join(syllabus_dir, f"{course_code}.html")
            with open(syllabus_path, "w", encoding="utf-8") as f:
                f.write(syllabus_body)
            syllabus_ref = f"file:///{os.path.abspath(syllabus_path)}"

        existing = cursor.execute(
            "SELECT canvas_updated_at FROM courses WHERE id = ?", (course_id,)
        ).fetchone()

        if existing is None:
            cursor.execute("""
                INSERT INTO courses
                    (id, term_id, course_code, name, syllabus_ref, canvas_updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (course_id, term_id, course_code, name, syllabus_ref, canvas_updated_at))
        elif existing["canvas_updated_at"] != canvas_updated_at:
            cursor.execute("""
                UPDATE courses
                SET term_id=?, course_code=?, name=?, syllabus_ref=?,
                    canvas_updated_at=?, updated_at=?
                WHERE id=?
            """, (term_id, course_code, name, syllabus_ref,
                  canvas_updated_at, now_iso(), course_id))
        # 无变化则跳过

    conn.commit()
