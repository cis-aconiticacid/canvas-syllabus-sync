# Canvas 同步文档
version: 0.2

## 定位

本文档描述从 Canvas API 拉取原生数据并写入本地 SQLite 的同步程序。该程序不涉及任何 AI 操作，纯粹是数据搬运，由定时任务或手动触发执行。文件下载后只写入下载记录，后续的文件处理（移动、插入数据库、生成 manifest）全部由 agent-operation 负责。

---

## 目录结构

```
project/
    file/
        week.config         ← week 配置文件
        manifest.json       ← buffer 状态，由 agent-operation 维护
        storage/            ← syllabus 原文存储
            {term_id}/
                {course_code}.html
    buffer/                 ← 下载文件暂存区
    storage/                ← 课程文件正式存储区
        {course_code}/
            {term_id}/
                week{N}/
                general/
    error/                  ← 处理失败的文件
    src/
        canvas-sync/
        agent-operation/
```

---

## Week 配置

路径：`file/week.config`

```json
{
  "25F": {
    "week1_start": "2025-09-03",
    "total_weeks": 16
  },
  "25SP": {
    "week1_start": "2025-01-21",
    "total_weeks": 16
  }
}
```

**week 编号计算：**

```
week_number = floor((published_at - week1_start) / 7) + 1
```

- 结果 < 1 或 > total_weeks → `week_number = NULL`，文件归入 `general/`
- canvas-sync 在下载文件时读此配置计算 week 编号，写入下载记录供 agent-operation 使用

---

## 触发方式

| 方式 | 说明 |
|------|------|
| 手动触发 | 直接运行同步程序，全量或指定课程 |
| 定时任务 | 按配置的 cron 表达式自动执行增量同步 |

---

## 同步范围

每次同步按以下顺序执行，顺序不可颠倒（下层依赖上层的外键）：

```
1. terms
2. courses
3. tasks（canvas_native 部分）
4. files（下载到 buffer，写下载记录）
```

---

## 各表同步逻辑

### terms

**Canvas API 端点：** `GET /api/v1/accounts/self/terms`

**写入规则：**
- `id` 字段写入本文档约定的自定义格式（`25F` / `25SP` / `25SU`），不使用 Canvas 返回的数字 term_id
- Canvas 返回的 `start_at` / `end_at` 直接写入
- 已存在的 term 检查 `start_at` / `end_at` 是否有变化，有变化则更新，无变化跳过

---

### courses

**Canvas API 端点：** `GET /api/v1/courses?enrollment_state=active&include[]=term&include[]=syllabus_body`

**写入规则：**
- `id` 使用 Canvas course_id
- `term_id` 写入对应的自定义 term 格式
- `course_code` 从 Canvas 返回的 `course_code` 字段截取，去掉学期后缀（如 `CS544_LEC_25F` → `CS544`）
- `syllabus_ref` 写入本地路径 `file/storage/{term_id}/{course_code}.html`，syllabus 原文同步下载到此路径
- `canvas_updated_at` 写入 Canvas 返回的 `updated_at`
- 已存在的 course 比对 `canvas_updated_at`，有变化则更新相关字段，无变化跳过

---

### tasks（canvas_native）

**Canvas API 端点：** `GET /api/v1/courses/{course_id}/assignments?include[]=submission`

**写入规则：**

| 本地字段 | Canvas 字段 | 说明 |
|----------|-------------|------|
| `id` | `assignment.id` | 直接使用 Canvas assignment_id |
| `course_id` | `assignment.course_id` | |
| `title` | `assignment.name` | |
| `description` | `assignment.description` | HTML，原样存储 |
| `has_explicit_due` | 判断 `due_at` 是否为 null | 非 null 则为 1 |
| `due_at_earliest` | `assignment.due_at` | 有明确截止日时与 due_at_latest 相同 |
| `due_at_latest` | `assignment.due_at` | 有明确截止日时与 due_at_earliest 相同 |
| `source_type` | 固定值 `canvas_native` | |
| `source_document` | NULL | 原生任务无来源文档 |
| `points_possible` | `assignment.points_possible` | |
| `submission_types` | `assignment.submission_types` | JSON 数组原样存储 |
| `canvas_assignment_id` | `assignment.id` | 与 id 字段相同 |
| `confidence` | NULL | 原生任务无置信度 |
| `is_recurring` | 固定值 0 | 原生任务不标记周期性 |
| `recurrence_note` | NULL | |
| `status` | 根据 `submission.workflow_state` 判断 | 见下方状态映射 |

**Canvas submission 状态映射：**

| Canvas workflow_state | 本地 status |
|-----------------------|-------------|
| `submitted` / `graded` | `completed` |
| 其他 | `pending` |

**去重规则：**
- 查 `canvas_assignment_id` 是否已存在
- 已存在 → 比对 `title`、`due_at_latest`、`points_possible` 是否有变化，有变化则更新，无变化跳过
- 不存在 → 插入

---

### files（下载到 buffer）

**Canvas API 端点：** `GET /api/v1/courses/{course_id}/files`

**增量判断：**
- 查本地 `files` 表中该 `canvas_file_id` 的 `canvas_updated_at` 和 `file_size`
- 两者与 Canvas 返回值均一致 → 跳过，不重新下载
- 任一不一致或本地无记录 → 下载到 `buffer/`

**下载后写入下载记录（临时结构，供 agent-operation 读取）：**

```json
{
  "canvas_file_id": "...",
  "filename": "...",
  "buffer_path": "buffer/{filename}",
  "course_code": "CS544",
  "term_id": "25F",
  "week_number": 3,
  "published_at": "...",
  "file_size": 204800,
  "canvas_updated_at": "..."
}
```

此记录追加到 `file/manifest.json`，status 初始值为 `pending`。`files` 表的正式写入由 agent-operation 的 `insert_file_record()` 完成。

---

## sync_log 写入

每次同步结束后写入一条 `sync_log` 记录：

| 字段 | 值 |
|------|----|
| `course_id` | 指定课程的 id，全量同步时为 NULL |
| `sync_type` | `full` 或 `incremental` |
| `provider` | NULL（本程序不调用 AI） |
| `status` | `success` / `failed` / `partial` |
| `tasks_added` | 新插入的 canvas_native 任务数 |
| `tasks_updated` | 更新的 canvas_native 任务数 |
| `tasks_skipped` | 无变化跳过的任务数 |
| `tasks_ai_resolved` | 固定 0 |
| `ai_resolution_log` | NULL |

---

## 错误处理

- Canvas API 返回 401 → 停止同步，写 `sync_log.status = failed`
- Canvas API 返回 429 → 指数退避重试，最多 3 次
- 单条记录写库失败 → 记录错误继续处理下一条，最终 `status = partial`
- 文件下载失败 → 跳过该文件，不写入 manifest，记录到 `sync_log.error_message`
- term 不存在时插入 course → 先补插 term 再继续

---

## 配置项

```
canvas_api_token        # Canvas API 密钥，从环境变量读取
canvas_base_url         # 机构的 Canvas 域名，如 https://canvas.wisc.edu
db_path                 # SQLite 文件路径
buffer_path             # buffer 区根目录
week_config_path        # week 配置文件路径，默认 file/week.config
sync_interval_minutes   # 定时同步间隔，0 表示不自动同步
```
