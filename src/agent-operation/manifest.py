import json
import os
from typing import Optional


def load_manifest(manifest_path: str) -> list[dict]:
    """
    读取 manifest.json，返回所有条目。文件不存在时返回空列表。

    Args:
        manifest_path: manifest.json 的文件路径。

    Returns:
        manifest 条目列表，每条包含 canvas_file_id、filename、status 等字段。
    """
    if not os.path.exists(manifest_path):
        return []
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest_path: str, manifest: list[dict]) -> None:
    """
    将 manifest 列表写回 manifest.json。

    Args:
        manifest_path: manifest.json 的文件路径。
        manifest:      要写入的条目列表。
    """
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def get_pending(manifest_path: str) -> list[dict]:
    """
    返回 manifest 中所有 status = pending 的条目。

    Args:
        manifest_path: manifest.json 的文件路径。

    Returns:
        status 为 pending 的条目列表。
    """
    return [e for e in load_manifest(manifest_path) if e.get("status") == "pending"]


def update_status(manifest_path: str, canvas_file_id: str, status: str) -> None:
    """
    更新 manifest 中指定条目的 status 字段，并写回文件。

    Args:
        manifest_path:  manifest.json 的文件路径。
        canvas_file_id: 要更新的文件的 Canvas file id。
        status:         新的状态值，pending / moved / inserted / error。
    """
    manifest = load_manifest(manifest_path)
    for entry in manifest:
        if entry["canvas_file_id"] == canvas_file_id:
            entry["status"] = status
            break
    save_manifest(manifest_path, manifest)


def remove_inserted(manifest_path: str) -> int:
    """
    从 manifest 中移除所有 status = inserted 的条目，并写回文件。

    Args:
        manifest_path: manifest.json 的文件路径。

    Returns:
        实际移除的条目数。
    """
    manifest = load_manifest(manifest_path)
    before = len(manifest)
    manifest = [e for e in manifest if e.get("status") != "inserted"]
    save_manifest(manifest_path, manifest)
    return before - len(manifest)


def find_entry(manifest_path: str, canvas_file_id: str) -> Optional[dict]:
    """
    在 manifest 中查找指定 canvas_file_id 的条目。

    Args:
        manifest_path:  manifest.json 的文件路径。
        canvas_file_id: 要查找的 Canvas file id。

    Returns:
        找到的条目 dict，找不到返回 None。
    """
    for entry in load_manifest(manifest_path):
        if entry["canvas_file_id"] == canvas_file_id:
            return entry
    return None
