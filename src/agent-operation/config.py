import os
import json
from dotenv import load_dotenv

load_dotenv()


class Config:
    """
    agent-operation 的配置入口，所有配置从环境变量读取。
    通过 .env 文件或系统环境变量设置，见项目根目录的 .env.example。
    """

    DB_PATH: str = os.getenv("DB_PATH", "canvas.db")
    BUFFER_PATH: str = os.getenv("BUFFER_PATH", "buffer")
    STORAGE_PATH: str = os.getenv("STORAGE_PATH", "storage")
    ERROR_PATH: str = os.getenv("ERROR_PATH", "error")
    MANIFEST_PATH: str = os.getenv("MANIFEST_PATH", "file/manifest.json")
    DEFAULT_PROVIDER_ID: str = os.getenv("DEFAULT_PROVIDER_ID", "anthropic")

    @classmethod
    def load_provider_configs(cls) -> dict:
        """
        从环境变量加载所有已配置的 provider。
        只有 api_key 和 model 均已设置的 provider 才会被加载。

        环境变量命名规则：
            {PROVIDER_ID}_API_KEY   → api_key
            {PROVIDER_ID}_MODEL     → model

        Returns:
            dict，格式为 {provider_id: {"api_key": str, "model": str}}。
            未配置的 provider 不出现在返回值中。
        """
        providers = {}
        for pid in ["anthropic", "openai", "deepseek", "kimi"]:
            api_key = os.getenv(f"{pid.upper()}_API_KEY", "")
            model = os.getenv(f"{pid.upper()}_MODEL", "")
            if api_key and model:
                providers[pid] = {"api_key": api_key, "model": model}
        return providers
