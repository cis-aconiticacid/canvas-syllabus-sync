import os
import shutil
from . import manifest as mf


def move_file(
    canvas_file_id: str,
    dest_path: str,
    manifest_path: str,
    storage_root: str,
    rename_to: str = None,
) -> dict:
    """
    将文件从 buffer 移动到正式存储位置，并原子性地更新 manifest status 为 moved。
    AI 可通过 rename_to 在移动时指定新文件名。

    dest_path 格式：
        {course_code}/{term_id}/week{N}/{filename}
        {course_code}/{term_id}/general/{filename}

    注意：文件移动和 manifest 更新是两步操作，非数据库事务。
    若文件移动成功但 manifest 更新失败，status 保持 pending，
    下次运行时会因 source_not_found（文件已不在 buffer）进入错误流程。

    Args:
        canvas_file_id: Canvas 文件 id，用于查找 manifest 条目。
        dest_path:      相对于 storage_root 的目标目录路径（不含文件名）。
        manifest_path:  manifest.json 的文件路径。
        storage_root:   正式存储区根目录。
        rename_to:      可选，指定新文件名。不填则保留原文件名。

    Returns:
        dict，包含：
            success (bool)
            final_path (str)    成功时为完整目标路径，失败时为 None
            error_code (str)    失败时填写，取值：
                                dest_exists / source_not_found / io_error
    """
    entry = mf.find_entry(manifest_path, canvas_file_id)
    if entry is None:
        return {"success": False, "final_path": None, "error_code": "source_not_found"}

    src = entry["buffer_path"]
    if not os.path.exists(src):
        return {"success": False, "final_path": None, "error_code": "source_not_found"}

    filename = rename_to if rename_to else os.path.basename(src)
    final_path = os.path.join(storage_root, dest_path, filename)

    if os.path.exists(final_path):
        return {"success": False, "final_path": None, "error_code": "dest_exists"}

    try:
        os.makedirs(os.path.dirname(final_path), exist_ok=True)
        shutil.move(src, final_path)
    except OSError:
        return {"success": False, "final_path": None, "error_code": "io_error"}

    # 若重命名，同步更新 manifest 的 filename 字段
    if rename_to:
        entries = mf.load_manifest(manifest_path)
        for e in entries:
            if e["canvas_file_id"] == canvas_file_id:
                e["filename"] = rename_to
                break
        mf.save_manifest(manifest_path, entries)

    mf.update_status(manifest_path, canvas_file_id, "moved")
    return {"success": True, "final_path": final_path, "error_code": None}


def move_file_to_error(
    canvas_file_id: str,
    reason: str,
    manifest_path: str,
    storage_root: str,
    error_root: str,
) -> dict:
    """
    将文件移动到 error 目录，manifest status 更新为 error，记录失败原因。
    不可撤销操作，所有文件处理失败的情况最终都调此函数。

    文件可能在 buffer（status = pending）或正式存储位置（status = moved），
    两种情况都能正确处理。若文件在两处都找不到，仍会更新 manifest 状态。

    同名文件冲突时自动在文件名后追加 canvas_file_id 避免覆盖。

    Args:
        canvas_file_id: Canvas 文件 id。
        reason:         失败原因，写入 manifest 的 error_reason 字段。
        manifest_path:  manifest.json 的文件路径。
        storage_root:   正式存储区根目录，用于查找 status = moved 的文件。
        error_root:     error 目录根路径。

    Returns:
        dict，包含：
            success (bool)
            error_path (str)    文件在 error 目录的最终路径，失败时为 None
    """
    entry = mf.find_entry(manifest_path, canvas_file_id)
    if entry is None:
        return {"success": False, "error_path": None}

    if entry.get("status") == "moved" and entry.get("file_path"):
        src = os.path.join(storage_root, entry["file_path"])
    else:
        src = entry.get("buffer_path", "")

    if not src or not os.path.exists(src):
        _mark_error(manifest_path, canvas_file_id, reason)
        return {"success": False, "error_path": None}

    filename = os.path.basename(src)
    error_path = os.path.join(error_root, filename)
    if os.path.exists(error_path):
        base, ext = os.path.splitext(filename)
        error_path = os.path.join(error_root, f"{base}_{canvas_file_id}{ext}")

    try:
        os.makedirs(error_root, exist_ok=True)
        shutil.move(src, error_path)
    except OSError:
        return {"success": False, "error_path": None}

    _mark_error(manifest_path, canvas_file_id, reason)
    return {"success": True, "error_path": error_path}


def _mark_error(manifest_path: str, canvas_file_id: str, reason: str) -> None:
    """
    在 manifest 中将指定条目的 status 更新为 error，并记录失败原因。

    Args:
        manifest_path:  manifest.json 的文件路径。
        canvas_file_id: 要更新的文件 id。
        reason:         失败原因描述，写入 error_reason 字段。
    """
    entries = mf.load_manifest(manifest_path)
    for entry in entries:
        if entry["canvas_file_id"] == canvas_file_id:
            entry["status"] = "error"
            entry["error_reason"] = reason
            break
    mf.save_manifest(manifest_path, entries)


def redirect_file(
    canvas_file_id: str,
    new_path: str,
    manifest_path: str,
    db_path: str,
) -> dict:
    """
    将数据库中指定文件的 file_path 更新为新路径。
    仅做数据库同步，不移动实际文件。调用前文件必须已在新路径上。

    Args:
        canvas_file_id: Canvas 文件 id。
        new_path:       文件的新完整路径，必须实际存在。
        manifest_path:  manifest.json 的文件路径。
        db_path:        SQLite 文件路径。

    Returns:
        dict，包含：
            success (bool)
            error_code (str)    失败时填写，取值：
                                file_not_found / db_error / not_in_db
    """
    import sqlite3
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../canvas-sync"))
    from db import get_connection
    from utils import now_iso

    if not os.path.exists(new_path):
        return {"success": False, "error_code": "file_not_found"}

    conn = get_connection(db_path)
    try:
        result = conn.execute(
            "UPDATE files SET file_path = ?, updated_at = ? WHERE canvas_file_id = ?",
            (new_path, now_iso(), canvas_file_id)
        )
        conn.commit()
        if result.rowcount == 0:
            return {"success": False, "error_code": "not_in_db"}
    except sqlite3.Error:
        conn.rollback()
        return {"success": False, "error_code": "db_error"}
    finally:
        conn.close()

    # 同步更新 manifest 里的路径（如果还在 manifest 中）
    entries = mf.load_manifest(manifest_path)
    for entry in entries:
        if entry["canvas_file_id"] == canvas_file_id:
            entry["file_path"] = new_path
            break
    mf.save_manifest(manifest_path, entries)

    return {"success": True, "error_code": None}
