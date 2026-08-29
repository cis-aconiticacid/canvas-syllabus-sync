import time
from .base import BaseProvider, ProviderRequest, ProviderResponse, ProviderError

RETRYABLE_CODES = {"timeout", "rate_limited", "provider_error"}


def call_with_retry(provider: BaseProvider, request: ProviderRequest) -> ProviderResponse:
    """
    对 provider._do_call() 执行带指数退避的重试逻辑。

    重试策略：
    - 可重试的 error_code（timeout / rate_limited / provider_error）：
      等待 retry_backoff ^ retry_count 秒后重试，最多 max_retries 次。
    - 不可重试的 error_code：立即重新抛出，不消耗重试次数。
    - 达到 max_retries 后仍失败：抛出最后一次的 ProviderError，
      retries_attempted 更新为实际重试次数。

    Args:
        provider: BaseProvider 子类实例。
        request:  ProviderRequest 实例。

    Returns:
        ProviderResponse 实例（首次或重试成功时）。

    Raises:
        ProviderError: 不可重试的错误，或超过 max_retries 后仍失败。
    """
    last_error: ProviderError | None = None

    for attempt in range(provider.max_retries + 1):
        try:
            response = provider._do_call(request)
            # 成功时把实际重试次数写回 response
            response.retries = attempt
            return response
        except ProviderError as e:
            if e.error_code not in RETRYABLE_CODES:
                raise

            last_error = e
            if attempt < provider.max_retries:
                wait = provider.retry_backoff ** attempt
                time.sleep(wait)

    # 超出重试次数，抛出最后一次错误并更新重试计数
    last_error.retries_attempted = provider.max_retries
    raise last_error
