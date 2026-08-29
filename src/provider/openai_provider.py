import json
import time
from openai import OpenAI, AuthenticationError, RateLimitError, APITimeoutError, APIStatusError
from .base import BaseProvider, ProviderRequest, ProviderResponse, ProviderError, schema_to_prompt
from .anthropic_provider import _inject_schema_instruction, _parse_json


class OpenAICompatibleProvider(BaseProvider):
    """
    OpenAI 兼容 API 的 provider 实现，支持 OpenAI、DeepSeek、Kimi 等。
    通过配置不同的 base_url 和 model 来切换服务商，代码逻辑完全共用。

    使用 prompt engineering 方式实现 structured output，
    将 output_schema 注入 system prompt，要求模型返回纯 JSON。
    """

    def __init__(self, config: dict):
        """
        Args:
            config: provider 配置字典，需包含 api_key、model，
                    可选 base_url（不填则使用 OpenAI 默认）、
                    timeout_seconds、max_retries、retry_backoff。
        """
        super().__init__(config)
        self._client = OpenAI(
            api_key=config["api_key"],
            base_url=config.get("base_url"),
            timeout=self.timeout_seconds,
        )

    def _do_call(self, request: ProviderRequest) -> ProviderResponse:
        """
        执行单次 OpenAI 兼容 API 调用。
        将 output_schema 转换为 prompt 并注入 system message，
        返回解析后的结构化结果。

        Args:
            request: ProviderRequest 实例。

        Returns:
            ProviderResponse 实例。

        Raises:
            ProviderError: API 调用失败或返回内容无法解析时抛出。
        """
        schema_instruction = schema_to_prompt(request.output_schema)
        messages = _inject_schema_instruction(request.messages, schema_instruction)

        start = time.time()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=4096,
            )
        except AuthenticationError as e:
            raise ProviderError(
                error_code="auth_failed",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except RateLimitError as e:
            raise ProviderError(
                error_code="rate_limited",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except APITimeoutError as e:
            raise ProviderError(
                error_code="timeout",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except APIStatusError as e:
            raise ProviderError(
                error_code="provider_error",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )

        latency_ms = int((time.time() - start) * 1000)
        raw_text = response.choices[0].message.content or ""

        result = _parse_json(raw_text)
        if result is None:
            raise ProviderError(
                error_code="invalid_response",
                error_message=f"Could not parse response as JSON: {raw_text[:200]}",
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )

        usage = response.usage
        return ProviderResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=self.model,
            result=result,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            retries=0,
        )
