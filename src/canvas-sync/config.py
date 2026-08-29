import os
import json
from dotenv import load_dotenv

load_dotenv()


class Config:
    CANVAS_API_TOKEN: str = os.getenv("CANVAS_API_TOKEN", "")
    CANVAS_BASE_URL: str = os.getenv("CANVAS_BASE_URL", "")
    DB_PATH: str = os.getenv("DB_PATH", "canvas.db")
    BUFFER_PATH: str = os.getenv("BUFFER_PATH", "buffer")
    WEEK_CONFIG_PATH: str = os.getenv("WEEK_CONFIG_PATH", "file/week.config")
    SYNC_INTERVAL_MINUTES: int = int(os.getenv("SYNC_INTERVAL_MINUTES", "0"))

    @classmethod
    def validate(cls) -> None:
        if not cls.CANVAS_API_TOKEN:
            raise ValueError("CANVAS_API_TOKEN is not set")
        if not cls.CANVAS_BASE_URL:
            raise ValueError("CANVAS_BASE_URL is not set")

    @classmethod
    def load_week_config(cls) -> dict:
        with open(cls.WEEK_CONFIG_PATH, "r") as f:
            return json.load(f)
