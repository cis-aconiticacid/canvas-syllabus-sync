"""
agent-operation 入口。

将 agent-interface.md 中定义的所有操作暴露为可直接调用的 Python 函数。
AI agent（Cursor、Cowork 等）通过读取本文件了解可用操作，并直接调用。

所有操作分三类（见 agent-interface.md）：
    read        只读，不修改任何状态
    write       修改数据库或移动文件
    destructive 不可撤销
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from manifest import get_pending, load_manifest, remove_inserted
from file_ops import move_file as _move_file, move_file_to_error as _move_file_to_error, redirect_file as _redirect_file
from db_ops import (
    insert_file_record as _insert_file_record,
    update_task_status as _update_task_status,
    get_course_info as _get_course_info,
    get_week_range as _get_week_range,
)
from parse_ops import parse_course as _parse_course


# ── Read ──────────────────────────────────────────────────────────────────────

def get_manifest() -> list[dict]:
    """
    返回当前 buffer 区所有待处理文件的列表，含各条目的完整元信息和处理状态。

    Returns:
        manifest 条目列表，每条包含 canvas_file_id / filename / buffer_path /
        course_code / term_id / week_number / published_at / file_size / status。
    """
    return load_manifest(Config.MANIFEST_PATH)


def get_pending_files() -> list[dict]:
    """
    返回 manifest 中所有 status = pending 的条目。
    AI 处理文件时优先调此函数而非 get_manifest()。

    Returns:
        pending 条目列表，字段同 get_manifest()。
    """
    return get_pending(Config.MANIFEST_PATH)


def get_course_info(course_code: str, term_id: str) -> dict | None:
    """
    查询课程基本信息，用于处理文件前确认课程存在。

    Args:
        course_code: 课程代码，如 CS544。
        term_id:     学期 id，如 25F。

    Returns:
        包含 course_id / course_code / term_id / name /
        syllabus_ref / syllabus_parsed_at 的 dict；找不到返回 None。
    """
    return _get_course_info(course_code, term_id, Config.DB_PATH)


def get_week_range(course_code: str, term_id: str, week_number: int) -> dict | None:
    """
    查询指定 week 的日期区间。

    Args:
        course_code:  课程代码。
        term_id:      学期 id，如 25F。
        week_number:  week 编号，1 起始。

    Returns:
        包含 week_number / start_date / end_date 的 dict；找不到返回 None。
    """
    return _get_week_range(course_code, term_id, week_number, "file/week.config")


# ── Write ─────────────────────────────────────────────────────────────────────

def move_file(canvas_file_id: str, dest_path: str, rename_to: str = None) -> dict:
    """
    将文件从 buffer 移动到正式存储位置，原子性更新 manifest status 为 moved。
    AI 可通过 rename_to 在移动时指定新文件名。

    dest_path 格式：
        {course_code}/{term_id}/week{N}
        {course_code}/{term_id}/general

    Args:
        canvas_file_id: Canvas 文件 id。
        dest_path:      相对于 storage 根目录的目标目录路径（不含文件名）。
        rename_to:      可选，指定新文件名。不填则保留原文件名。

    Returns:
        dict，包含 success (bool) / final_path (str) / error_code (str)。
        error_code 取值：dest_exists / source_not_found / io_error
    """
    return _move_file(
        canvas_file_id, dest_path,
        Config.MANIFEST_PATH, Config.STORAGE_PATH,
        rename_to=rename_to,
    )


def insert_file_record(canvas_file_id: str, file_path: str) -> dict:
    """
    将文件信息写入 files 表，原子性更新 manifest status 为 inserted。
    应在 move_file() 成功后调用，file_path 传入 move_file() 返回的 final_path。

    Args:
        canvas_file_id: Canvas 文件 id。
        file_path:      文件在正式存储区的完整路径。

    Returns:
        dict，包含 success (bool) / file_id (int) / error_code (str)。
        error_code 取值：already_exists / course_not_found / db_error
    """
    return _insert_file_record(
        canvas_file_id, file_path,
        Config.MANIFEST_PATH, Config.DB_PATH
    )


def parse_course(course_id: str, term_id: str, provider_id: str = None) -> dict:
    """
    触发指定课程的 syllabus 解析，提取隐性任务写入 tasks 表。

    Args:
        course_id:   Canvas course_id，对应 courses.id。
        term_id:     学期 id，如 25F。
        provider_id: 使用的 AI provider；不填则使用配置默认值。

    Returns:
        dict，包含 status / tasks_added / tasks_updated / tasks_skipped /
        tasks_ai_resolved / error_message / sync_log_id。
        status 取值：success / failed / partial / skipped
    """
    pid = provider_id or Config.DEFAULT_PROVIDER_ID
    provider_configs = Config.load_provider_configs()
    return _parse_course(course_id, term_id, pid, provider_configs, Config.DB_PATH)


def update_task_status(task_id: str, status: str) -> dict:
    """
    更新任务状态。

    Args:
        task_id: tasks.id。
        status:  新状态，pending / completed / dismissed。

    Returns:
        dict，包含 success (bool) / error_code (str)。
    """
    return _update_task_status(task_id, status, Config.DB_PATH)


def redirect_file(canvas_file_id: str, new_path: str) -> dict:
    """
    将数据库中指定文件的 file_path 更新为新路径。
    仅做数据库同步，不移动实际文件。调用前文件必须已在新路径上。
    供人工操作使用。

    Args:
        canvas_file_id: Canvas 文件 id。
        new_path:       文件的新完整路径，必须实际存在。

    Returns:
        dict，包含 success (bool) / error_code (str)。
        error_code 取值：file_not_found / db_error / not_in_db
    """
    return _redirect_file(
        canvas_file_id, new_path,
        Config.MANIFEST_PATH, Config.DB_PATH,
    )


# ── Destructive ───────────────────────────────────────────────────────────────

def move_file_to_error(canvas_file_id: str, reason: str) -> dict:
    """
    将文件移动到 error 目录，manifest status 更新为 error。
    不可撤销。所有文件处理失败的情况最终都调此函数。

    Args:
        canvas_file_id: Canvas 文件 id。
        reason:         失败原因，写入 manifest 和 files 表的 error_reason 字段。

    Returns:
        dict，包含 success (bool) / error_path (str)。
    """
    return _move_file_to_error(
        canvas_file_id, reason,
        Config.MANIFEST_PATH, Config.STORAGE_PATH, Config.ERROR_PATH
    )


def clear_inserted_from_manifest() -> dict:
    """
    从 manifest 中清除所有 status = inserted 的条目。
    通常在一批文件全部处理完后调用。不可撤销。

    Returns:
        dict，包含 cleared_count (int)。
    """
    count = remove_inserted(Config.MANIFEST_PATH)
    return {"cleared_count": count}
