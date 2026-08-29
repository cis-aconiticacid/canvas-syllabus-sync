import sqlite3
from canvasapi import Canvas
from utils import now_iso


TERM_NAME_MAP = {
    "Fall":   "F",
    "Spring": "SP",
    "Summer": "SU",
}


def _canvas_term_to_id(term_name: str) -> str | None:
    """
    将 Canvas term 名称转换为自定义 term_id 格式。

    Examples:
        "Fall 2025"   → "25F"
        "Spring 2025" → "25SP"
        "Summer 2024" → "24SU"

    Args:
        term_name: Canvas 返回的学期名称字符串。

    Returns:
        自定义 term_id，无法识别时返回 None。
    """
    for season, suffix in TERM_NAME_MAP.items():
        if season in term_name:
            # 找年份后两位
            import re
            match = re.search(r'\d{4}', term_name)
            if match:
                year2 = match.group()[-2:]
                return f"{year2}{suffix}"
    return None


def sync_terms(canvas: Canvas, conn: sqlite3.Connection) -> None:
    """
    从用户已选课程中提取 term 信息，写入本地 terms 表。
    使用课程 API 而非 account API，学生账号有权限访问。

    - 新 term：插入
    - 已有 term 且 start_at/end_at 有变化：更新
    - 无变化：跳过
    - 无法识别名称的 term（如 Default Term）：静默跳过

    Args:
        canvas: 已初始化的 canvasapi Canvas 对象。
        conn:   已打开的 SQLite 连接。
    """
    cursor = conn.cursor()

    courses = canvas.get_courses(enrollment_state="active", include=["term"])
    seen = set()

    for course in courses:
        term = getattr(course, "term", None)
        if term is None:
            continue

        term_name = term.get("name", "")
        term_id = _canvas_term_to_id(term_name)
        if term_id is None or term_id in seen:
            continue
        seen.add(term_id)

        start_at = term.get("start_at")
        end_at = term.get("end_at")

        existing = cursor.execute(
            "SELECT start_at, end_at FROM terms WHERE id = ?", (term_id,)
        ).fetchone()

        if existing is None:
            cursor.execute("""
                INSERT INTO terms (id, name, start_at, end_at)
                VALUES (?, ?, ?, ?)
            """, (term_id, term_name, start_at, end_at))
        elif existing["start_at"] != start_at or existing["end_at"] != end_at:
            cursor.execute("""
                UPDATE terms SET name=?, start_at=?, end_at=?, updated_at=?
                WHERE id=?
            """, (term_name, start_at, end_at, now_iso(), term_id))

    conn.commit()
