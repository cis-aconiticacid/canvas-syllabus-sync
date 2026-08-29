# Canvas Sync Database Schema
version: 0.3

## 设计原则

- 所有历史学期数据全部保留，查询时用 `term_id` 或时间字段过滤
- Canvas 原生数据与 AI 推断数据存在同一张 `tasks` 表，用 `source_type` 字段区分
- Syllabus 原文存储在独立的文件系统中，数据库只存路径
- `course_code` 是课程的稳定标识，去重逻辑的前置匹配条件，AI 解析时作为提示词上下文
- 所有表都带 `created_at` / `updated_at`，支持增量同步判断

---

## Term ID 格式约定

| 格式 | 含义 | 示例 |
|------|------|------|
| `{YY}F` | Fall 学期 | `25F` |
| `{YY}SP` | Spring 学期 | `25SP` |
| `{YY}SU` | Summer 学期 | `25SU` |

---

## 表结构

### `terms`
学期信息。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 自定义格式，如 `25F` `25SP` |
| `name` | TEXT | 完整名称，如 "Fall 2025"，仅供展示 |
| `start_at` | TEXT | 学期开始时间（ISO 8601） |
| `end_at` | TEXT | 学期结束时间（ISO 8601） |
| `created_at` | TEXT | 本地记录创建时间 |
| `updated_at` | TEXT | 本地记录更新时间 |

---

### `courses`
课程基本信息。`course_code` 是跨学期的稳定标识，去重逻辑的前置匹配条件。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | Canvas course_id |
| `term_id` | TEXT FK → terms.id | 所属学期，如 `25F` |
| `course_code` | TEXT NOT NULL | 课程代码，如 `CS544`，AI 解析时作为提示词上下文 |
| `name` | TEXT | 课程完整名称，仅供展示 |
| `syllabus_ref` | TEXT | syllabus 原文的存储路径 |
| `syllabus_parsed_at` | TEXT | 最近一次 AI 解析 syllabus 的时间 |
| `canvas_updated_at` | TEXT | Canvas 上课程信息的最后更新时间 |
| `created_at` | TEXT | 本地记录创建时间 |
| `updated_at` | TEXT | 本地记录更新时间 |

---

### `tasks`
所有任务统一存此表。原生 assignment 和 AI 推断的隐性任务都在此，用 `source_type` 区分。

**截止时间设计：**
- 有明确截止日：`has_explicit_due = 1`，`due_at_earliest = due_at_latest = 精确时间`
- 有模糊范围：`has_explicit_due = 0`，`due_at_earliest` 和 `due_at_latest` 存推断区间
- 完全未知：`has_explicit_due = 0`，两个字段均为 NULL

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | TEXT PK | 原生任务用 Canvas assignment_id；推断任务用 UUID |
| `course_id` | TEXT FK → courses.id | 所属课程 |
| `title` | TEXT | 任务标题 |
| `description` | TEXT | 任务描述 |
| `has_explicit_due` | INTEGER | 是否有明确截止日（0/1） |
| `due_at_earliest` | TEXT | 截止时间区间起点（ISO 8601），可为 NULL |
| `due_at_latest` | TEXT | 截止时间区间终点（ISO 8601），可为 NULL |
| `source_type` | TEXT | `canvas_native` \| `ai_inferred` |
| `source_document` | TEXT | 来源文档路径；原生任务为 NULL |
| `points_possible` | REAL | 分值；仅原生任务有，推断任务为 NULL |
| `submission_types` | TEXT | JSON 数组，如 `["online_upload"]`；原生任务专用 |
| `canvas_assignment_id` | TEXT | Canvas 原始 assignment_id；推断任务为 NULL |
| `confidence` | REAL | AI 推断置信度 0.0–1.0；原生任务为 NULL |
| `is_recurring` | INTEGER | 是否周期性任务（0/1），如"每周阅读" |
| `recurrence_note` | TEXT | 周期性说明，如 "每周一前提交"；非周期性为 NULL |
| `status` | TEXT | `pending` \| `completed` \| `dismissed` |
| `created_at` | TEXT | 本地记录创建时间 |
| `updated_at` | TEXT | 本地记录更新时间 |

---

### `files`
Canvas 课程文件的本地记录，由 agent-operation 在文件处理完成后写入。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `canvas_file_id` | TEXT UNIQUE NOT NULL | Canvas 原始文件 id |
| `course_id` | TEXT FK → courses.id | 所属课程 |
| `term_id` | TEXT FK → terms.id | 所属学期 |
| `filename` | TEXT | 文件名 |
| `file_path` | TEXT | 正式存储路径，格式：`{course_code}/{term_id}/week{N}/{filename}` 或 `{course_code}/{term_id}/general/{filename}` |
| `week_number` | INTEGER | 所属 week，无法归属时为 NULL |
| `file_size` | INTEGER | 文件大小（字节） |
| `canvas_updated_at` | TEXT | Canvas 上文件的最后更新时间，用于增量同步比对 |
| `published_at` | TEXT | Canvas 上文件的发布时间 |
| `status` | TEXT | `active` \| `error` |
| `error_reason` | TEXT | 处理失败的原因；status 为 active 时为 NULL |
| `created_at` | TEXT | 本地记录创建时间 |
| `updated_at` | TEXT | 本地记录更新时间 |

---

### `sync_log`
每次同步的执行记录，追踪增量同步状态和 AI 决策过程。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `course_id` | TEXT FK → courses.id | 本次同步的课程，NULL 表示全量同步 |
| `sync_type` | TEXT | `full` \| `incremental` \| `syllabus_parse` |
| `provider` | TEXT | 解析层使用的 AI provider，如 `anthropic` \| `deepseek`；非 AI 同步为 NULL |
| `status` | TEXT | `success` \| `failed` \| `partial` |
| `error_message` | TEXT | 失败时的错误信息 |
| `tasks_added` | INTEGER | 本次新增任务数 |
| `tasks_updated` | INTEGER | 本次更新任务数 |
| `tasks_skipped` | INTEGER | 去重命中、直接驳回的任务数 |
| `tasks_ai_resolved` | INTEGER | 交由 AI 决策的去重冲突数 |
| `ai_resolution_log` | TEXT | JSON 数组，记录每次 AI 三选一决策的输入和结果 |
| `started_at` | TEXT | 同步开始时间 |
| `finished_at` | TEXT | 同步结束时间 |

---

## 去重逻辑说明

### 前置条件
所有去重逻辑在 `course_code` 匹配的前提下执行。`course_code` 不匹配则静默跳过，不插入也不报 warning。

### 原生任务（canvas_native）
1. 查 `canvas_assignment_id` 是否完全匹配
2. 匹配 → 驳回，计入 `tasks_skipped`
3. 不匹配 → 正常插入

### 推断任务（ai_inferred）
1. 查同 `course_id` 下，与新任务时间区间重叠且在 ±5 天范围内的候选任务
2. 候选集为空 → 正常插入
3. 候选集非空 → 检查 `title` 重叠词是否 ≥ 2 个
   - 重叠词 < 2 → 正常插入
   - 重叠词 ≥ 2 → 交由 AI 三选一决策：
     - **保留旧的**：驳回新任务，计入 `tasks_skipped`
     - **替换**：用新任务覆盖旧任务，计入 `tasks_updated`
     - **两条都保留**：正常插入，计入 `tasks_added`
   - 决策过程和结果写入 `sync_log.ai_resolution_log`

### 无明确截止日的推断任务（has_explicit_due = 0，due 为 NULL）
- 跳过日期区间匹配
- 直接走 `title` 重叠词检查逻辑

---

## Syllabus 原文存储约定

`syllabus_ref` 和 `source_document` 字段存储本地路径：

```
file/storage/{term_id}/{course_code}.html
```

---

## 索引建议

```sql
CREATE INDEX idx_tasks_course_id ON tasks(course_id);
CREATE INDEX idx_tasks_due_at_earliest ON tasks(due_at_earliest);
CREATE INDEX idx_tasks_due_at_latest ON tasks(due_at_latest);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_source_type ON tasks(source_type);
CREATE INDEX idx_tasks_canvas_assignment_id ON tasks(canvas_assignment_id);
CREATE INDEX idx_courses_term_id ON courses(term_id);
CREATE INDEX idx_courses_course_code ON courses(course_code);
CREATE INDEX idx_files_canvas_file_id ON files(canvas_file_id);
CREATE INDEX idx_files_course_id ON files(course_id);
```

---

## 常用查询示意

```sql
-- 当前学期所有未完成任务，按截止区间排序
SELECT t.* FROM tasks t
JOIN courses c ON t.course_id = c.id
WHERE c.term_id = '25F'
  AND t.status = 'pending'
ORDER BY t.due_at_latest ASC NULLS LAST;

-- 本周截止的所有任务（含模糊区间）
SELECT * FROM tasks
WHERE due_at_earliest <= '2025-01-26'
  AND due_at_latest >= '2025-01-20'
  AND status = 'pending';

-- 某课程所有 AI 推断的隐性任务
SELECT * FROM tasks
WHERE course_id = '?'
  AND source_type = 'ai_inferred'
ORDER BY due_at_latest ASC NULLS LAST;

-- 没有明确截止日的待处理任务
SELECT t.*, c.course_code FROM tasks t
JOIN courses c ON t.course_id = c.id
WHERE t.has_explicit_due = 0
  AND t.status = 'pending';

-- 某课程某 week 的所有文件
SELECT * FROM files
WHERE course_id = '?'
  AND week_number = 3
  AND status = 'active';
```
