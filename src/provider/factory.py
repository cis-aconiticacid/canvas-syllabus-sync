from .base import BaseProvider
from .anthropic_provider import AnthropicProvider
from .openai_provider import OpenAICompatibleProvider

# provider_id → base_url 映射，OpenAI 兼容的服务商在这里注册
OPENAI_COMPATIBLE_URLS: dict[str, str] = {
    "openai":   None,                              # 使用 OpenAI SDK 默认值
    "deepseek": "https://api.deepseek.com/v1",
    "kimi":     "https://api.moonshot.cn/v1",
}


def get_provider(provider_id: str, all_configs: dict) -> BaseProvider:
    """
    根据 provider_id 和配置字典返回对应的 provider 实例。

    provider 配置从 all_configs[provider_id] 读取，需包含：
        api_key    必填
        model      必填
        以及其他可选字段（timeout_seconds、max_retries、retry_backoff）

    OpenAI 兼容的 provider（openai / deepseek / kimi）共用
    OpenAICompatibleProvider，base_url 由 OPENAI_COMPATIBLE_URLS 自动填入，
    无需在配置文件中手动指定。

    Args:
        provider_id:  provider 标识，如 "anthropic" / "openai" / "deepseek" / "kimi"。
        all_configs:  所有 provider 的配置字典，格式为 {provider_id: config_dict}。

    Returns:
        对应的 BaseProvider 子类实例。

    Raises:
        KeyError:   provider_id 在 all_configs 中不存在。
        ValueError: provider_id 不在已支持的列表中。
    """
    config = dict(all_configs[provider_id])
    config["provider_id"] = provider_id

    if provider_id == "anthropic":
        return AnthropicProvider(config)

    if provider_id in OPENAI_COMPATIBLE_URLS:
        base_url = OPENAI_COMPATIBLE_URLS[provider_id]
        if base_url:
            config.setdefault("base_url", base_url)
        return OpenAICompatibleProvider(config)

    raise ValueError(
        f"Unsupported provider_id: '{provider_id}'. "
        f"Supported: anthropic, {', '.join(OPENAI_COMPATIBLE_URLS)}"
    )
