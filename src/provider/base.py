from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProviderRequest:
    """
    provider 层的统一调用入口结构。

    Attributes:
        messages:       消息数组，由调用方构造，格式为
                        [{"role": "system"|"user"|"assistant", "content": str}]
        output_schema:  期望的输出结构，JSON Schema 子集描述。
                        provider 层将其注入 system prompt，要求 AI 返回对应 JSON。
        request_id:     调用方传入的唯一标识，用于日志追踪，透传到 response。
        provider_id:    指定使用哪个 provider，由 factory 路由。
    """
    messages: list[dict]
    output_schema: dict
    request_id: str
    provider_id: str


@dataclass
class ProviderResponse:
    """
    provider 层的统一返回结构。

    Attributes:
        request_id:     透传自 ProviderRequest.request_id。
        provider_id:    实际使用的 provider。
        model:          实际使用的模型名称。
        result:         解析后的结构化结果，字段由 output_schema 决定。
        prompt_tokens:  输入 token 数。
        output_tokens:  输出 token 数。
        latency_ms:     实际耗时（毫秒）。
        retries:        实际重试次数。
    """
    request_id: str
    provider_id: str
    model: str
    result: Any
    prompt_tokens: int
    output_tokens: int
    latency_ms: int
    retries: int


@dataclass
class ProviderError(Exception):
    """
    provider 层的标准错误结构。所有网络层错误在 provider 内部消化后，
    以此结构对上层暴露。

    error_code 取值：
        timeout          请求超时，已重试至 max_retries
        rate_limited     触发限流，已重试至 max_retries
        auth_failed      API 密钥无效，不重试
        invalid_response 返回内容无法解析为 output_schema，不重试
        provider_error   服务端 5xx，已重试至 max_retries
        schema_mismatch  output_schema 本身格式有误，不重试

    Attributes:
        error_code:         见上方说明。
        error_message:      人类可读的错误描述。
        retries_attempted:  已重试次数。
        provider_id:        出错的 provider。
        request_id:         透传自 ProviderRequest.request_id。
    """
    error_code: str
    error_message: str
    retries_attempted: int
    provider_id: str
    request_id: str


def schema_to_prompt(schema: dict) -> str:
    """
    将 output_schema（JSON Schema 子集）转换为注入 system prompt 的文字描述，
    要求 AI 严格按此结构返回纯 JSON，不含 markdown 代码块或其他前缀。

    Args:
        schema: JSON Schema 子集描述，支持 object / array / 基本类型。

    Returns:
        格式化后的 prompt 字符串。
    """
    import json
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        "You must respond with ONLY valid JSON that matches the following schema. "
        "Do not include markdown, code blocks, or any text outside the JSON.\n\n"
        f"Schema:\n{schema_str}"
    )


class BaseProvider(ABC):
    """
    所有 provider 实现的抽象基类。

    子类需实现 _do_call()，负责实际的 HTTP 请求和响应解析。
    重试、退避、超时逻辑统一在 call() 中处理，子类不需要关心。
    """

    def __init__(self, config: dict):
        """
        Args:
            config: provider 配置字典，包含 api_key、base_url、model、
                    timeout_seconds、max_retries、retry_backoff、rate_limit_rpm。
        """
        self.provider_id: str = config["provider_id"]
        self.model: str = config["model"]
        self.timeout_seconds: int = config.get("timeout_seconds", 60)
        self.max_retries: int = config.get("max_retries", 3)
        self.retry_backoff: float = config.get("retry_backoff", 2.0)

    def call(self, request: ProviderRequest) -> ProviderResponse:
        """
        provider 层对外暴露的唯一调用入口。内部处理重试和退避，
        对上只返回成功的 ProviderResponse 或抛出 ProviderError。

        可重试的错误码：timeout、rate_limited、provider_error
        不可重试的错误码：auth_failed、invalid_response、schema_mismatch

        Args:
            request: ProviderRequest 实例。

        Returns:
            ProviderResponse 实例。

        Raises:
            ProviderError: 所有错误情况，含 error_code 和重试次数。
        """
        from .retry import call_with_retry
        return call_with_retry(self, request)

    @abstractmethod
    def _do_call(self, request: ProviderRequest) -> ProviderResponse:
        """
        实际执行单次 HTTP 请求，由子类实现。
        不处理重试，失败时直接抛出 ProviderError。

        Args:
            request: ProviderRequest 实例。

        Returns:
            ProviderResponse 实例。

        Raises:
            ProviderError: 单次调用失败时抛出。
        """
        ...
