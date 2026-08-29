# 解析层接口文档
version: 0.1

## 定位

解析层负责读取 syllabus 原文，通过 AI 提取隐性任务，经去重逻辑后写入 `tasks` 表。这是系统中唯一调用 AI 的业务流程入口。调用方可以是 Cursor、Claude Code、Cowork 或其他 AI agent。

---

## 核心接口

### `parse_course(request) → response`

解析指定课程的 syllabus，完整流程一次性执行。

**request 结构：**

```
course_id               # 必填，对应 courses.id
term_id                 # 必填，如 25F，用于定位 syllabus 文件
provider_id             # 使用哪个 AI provider，不填则使用配置文件默认值
force_reparse           # 是否强制重新解析（忽略 syllabus_parsed_at），默认 false
```

**response 结构：**

```
course_id
term_id
provider_id             # 实际使用的 provider
status                  # success | failed | partial
tasks_added
tasks_updated
tasks_skipped
tasks_ai_resolved
error_message           # 失败时填写
sync_log_id             # 本次解析对应的 sync_log.id
```

---

## 内部执行流程

```
1. 前置检查
2. 读取 syllabus 原文
3. AI 提取隐性任务
4. 逐条去重
5. 批量写库
6. 更新 courses 和 sync_log
```

以下按步骤说明。

---

### Step 1 — 前置检查

按顺序检查，任一失败则终止并返回 `failed`：

- `course_id` 在 `courses` 表中存在
- `courses.syllabus_ref` 不为空
- `force_reparse = false` 时，检查 `syllabus_parsed_at` 是否为空；不为空说明已解析过，跳过并返回 `status = skipped`
- syllabus 文件在 `syllabus_ref` 指向的地址实际存在

---

### Step 2 — 读取 syllabus 原文

从 `courses.syllabus_ref` 读取文件内容。

- 本地文件（`file://`）：直接读取
- 对象存储（`s3://`）：通过配置的存储客户端读取
- 读取失败 → 终止，返回 `failed`

---

### Step 3 — AI 提取隐性任务

调用 provider 层 `call()`，构造方式如下：

**system prompt 包含：**
- 当前课程的 `course_code` 和 `name`
- 任务提取目标说明：找出 syllabus 中没有对应 Canvas assignment 的隐性工作要求
- 输出格式要求（由 `output_schema` 驱动，见 provider 接口文档）

**user prompt 包含：**
- syllabus 原文全文

**output_schema：** 见 provider 接口文档中的示例

**返回结果：** 结构化的候选任务数组，每条包含：

```
title
description
has_explicit_due
due_at_earliest
due_at_latest
is_recurring
recurrence_note
confidence
```

---

### Step 4 — 逐条去重

对 Step 3 返回的每条候选任务，按以下逻辑处理：

**前置条件：** `course_code` 匹配。不匹配则静默跳过，不计入任何计数。

**主流程：**

```
查同 course_id 下已有的 ai_inferred 任务

if 候选任务 has_explicit_due = 1:
    查 due_at_earliest/due_at_latest 区间与候选任务重叠且在 ±5 天内的已有任务
else:
    跳过日期匹配，直接进入 title 检查

if 无候选集:
    → 标记为「插入」

if 有候选集:
    计算 title 重叠词数量
    if 重叠词 < 2:
        → 标记为「插入」
    if 重叠词 ≥ 2:
        → 调用 AI 三选一决策（见下方）
```

**AI 三选一决策：**

调用 provider 层 `call()`，传入新任务和候选任务的完整字段，要求 AI 返回以下结构：

```
decision                # keep_old | replace | keep_both
reasoning               # 决策理由，写入 ai_resolution_log
```

| decision | 执行动作 |
|----------|----------|
| `keep_old` | 丢弃新任务，计入 `tasks_skipped` |
| `replace` | 用新任务覆盖旧任务，计入 `tasks_updated` |
| `keep_both` | 保留旧任务，插入新任务，计入 `tasks_added` |

每次 AI 决策的输入和结果追加到本次 `sync_log.ai_resolution_log`（JSON 数组）。

---

### Step 5 — 批量写库

Step 4 标记完成后，统一执行数据库操作：

- 「插入」→ INSERT INTO tasks，`source_type = ai_inferred`，`status = pending`
- 「更新」→ UPDATE tasks SET ... WHERE id = ?
- 「跳过」→ 不操作

所有操作在同一个事务内执行，任一失败则整体回滚，返回 `status = failed`。

---

### Step 6 — 收尾更新

写库成功后：

- 更新 `courses.syllabus_parsed_at` 为当前时间
- 写入 `sync_log`，`sync_type = syllabus_parse`

---

## 错误处理

| 情况 | 行为 |
|------|------|
| 前置检查失败 | 终止，status = failed |
| syllabus 文件读取失败 | 终止，status = failed |
| AI 提取调用失败 | 终止，status = failed，provider 层错误码写入 error_message |
| AI 去重决策失败 | 该条任务标记为「插入」（保守策略），继续处理下一条 |
| 写库事务失败 | 回滚，status = failed |

---

## 配置项

```
db_path                 # SQLite 文件路径
syllabus_store_path     # syllabus 文件的根目录（本地存储时使用）
default_provider_id     # 默认 AI provider，可被 request 覆盖
```
