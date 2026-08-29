# Provider 抽象层接口文档
version: 0.1

## 定位

Provider 层是解析层与各 AI 服务之间的唯一边界。它对上屏蔽所有 provider 差异、网络细节、以及防御性逻辑（重试、超时、限流），对上只暴露两件事：调用成功返回结构化结果，调用失败返回标准错误。

调用方（解析层、Cursor、其他 AI agent）不需要知道底层是哪个 provider，也不需要处理网络层错误。

---

## 支持的 Provider

| provider_id | 服务 | 备注 |
|-------------|------|------|
| `anthropic` | Claude API | 默认 |
| `deepseek` | DeepSeek API | 低成本备选 |
| `openai` | OpenAI API | |
| `ollama` | 本地 Ollama | 离线场景 |

provider 通过配置文件指定，运行时不hardcode。

---

## 配置

每个 provider 在配置文件中独立配置，结构如下：

```
provider_id         # 对应上表的 provider_id
api_key             # API 密钥，从环境变量读取
base_url            # API 入口，ollama 等本地服务需要自定义
model               # 使用的模型名称
timeout_seconds     # 单次请求超时时间，默认 60
max_retries         # 最大重试次数，默认 3
retry_backoff       # 重试等待基数（秒），指数退避，默认 2
rate_limit_rpm      # 每分钟最大请求数，0 表示不限制
```

---

## 核心接口

### `call(request) → response`

provider 层对外暴露的唯一调用入口。

**request 结构：**

```
provider_id         # 指定使用哪个 provider
messages            # 消息数组，由调用方构造，provider 层透传
                    # 格式：[{ role: "system"|"user"|"assistant", content: string }]
output_schema       # 期望的输出结构描述，provider 层用于构造 structured output 指令
                    # 格式见下方「output_schema 格式」
request_id          # 调用方传入的唯一标识，用于日志追踪
```

**response 结构：**

```
request_id          # 透传调用方传入的 request_id
provider_id         # 实际使用的 provider
model               # 实际使用的模型
result              # 结构化结果对象，字段由 output_schema 决定
usage               #
    prompt_tokens   # 输入 token 数
    output_tokens   # 输出 token 数
latency_ms          # 实际耗时（毫秒）
retries             # 实际重试次数
```

---

## output_schema 格式

调用方通过 `output_schema` 描述期望的输出结构，provider 层负责将其转换为对应 provider 的 structured output 指令（function calling / JSON mode 等），并将返回结果解析为结构化对象。

`output_schema` 采用 JSON Schema 子集描述，支持：

```
type                # "object" | "array" | "string" | "number" | "boolean"
properties          # type 为 object 时的字段定义
items               # type 为 array 时的元素定义
required            # 必填字段列表
description         # 字段说明，provider 层会将其注入 prompt 提示 AI
```

**示例——解析 syllabus 隐性任务的 output_schema：**

```json
{
  "type": "array",
  "items": {
    "type": "object",
    "required": ["title", "has_explicit_due", "confidence"],
    "properties": {
      "title":            { "type": "string",  "description": "任务标题" },
      "description":      { "type": "string",  "description": "任务描述" },
      "has_explicit_due": { "type": "boolean", "description": "是否有明确截止日" },
      "due_at_earliest":  { "type": "string",  "description": "截止区间起点 ISO 8601，无则 null" },
      "due_at_latest":    { "type": "string",  "description": "截止区间终点 ISO 8601，无则 null" },
      "is_recurring":     { "type": "boolean", "description": "是否周期性任务" },
      "recurrence_note":  { "type": "string",  "description": "周期性说明，非周期性为 null" },
      "confidence":       { "type": "number",  "description": "推断置信度 0.0–1.0" }
    }
  }
}
```

---

## 错误处理

provider 层内部消化所有网络层错误，对上只返回标准错误对象：

```
error_code          # 见下表
error_message       # 人类可读的错误描述
retries_attempted   # 已重试次数
provider_id         # 出错的 provider
request_id          # 透传调用方的 request_id
```

| error_code | 含义 | provider 层行为 |
|------------|------|----------------|
| `timeout` | 请求超时 | 重试至 max_retries，仍超时则返回此错误 |
| `rate_limited` | 触发限流 | 指数退避后重试，超出 max_retries 则返回此错误 |
| `auth_failed` | API 密钥无效 | 不重试，立即返回 |
| `invalid_response` | 返回内容无法解析为 output_schema | 不重试，返回原始内容供调用方排查 |
| `provider_error` | provider 服务端 5xx 错误 | 重试至 max_retries |
| `schema_mismatch` | output_schema 本身格式有误 | 不重试，立即返回 |

---

## 重试与退避策略

- 触发重试的条件：`timeout`、`rate_limited`、`provider_error`
- 不重试的条件：`auth_failed`、`invalid_response`、`schema_mismatch`
- 退避公式：`wait = retry_backoff ^ retry_count`（秒），即第1次等2s，第2次等4s，第3次等8s
- 达到 `max_retries` 后不再重试，返回最后一次的错误

---

## 日志约定

每次 `call()` 的结果由调用方写入 `sync_log`，provider 层只负责在 response 里返回足够的信息（token用量、延迟、重试次数），不自己写库。
