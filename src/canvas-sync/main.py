import sys
import os
from datetime import datetime
from canvasapi import Canvas

# 把 src/canvas-sync 加入 path
sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from db import get_connection, init_db
from sync_terms import sync_terms
from sync_courses import sync_courses
from sync_tasks import sync_tasks
from sync_files import sync_files
from utils import now_iso


MANIFEST_PATH = "file/manifest.json"
SYLLABUS_STORE_ROOT = "file/storage"


def run_sync(sync_type: str = "full", course_id: str = None) -> None:
    """
    执行一次完整的 Canvas 同步，按顺序同步 terms → courses → tasks → files。

    Args:
        sync_type:  同步类型，"full" 或 "incremental"，写入 sync_log。
        course_id:  指定课程的 Canvas course_id，None 表示全量同步所有课程。
    """
    Config.validate()
    week_config = Config.load_week_config()

    init_db(Config.DB_PATH)
    conn = get_connection(Config.DB_PATH)
    canvas = Canvas(Config.CANVAS_BASE_URL, Config.CANVAS_API_TOKEN)

    started_at = now_iso()
    error_message = None
    status = "success"
    task_counts = {"added": 0, "updated": 0, "skipped": 0}
    file_counts = {"downloaded": 0, "skipped": 0, "failed": 0}

    try:
        print("[1/4] Syncing terms...")
        sync_terms(canvas, conn)

        print("[2/4] Syncing courses...")
        sync_courses(canvas, conn, SYLLABUS_STORE_ROOT)

        print("[3/4] Syncing tasks...")
        task_counts = sync_tasks(canvas, conn)

        print("[4/4] Syncing files...")
        file_counts = sync_files(
            canvas, conn,
            Config.BUFFER_PATH,
            MANIFEST_PATH,
            week_config,
            Config.CANVAS_API_TOKEN
        )

    except Exception as e:
        import traceback
        status = "failed"
        error_message = str(e)
        print(f"[ERROR] {e}")
        traceback.print_exc()

    finally:
        # 写 sync_log
        conn.execute("""
            INSERT INTO sync_log (
                course_id, sync_type, provider, status, error_message,
                tasks_added, tasks_updated, tasks_skipped,
                tasks_ai_resolved, started_at, finished_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 0, ?, ?)
        """, (
            course_id, sync_type, status, error_message,
            task_counts["added"], task_counts["updated"], task_counts["skipped"],
            started_at, now_iso()
        ))
        conn.commit()
        conn.close()

    print(f"\nDone. status={status}")
    print(f"  tasks: +{task_counts['added']} ~{task_counts['updated']} skip{task_counts['skipped']}")
    print(f"  files: dl{file_counts['downloaded']} skip{file_counts['skipped']} fail{file_counts['failed']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Canvas sync")
    parser.add_argument("--type", choices=["full", "incremental"], default="full")
    parser.add_argument("--course", default=None, help="Canvas course_id (optional)")
    args = parser.parse_args()
    run_sync(sync_type=args.type, course_id=args.course)