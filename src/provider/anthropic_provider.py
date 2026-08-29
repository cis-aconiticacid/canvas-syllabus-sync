import json
import time
import anthropic
from .base import BaseProvider, ProviderRequest, ProviderResponse, ProviderError, schema_to_prompt


class AnthropicProvider(BaseProvider):
    """
    Anthropic Claude API 的 provider 实现。
    使用 prompt engineering 方式实现 structured output，
    将 output_schema 注入 system prompt，要求模型返回纯 JSON。
    """

    def __init__(self, config: dict):
        """
        Args:
            config: provider 配置字典，需包含 api_key、model，
                    可选 timeout_seconds、max_retries、retry_backoff。
        """
        super().__init__(config)
        self._client = anthropic.Anthropic(api_key=config["api_key"])

    def _do_call(self, request: ProviderRequest) -> ProviderResponse:
        """
        执行单次 Anthropic API 调用。
        将 output_schema 转换为 prompt 并注入 system message，
        返回解析后的结构化结果。

        Args:
            request: ProviderRequest 实例。

        Returns:
            ProviderResponse 实例。

        Raises:
            ProviderError: API 调用失败或返回内容无法解析时抛出。
        """
        # 将 output_schema 注入 system message
        schema_instruction = schema_to_prompt(request.output_schema)
        messages = _inject_schema_instruction(request.messages, schema_instruction)

        # 分离 system message（Anthropic API 单独传 system 参数）
        system_prompt, user_messages = _split_system(messages)

        start = time.time()
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system_prompt,
                messages=user_messages,
                timeout=self.timeout_seconds,
            )
        except anthropic.AuthenticationError as e:
            raise ProviderError(
                error_code="auth_failed",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except anthropic.RateLimitError as e:
            raise ProviderError(
                error_code="rate_limited",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except anthropic.APITimeoutError as e:
            raise ProviderError(
                error_code="timeout",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )
        except anthropic.APIStatusError as e:
            raise ProviderError(
                error_code="provider_error",
                error_message=str(e),
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )

        latency_ms = int((time.time() - start) * 1000)
        raw_text = response.content[0].text

        result = _parse_json(raw_text)
        if result is None:
            raise ProviderError(
                error_code="invalid_response",
                error_message=f"Could not parse response as JSON: {raw_text[:200]}",
                retries_attempted=0,
                provider_id=self.provider_id,
                request_id=request.request_id,
            )

        return ProviderResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            model=self.model,
            result=result,
            prompt_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            retries=0,
        )


def _inject_schema_instruction(messages: list[dict], instruction: str) -> list[dict]:
    """
    将 schema 说明注入到消息列表的 system message 末尾。
    如果没有 system message，则新建一条。

    Args:
        messages:    原始消息列表。
        instruction: schema_to_prompt() 生成的指令字符串。

    Returns:
        注入后的消息列表（不修改原列表）。
    """
    messages = list(messages)
    for i, msg in enumerate(messages):
        if msg["role"] == "system":
            messages[i] = {
                "role": "system",
                "content": msg["content"] + "\n\n" + instruction,
            }
            return messages
    messages.insert(0, {"role": "system", "content": instruction})
    return messages


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    将消息列表中的 system message 分离出来，
    返回 (system_prompt, 其余消息列表)。
    Anthropic API 要求 system 单独作为参数传入。

    Args:
        messages: 含 system message 的消息列表。

    Returns:
        (system_prompt_str, non_system_messages)
    """
    system_parts = []
    other = []
    for msg in messages:
        if msg["role"] == "system":
            system_parts.append(msg["content"])
        else:
            other.append(msg)
    return "\n\n".join(system_parts), other


def _parse_json(text: str):
    """
    尝试将字符串解析为 JSON 对象。
    会先去掉可能残留的 markdown 代码块标记。

    Args:
        text: 模型返回的原始文本。

    Returns:
        解析成功返回 Python 对象，失败返回 None。
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None
