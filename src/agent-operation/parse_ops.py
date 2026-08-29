import os
import sys
import uuid
import json
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../canvas-sync"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../provider"))
from db import get_connection
from utils import now_iso
from provider import get_provider, ProviderRequest, ProviderError

INFERRED_TASK_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["title", "has_explicit_due", "confidence"],
        "properties": {
            "title":            {"type": "string",  "description": "任务标题"},
            "description":      {"type": "string",  "description": "任务描述"},
            "has_explicit_due": {"type": "boolean", "description": "是否有明确截止日"},
            "due_at_earliest":  {"type": "string",  "description": "截止区间起点 ISO 8601，无则 null"},
            "due_at_latest":    {"type": "string",  "description": "截止区间终点 ISO 8601，无则 null"},
            "is_recurring":     {"type": "boolean", "description": "是否周期性任务"},
            "recurrence_note":  {"type": "string",  "description": "周期性说明，非周期性为 null"},
            "confidence":       {"type": "number",  "description": "推断置信度 0.0–1.0"},
        },
    },
}

DEDUP_SCHEMA = {
    "type": "object",
    "required": ["decision", "reasoning"],
    "properties": {
        "decision":  {"type": "string",  "description": "keep_old | replace | keep_both"},
        "reasoning": {"type": "string",  "description": "决策理由"},
    },
}


def parse_course(
    course_id: str,
    term_id: str,
    provider_id: str,
    provider_configs: dict,
    db_path: str,
    force_reparse: bool = False,
) -> dict:
    """
    解析指定课程的 syllabus，提取隐性任务并写入 tasks 表。
    完整流程：前置检查 → 读 syllabus → AI 提取 → 逐条去重 → 批量写库 → 更新状态。

    Args:
        course_id:        courses.id（Canvas course_id）。
        term_id:          学期 id，如 25F。
        provider_id:      使用的 AI provider。
        provider_configs: 所有 provider 配置字典。
        db_path:          SQLite 文件路径。
        force_reparse:    True 时忽略 syllabus_parsed_at，强制重新解析。

    Returns:
        dict，包含 status / tasks_added / tasks_updated / tasks_skipped /
        tasks_ai_resolved / error_message / sync_log_id。
    """
    started_at = now_iso()
    counts = {"added": 0, "updated": 0, "skipped": 0, "ai_resolved": 0}
    ai_resolution_log = []
    error_message = None
    status = "success"

    conn = get_connection(db_path)
    try:
        # Step 1 — 前置检查
        course = conn.execute(
            "SELECT id, course_code, name, syllabus_ref, syllabus_parsed_at FROM courses WHERE id = ?",
            (course_id,)
        ).fetchone()

        if course is None:
            return _fail("course_not_found", started_at, conn, course_id)

        if not course["syllabus_ref"]:
            return _fail("syllabus_ref_empty", started_at, conn, course_id)

        if not force_reparse and course["syllabus_parsed_at"]:
            return {
                "status": "skipped", "tasks_added": 0, "tasks_updated": 0,
                "tasks_skipped": 0, "tasks_ai_resolved": 0,
                "error_message": None, "sync_log_id": None,
            }

        # Step 2 — 读取 syllabus 原文
        syllabus_path = course["syllabus_ref"].replace("file:///", "/")
        if not os.path.exists(syllabus_path):
            return _fail("syllabus_file_not_found", started_at, conn, course_id)

        with open(syllabus_path, "r", encoding="utf-8") as f:
            syllabus_body = f.read()

        # Step 3 — AI 提取隐性任务
        provider = get_provider(provider_id, provider_configs)
        request = ProviderRequest(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"你正在分析课程 {course['course_code']}（{course['name']}）的 syllabus。"
                        "请找出所有没有对应 Canvas assignment 的隐性工作要求，"
                        "例如每周阅读、课堂参与、未列出截止日的小测验等。"
                        "只提取真实存在的要求，不要推断或捏造。"
                    ),
                },
                {"role": "user", "content": syllabus_body},
            ],
            output_schema=INFERRED_TASK_SCHEMA,
            request_id=f"parse_{course_id}_{now_iso()}",
            provider_id=provider_id,
        )

        try:
            response = provider.call(request)
            candidates = response.result
        except ProviderError as e:
            return _fail(e.error_code, started_at, conn, course_id, str(e))

        # Step 4 — 逐条去重
        tasks_to_insert = []
        tasks_to_update = []

        for candidate in candidates:
            action, existing_id, resolution = _dedup(
                candidate, course, conn, provider, provider_id, provider_configs
            )
            if resolution:
                ai_resolution_log.append(resolution)
                counts["ai_resolved"] += 1

            if action == "insert":
                tasks_to_insert.append(candidate)
            elif action == "update":
                tasks_to_update.append((candidate, existing_id))
            elif action == "skip":
                counts["skipped"] += 1

        # Step 5 — 批量写库（单一事务）
        try:
            with conn:
                for task in tasks_to_insert:
                    task_id = str(uuid.uuid4())
                    conn.execute("""
                        INSERT INTO tasks (
                            id, course_id, title, description,
                            has_explicit_due, due_at_earliest, due_at_latest,
                            source_type, source_document,
                            confidence, is_recurring, recurrence_note, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ai_inferred', ?, ?, ?, ?, 'pending')
                    """, (
                        task_id, course_id,
                        task.get("title"), task.get("description"),
                        1 if task.get("has_explicit_due") else 0,
                        task.get("due_at_earliest"), task.get("due_at_latest"),
                        course["syllabus_ref"],
                        task.get("confidence"),
                        1 if task.get("is_recurring") else 0,
                        task.get("recurrence_note"),
                    ))
                    counts["added"] += 1

                for task, existing_id in tasks_to_update:
                    conn.execute("""
                        UPDATE tasks SET
                            title=?, description=?, has_explicit_due=?,
                            due_at_earliest=?, due_at_latest=?,
                            confidence=?, is_recurring=?, recurrence_note=?,
                            updated_at=?
                        WHERE id=?
                    """, (
                        task.get("title"), task.get("description"),
                        1 if task.get("has_explicit_due") else 0,
                        task.get("due_at_earliest"), task.get("due_at_latest"),
                        task.get("confidence"),
                        1 if task.get("is_recurring") else 0,
                        task.get("recurrence_note"),
                        now_iso(), existing_id,
                    ))
                    counts["updated"] += 1

        except Exception as e:
            return _fail("db_error", started_at, conn, course_id, str(e))

        # Step 6 — 更新 syllabus_parsed_at
        conn.execute(
            "UPDATE courses SET syllabus_parsed_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), course_id)
        )
        conn.commit()

    finally:
        sync_log_id = _write_sync_log(
            conn, course_id, provider_id, status, error_message,
            counts, ai_resolution_log, started_at
        )
        conn.close()

    return {
        "status": status,
        "tasks_added": counts["added"],
        "tasks_updated": counts["updated"],
        "tasks_skipped": counts["skipped"],
        "tasks_ai_resolved": counts["ai_resolved"],
        "error_message": error_message,
        "sync_log_id": sync_log_id,
    }


def _dedup(
    candidate: dict,
    course: sqlite3.Row,
    conn,
    provider,
    provider_id: str,
    provider_configs: dict,
) -> tuple[str, str | None, dict | None]:
    """
    对单条候选任务执行去重逻辑。

    流程：
    1. has_explicit_due = True → 查 ±5 天内时间区间重叠的已有任务
       has_explicit_due = False → 跳过日期匹配，直接走 title 检查
    2. 候选集为空 → insert
    3. 候选集非空 → 计算 title 重叠词
       重叠词 < 2 → insert
       重叠词 ≥ 2 → AI 三选一决策

    Args:
        candidate:        AI 提取的候选任务 dict。
        course:           当前课程的数据库行。
        conn:             已打开的 SQLite 连接。
        provider:         当前 provider 实例（用于 AI 三选一）。
        provider_id:      provider id 字符串。
        provider_configs: 所有 provider 配置。

    Returns:
        (action, existing_id, resolution_log)
        action:         "insert" | "update" | "skip" | "keep_both"
        existing_id:    replace 时的旧任务 id，其他情况为 None
        resolution_log: AI 决策记录 dict，无 AI 决策时为 None
    """
    course_id = course["id"]
    title = candidate.get("title", "")
    has_explicit_due = candidate.get("has_explicit_due", False)
    earliest = candidate.get("due_at_earliest")
    latest = candidate.get("due_at_latest")

    if has_explicit_due and earliest and latest:
        # 查日期区间重叠且在 ±5 天内的候选
        existing_rows = conn.execute("""
            SELECT id, title FROM tasks
            WHERE course_id = ?
              AND source_type = 'ai_inferred'
              AND due_at_earliest <= date(?, '+5 days')
              AND due_at_latest   >= date(?, '-5 days')
        """, (course_id, latest, earliest)).fetchall()
    else:
        # 无明确截止日，只查同课程的所有 ai_inferred 任务
        existing_rows = conn.execute("""
            SELECT id, title FROM tasks
            WHERE course_id = ? AND source_type = 'ai_inferred'
        """, (course_id,)).fetchall()

    if not existing_rows:
        return "insert", None, None

    # 找重叠词最多的候选
    best_match = None
    best_overlap = 0
    new_words = set(title.lower().split())

    for row in existing_rows:
        old_words = set(row["title"].lower().split())
        overlap = len(new_words & old_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = row

    if best_overlap < 2:
        return "insert", None, None

    # AI 三选一决策
    try:
        decision_request = ProviderRequest(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你正在帮助判断两条课程任务是否重复。"
                        "请根据任务内容判断应该：保留旧的（keep_old）、"
                        "用新的替换旧的（replace）、还是两条都保留（keep_both）。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "existing_task": {"id": best_match["id"], "title": best_match["title"]},
                        "new_task": candidate,
                    }, ensure_ascii=False),
                },
            ],
            output_schema=DEDUP_SCHEMA,
            request_id=f"dedup_{course_id}_{now_iso()}",
            provider_id=provider_id,
        )
        dedup_response = provider.call(decision_request)
        decision = dedup_response.result.get("decision", "keep_old")
        reasoning = dedup_response.result.get("reasoning", "")
    except ProviderError:
        # AI 决策失败，保守策略：插入
        return "insert", None, None

    resolution = {
        "existing_task_id": best_match["id"],
        "existing_title": best_match["title"],
        "new_title": title,
        "decision": decision,
        "reasoning": reasoning,
    }

    if decision == "replace":
        return "update", best_match["id"], resolution
    elif decision == "keep_both":
        return "insert", None, resolution
    else:
        return "skip", None, resolution


def _fail(error_code: str, started_at: str, conn, course_id: str, msg: str = "") -> dict:
    """
    构造失败返回值并写入 sync_log。

    Args:
        error_code:  失败原因标识。
        started_at:  同步开始时间。
        conn:        SQLite 连接。
        course_id:   课程 id。
        msg:         附加错误信息。

    Returns:
        标准失败返回 dict。
    """
    sync_log_id = _write_sync_log(
        conn, course_id, None, "failed", error_code + (f": {msg}" if msg else ""),
        {"added": 0, "updated": 0, "skipped": 0, "ai_resolved": 0}, [], started_at
    )
    return {
        "status": "failed",
        "tasks_added": 0, "tasks_updated": 0,
        "tasks_skipped": 0, "tasks_ai_resolved": 0,
        "error_message": error_code + (f": {msg}" if msg else ""),
        "sync_log_id": sync_log_id,
    }


def _write_sync_log(
    conn, course_id, provider_id, status, error_message,
    counts, ai_resolution_log, started_at
) -> int:
    """
    写入一条 sync_log 记录，返回插入的 id。

    Args:
        conn:              SQLite 连接。
        course_id:         课程 id。
        provider_id:       使用的 provider。
        status:            success / failed / partial。
        error_message:     失败时的错误描述。
        counts:            added / updated / skipped / ai_resolved 计数 dict。
        ai_resolution_log: AI 三选一决策记录列表。
        started_at:        同步开始时间 ISO 字符串。

    Returns:
        sync_log.id
    """
    cursor = conn.execute("""
        INSERT INTO sync_log (
            course_id, sync_type, provider, status, error_message,
            tasks_added, tasks_updated, tasks_skipped, tasks_ai_resolved,
            ai_resolution_log, started_at, finished_at
        ) VALUES (?, 'syllabus_parse', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        course_id, provider_id, status, error_message,
        counts["added"], counts["updated"], counts["skipped"], counts["ai_resolved"],
        json.dumps(ai_resolution_log, ensure_ascii=False) if ai_resolution_log else None,
        started_at, now_iso(),
    ))
    conn.commit()
    return cursor.lastrowid


import sqlite3  # noqa: E402 — 放在文件末尾避免循环依赖提示
