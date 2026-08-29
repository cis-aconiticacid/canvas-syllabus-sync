import sqlite3
import json
from canvasapi import Canvas
from utils import now_iso


def _map_submission_status(assignment) -> str:
    """
    从 assignment 对象的 submission 字段判断本地 status。
    通过 include[]=submission 拉取，不单独调 get_submission API。

    Args:
        assignment: canvasapi Assignment 对象，需包含 submission 字段。

    Returns:
        "completed" 如果已提交或已评分，否则 "pending"。
    """
    try:
        submission = getattr(assignment, "submission", None)
        if submission:
            state = submission.get("workflow_state", "")
            if state in ("submitted", "graded"):
                return "completed"
    except Exception:
        pass
    return "pending"


def sync_tasks(canvas: Canvas, conn: sqlite3.Connection) -> dict:
    """
    从 Canvas 拉取所有课程的 assignment，写入本地 tasks 表（source_type = canvas_native）。

    去重规则：
    - canvas_assignment_id 已存在且 title/due_at/points_possible 均无变化：跳过
    - 有变化：更新
    - 不存在：插入

    Args:
        canvas: 已初始化的 canvasapi Canvas 对象。
        conn:   已打开的 SQLite 连接。

    Returns:
        dict，包含 added / updated / skipped 计数。
    """
    cursor = conn.cursor()
    counts = {"added": 0, "updated": 0, "skipped": 0}

    courses = cursor.execute("SELECT id, course_code FROM courses").fetchall()

    for course_row in courses:
        course_id = course_row["id"]
        try:
            canvas_course = canvas.get_course(course_id)
            assignments = canvas_course.get_assignments(include=["submission"])
        except Exception:
            continue

        for assignment in assignments:
            canvas_assignment_id = str(assignment.id)
            title = getattr(assignment, "name", "")
            description = getattr(assignment, "description", None)
            due_at = getattr(assignment, "due_at", None)
            has_explicit_due = 1 if due_at else 0
            points_possible = getattr(assignment, "points_possible", None)
            submission_types = json.dumps(
                getattr(assignment, "submission_types", [])
            )

            existing = cursor.execute(
                """SELECT id, title, due_at_latest, points_possible
                   FROM tasks WHERE canvas_assignment_id = ?""",
                (canvas_assignment_id,)
            ).fetchone()

            status = _map_submission_status(assignment)

            if existing is None:
                cursor.execute("""
                    INSERT INTO tasks (
                        id, course_id, title, description,
                        has_explicit_due, due_at_earliest, due_at_latest,
                        source_type, points_possible, submission_types,
                        canvas_assignment_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'canvas_native', ?, ?, ?, ?)
                """, (
                    canvas_assignment_id, course_id, title, description,
                    has_explicit_due, due_at, due_at,
                    points_possible, submission_types,
                    canvas_assignment_id, status
                ))
                counts["added"] += 1
            else:
                changed = (
                    existing["title"] != title or
                    existing["due_at_latest"] != due_at or
                    existing["points_possible"] != points_possible
                )
                if changed:
                    cursor.execute("""
                        UPDATE tasks SET
                            title=?, due_at_earliest=?, due_at_latest=?,
                            has_explicit_due=?, points_possible=?,
                            submission_types=?, status=?, updated_at=?
                        WHERE canvas_assignment_id=?
                    """, (
                        title, due_at, due_at, has_explicit_due,
                        points_possible, submission_types,
                        status, now_iso(), canvas_assignment_id
                    ))
                    counts["updated"] += 1
                else:
                    counts["skipped"] += 1

    conn.commit()
    return counts
