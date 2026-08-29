"""
parse.py — 独立 syllabus 解析脚本

读取指定课程的 syllabus 原文，调用 Gemini API 提取隐性任务，
经去重逻辑后写入本地 SQLite 数据库。

用法：
    python src/parse.py --course CS544 --term 25F
    python src/parse.py --course CS544 --term 25F --force   # 强制重新解析
"""

import argparse
import json
import os
import sys
import uuid
import time
import sqlite3
from datetime import datetime
from urllib.request import url2pathname  # 引入这个工具
from dotenv import load_dotenv
from openai import OpenAI, AuthenticationError, RateLimitError, APITimeoutError, APIStatusError

load_dotenv()

# ── 配置 ──────────────────────────────────────────────────────────────────────

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_BASE_URL  = "https://generativelanguage.googleapis.com/v1beta/openai/"
DB_PATH          = os.getenv("DB_PATH", "canvas.db")
MAX_RETRIES      = 3
RETRY_BACKOFF    = 2.0

# ── output_schema ─────────────────────────────────────────────────────────────

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

# ── Gemini 调用 ───────────────────────────────────────────────────────────────

def _make_client() -> OpenAI:
    """
    初始化 Gemini OpenAI 兼容客户端。

    Returns:
        OpenAI 客户端实例，指向 Gemini base_url。

    Raises:
        SystemExit: GEMINI_API_KEY 未配置时退出。
    """
    if not GEMINI_API_KEY:
        print("[ERROR] GEMINI_API_KEY 未配置，请在 .env 里填入。")
        sys.exit(1)
    return OpenAI(api_key=GEMINI_API_KEY, base_url=GEMINI_BASE_URL)


def _call_gemini(client: OpenAI, messages: list[dict], schema: dict) -> any:
    """
    调用 Gemini API，返回解析后的结构化结果。
    内置指数退避重试，最多重试 MAX_RETRIES 次。

    Args:
        client:   OpenAI 兼容客户端。
        messages: 消息列表，含 system 和 user。
        schema:   output_schema，注入 system prompt 要求返回纯 JSON。

    Returns:
        解析后的 Python 对象（list 或 dict）。

    Raises:
        SystemExit: 超过最大重试次数或遇到不可重试错误时退出。
    """
    schema_instruction = (
        "你必须只返回符合以下 schema 的纯 JSON，不要包含 markdown、代码块或任何其他文字。\n\n"
        f"Schema:\n{json.dumps(schema, ensure_ascii=False, indent=2)}"
    )

    # 注入 schema 指令到 system message
    injected = list(messages)
    for i, msg in enumerate(injected):
        if msg["role"] == "system":
            injected[i] = {"role": "system", "content": msg["content"] + "\n\n" + schema_instruction}
            break
    else:
        injected.insert(0, {"role": "system", "content": schema_instruction})

    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=GEMINI_MODEL,
                messages=injected,
                max_tokens=4096,
            )
            raw = response.choices[0].message.content or ""
            result = _parse_json(raw)
            if result is None:
                print(f"[WARN] 返回内容无法解析为 JSON，重试第 {attempt + 1} 次...")
                last_error = "invalid_response"
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            return result

        except AuthenticationError as e:
            print(f"[ERROR] API key 无效：{e}")
            sys.exit(1)
        except (RateLimitError, APITimeoutError, APIStatusError) as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF ** attempt
                print(f"[WARN] 请求失败，{wait:.0f}s 后重试（{attempt + 1}/{MAX_RETRIES}）：{e}")
                time.sleep(wait)
            continue

    print(f"[ERROR] 超过最大重试次数，最后错误：{last_error}")
    sys.exit(1)


def _parse_json(text: str):
    """
    将字符串解析为 JSON 对象，自动去除 markdown 代码块标记。

    Args:
        text: 模型返回的原始文本。

    Returns:
        解析成功返回 Python 对象，失败返回 None。
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None

# ── 数据库操作 ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """
    返回 SQLite 连接，启用外键约束和 Row factory。

    Returns:
        sqlite3.Connection
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now() -> str:
    return datetime.utcnow().isoformat()


def _get_course(conn: sqlite3.Connection, course_code: str, term_id: str) -> sqlite3.Row | None:
    """
    查询课程信息。

    Args:
        conn:        SQLite 连接。
        course_code: 课程代码，如 CS544。
        term_id:     学期 id，如 25F。

    Returns:
        courses 表的行，找不到返回 None。
    """
    return conn.execute(
        "SELECT id, course_code, name, syllabus_ref, syllabus_parsed_at FROM courses "
        "WHERE course_code = ? AND term_id = ?",
        (course_code, term_id)
    ).fetchone()

# ── 去重逻辑 ──────────────────────────────────────────────────────────────────

def _dedup(
    candidate: dict,
    course_id: str,
    conn: sqlite3.Connection,
    client: OpenAI,
) -> tuple[str, str | None]:
    """
    对单条候选任务执行去重逻辑。

    流程：
    1. has_explicit_due = True → 查 ±5 天内时间区间重叠的已有任务
       has_explicit_due = False → 跳过日期匹配，直接走 title 检查
    2. 候选集为空 → insert
    3. 候选集非空，title 重叠词 < 2 → insert
    4. 重叠词 ≥ 2 → Gemini 三选一决策

    Args:
        candidate: AI 提取的候选任务 dict。
        course_id: 当前课程 id。
        conn:      SQLite 连接。
        client:    Gemini 客户端，用于三选一决策。

    Returns:
        (action, existing_id)
        action:      "insert" | "update" | "skip"
        existing_id: replace 时的旧任务 id，其他为 None
    """
    title = candidate.get("title", "")
    has_explicit_due = candidate.get("has_explicit_due", False)
    earliest = candidate.get("due_at_earliest")
    latest = candidate.get("due_at_latest")

    if has_explicit_due and earliest and latest:
        existing_rows = conn.execute("""
            SELECT id, title FROM tasks
            WHERE course_id = ?
              AND source_type = 'ai_inferred'
              AND due_at_earliest <= date(?, '+5 days')
              AND due_at_latest   >= date(?, '-5 days')
        """, (course_id, latest, earliest)).fetchall()
    else:
        existing_rows = conn.execute("""
            SELECT id, title FROM tasks
            WHERE course_id = ? AND source_type = 'ai_inferred'
        """, (course_id,)).fetchall()

    if not existing_rows:
        return "insert", None

    new_words = set(title.lower().split())
    best_match = None
    best_overlap = 0
    for row in existing_rows:
        overlap = len(new_words & set(row["title"].lower().split()))
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = row

    if best_overlap < 2:
        return "insert", None

    # Gemini 三选一决策
    print(f"  [DEDUP] '{title}' 与已有任务 '{best_match['title']}' 重叠，交由 Gemini 决策...")
    try:
        result = _call_gemini(client, [
            {
                "role": "system",
                "content": (
                    "你正在判断两条课程任务是否重复。"
                    "请根据内容判断：保留旧的（keep_old）、用新的替换（replace）、还是两条都保留（keep_both）。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "existing_task": {"id": best_match["id"], "title": best_match["title"]},
                    "new_task": candidate,
                }, ensure_ascii=False),
            },
        ], DEDUP_SCHEMA)

        decision = result.get("decision", "keep_old")
        reasoning = result.get("reasoning", "")
        print(f"  [DEDUP] 决策：{decision}（{reasoning}）")

        if decision == "replace":
            return "update", best_match["id"]
        elif decision == "keep_both":
            return "insert", None
        else:
            return "skip", None

    except Exception:
        # 决策失败，保守策略：插入
        return "insert", None

# ── 主流程 ────────────────────────────────────────────────────────────────────

def parse(course_code: str, term_id: str, force: bool = False) -> None:
    """
    解析指定课程的 syllabus，提取隐性任务写入数据库。
    完整流程：前置检查 → 读 syllabus → Gemini 提取 → 逐条去重 → 批量写库。

    Args:
        course_code: 课程代码，如 CS544。
        term_id:     学期 id，如 25F。
        force:       True 时忽略 syllabus_parsed_at，强制重新解析。
    """
    print(f"\n[PARSE] {course_code} / {term_id}")
    client = _make_client()
    conn = _get_conn()

    try:
        # Step 1 — 前置检查
        course = _get_course(conn, course_code, term_id)
        if course is None:
            print(f"[ERROR] 课程 {course_code} / {term_id} 在数据库中不存在。请先跑 canvas-sync。")
            return

        if not course["syllabus_ref"]:
            print(f"[ERROR] 课程 {course_code} 没有 syllabus_ref，可能 Canvas 上没有上传 syllabus。")
            return

        if not force and course["syllabus_parsed_at"]:
            print(f"[SKIP] 已于 {course['syllabus_parsed_at']} 解析过，跳过。使用 --force 强制重新解析。")
            return

        # Step 2 — 读取 syllabus 原文
        # url2pathname 会把 file:///C:/path 转换为 C:\path
        # 注意：它通常处理 file:/// 或 file:// 协议头
        raw_url = course["syllabus_ref"]
        if raw_url.startswith("file:///"):
            syllabus_path = url2pathname(raw_url[8:]) # 去掉协议头后转换
        else:
            syllabus_path = raw_url

        if not os.path.exists(syllabus_path):
            print(f"[ERROR] syllabus 文件不存在：{syllabus_path}")
            return

        with open(syllabus_path, "r", encoding="utf-8") as f:
            syllabus_body = f.read()

        print(f"[INFO] syllabus 读取成功（{len(syllabus_body)} 字符），开始提取...")

        # Step 3 — Gemini 提取隐性任务
        candidates = _call_gemini(client, [
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
        ], INFERRED_TASK_SCHEMA)

        print(f"[INFO] Gemini 提取到 {len(candidates)} 条候选任务，开始去重...")

        # Step 4 & 5 — 逐条去重 + 批量写库
        counts = {"added": 0, "updated": 0, "skipped": 0}
        course_id = course["id"]

        with conn:
            for candidate in candidates:
                action, existing_id = _dedup(candidate, course_id, conn, client)

                if action == "insert":
                    conn.execute("""
                        INSERT INTO tasks (
                            id, course_id, title, description,
                            has_explicit_due, due_at_earliest, due_at_latest,
                            source_type, source_document,
                            confidence, is_recurring, recurrence_note, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ai_inferred', ?, ?, ?, ?, 'pending')
                    """, (
                        str(uuid.uuid4()), course_id,
                        candidate.get("title"), candidate.get("description"),
                        1 if candidate.get("has_explicit_due") else 0,
                        candidate.get("due_at_earliest"), candidate.get("due_at_latest"),
                        course["syllabus_ref"],
                        candidate.get("confidence"),
                        1 if candidate.get("is_recurring") else 0,
                        candidate.get("recurrence_note"),
                    ))
                    counts["added"] += 1

                elif action == "update":
                    conn.execute("""
                        UPDATE tasks SET
                            title=?, description=?, has_explicit_due=?,
                            due_at_earliest=?, due_at_latest=?,
                            confidence=?, is_recurring=?, recurrence_note=?,
                            updated_at=?
                        WHERE id=?
                    """, (
                        candidate.get("title"), candidate.get("description"),
                        1 if candidate.get("has_explicit_due") else 0,
                        candidate.get("due_at_earliest"), candidate.get("due_at_latest"),
                        candidate.get("confidence"),
                        1 if candidate.get("is_recurring") else 0,
                        candidate.get("recurrence_note"),
                        _now(), existing_id,
                    ))
                    counts["updated"] += 1

                else:
                    counts["skipped"] += 1

        # Step 6 — 更新 syllabus_parsed_at
        conn.execute(
            "UPDATE courses SET syllabus_parsed_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), course_id)
        )
        conn.commit()

        print(f"\n[DONE] 新增 {counts['added']} 条，更新 {counts['updated']} 条，跳过 {counts['skipped']} 条。")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="解析 syllabus，提取隐性任务写入数据库")
    parser.add_argument("--course", required=True, help="课程代码，如 CS544")
    parser.add_argument("--term",   required=True, help="学期 id，如 25F")
    parser.add_argument("--force",  action="store_true", help="强制重新解析（忽略已解析标记）")
    args = parser.parse_args()

    parse(args.course, args.term, args.force)
