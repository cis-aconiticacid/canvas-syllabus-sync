import sqlite3
import os
import json
import requests
from canvasapi import Canvas
from utils import calc_week_number, now_iso


def _load_manifest(manifest_path: str) -> list:
    """
    读取 manifest.json，返回条目列表。文件不存在时返回空列表。

    Args:
        manifest_path: manifest.json 的文件路径。

    Returns:
        manifest 条目列表。
    """
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path, "r") as f:
        return json.load(f)


def _save_manifest(manifest_path: str, manifest: list) -> None:
    """
    将 manifest 列表写回 manifest.json。

    Args:
        manifest_path: manifest.json 的文件路径。
        manifest:      要写入的条目列表。
    """
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


def sync_files(
    canvas: Canvas,
    conn: sqlite3.Connection,
    buffer_path: str,
    manifest_path: str,
    week_config: dict,
    api_token: str
) -> dict:
    """
    从 Canvas 拉取所有课程文件，增量下载到 buffer，并追加到 manifest.json。

    增量判断：比对 canvas_updated_at + file_size，两者均一致则跳过。
    已在 manifest 中（上次下载但 agent-operation 尚未处理）的文件也跳过。
    files 表的正式写入由 agent-operation 的 insert_file_record() 完成。

    Args:
        canvas:        已初始化的 canvasapi Canvas 对象。
        conn:          已打开的 SQLite 连接。
        buffer_path:   文件下载的暂存目录。
        manifest_path: manifest.json 的文件路径。
        week_config:   week 配置字典，来自 file/week.config。
        api_token:     Canvas API token，用于文件下载请求的鉴权。

    Returns:
        dict，包含 downloaded / skipped / failed 计数。
    """
    cursor = conn.cursor()
    counts = {"downloaded": 0, "skipped": 0, "failed": 0}

    manifest = _load_manifest(manifest_path)
    manifest_ids = {entry["canvas_file_id"] for entry in manifest}

    courses = cursor.execute("SELECT id, course_code, term_id FROM courses").fetchall()
    os.makedirs(buffer_path, exist_ok=True)

    for course_row in courses:
        course_id = course_row["id"]
        course_code = course_row["course_code"]
        term_id = course_row["term_id"]
        term_week_config = week_config.get(term_id)

        try:
            canvas_course = canvas.get_course(course_id)
            # 学生账号无权访问 files API，改从 modules 里拉文件
            files = []
            try:
                modules = canvas_course.get_modules()
                for module in modules:
                    try:
                        items = module.get_module_items()
                        for item in items:
                            if getattr(item, "type", "") == "File":
                                files.append(item)
                    except Exception:
                        continue
            except Exception:
                continue
        except Exception:
            continue

        for f in files:
            canvas_file_id = str(getattr(f, "content_id", None) or getattr(f, "id", ""))
            canvas_updated_at = getattr(f, "updated_at", None)
            file_size = getattr(f, "file_size", None) or getattr(f, "size", None)
            filename = getattr(f, "title", None) or getattr(f, "display_name", str(f.id))
            published_at = getattr(f, "created_at", None)
            download_url = getattr(f, "url", None)

            if not canvas_file_id:
                counts["failed"] += 1
                continue

            existing = cursor.execute(
                "SELECT canvas_updated_at, file_size FROM files WHERE canvas_file_id = ?",
                (canvas_file_id,)
            ).fetchone()

            if existing is not None:
                if (existing["canvas_updated_at"] == canvas_updated_at and
                        existing["file_size"] == file_size):
                    counts["skipped"] += 1
                    continue

            # 跳过已在 manifest 里的（上次下载但 agent-operation 还没处理）
            if canvas_file_id in manifest_ids:
                counts["skipped"] += 1
                continue

            # 下载到 buffer
            if not download_url:
                counts["failed"] += 1
                continue

            try:
                headers = {"Authorization": f"Bearer {api_token}"}
                response = requests.get(download_url, headers=headers, timeout=30)
                response.raise_for_status()

                dest = os.path.join(buffer_path, filename)
                with open(dest, "wb") as out:
                    out.write(response.content)
            except Exception:
                counts["failed"] += 1
                continue

            # 计算 week 编号
            week_number = None
            if term_week_config and published_at:
                week_number = calc_week_number(
                    published_at,
                    term_week_config["week1_start"],
                    term_week_config["total_weeks"]
                )

            # 追加到 manifest
            manifest.append({
                "canvas_file_id": canvas_file_id,
                "filename": filename,
                "buffer_path": dest,
                "course_code": course_code,
                "course_id": course_id,
                "term_id": term_id,
                "week_number": week_number,
                "published_at": published_at,
                "file_size": file_size,
                "canvas_updated_at": canvas_updated_at,
                "status": "pending"
            })
            counts["downloaded"] += 1

    _save_manifest(manifest_path, manifest)
    return counts
