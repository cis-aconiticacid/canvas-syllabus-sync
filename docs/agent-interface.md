# Agent Interface
version: 0.1

## 定位

本文档是 AI agent 与系统交互的唯一参考。所有 AI 可调用的操作都在此定义。其他文档（schema、parse-interface、canvas-sync 等）是实现参考，供人阅读，AI 不需要读。

---

## 操作分类

| 类型 | 说明 | 可逆性 |
|------|------|--------|
| `read` | 只读，不修改任何状态 | 完全可逆 |
| `write` | 修改数据库或移动文件 | 可通过重新同步恢复 |
| `destructive` | 不可撤销的操作 | 不可逆，谨慎调用 |

---

## 错误处理原则

- `read` 操作失败 → 返回错误，AI 自行决定是否重试
- `write` 操作失败 → 返回错误码和失败原因，AI 根据错误码决定下一步
- 文件移动失败 → AI 调用 `move_file_to_error()` 将文件移至 error 目录，记录原因
- 任何操作都不需要人工介入，AI 自己处理所有错误分支

---

## Read 操作

### `get_manifest()`
读取当前 buffer 区的待处理文件列表。

**返回：**
```
files: [
  {
    canvas_file_id
    filename
    buffer_path
    course_code
    term_id
    week_number
    published_at
    file_size
    status          # pending | moved | inserted | error
  }
]
```

---

### `get_pending_files()`
`get_manifest()` 的过滤版，只返回 `status = pending` 的条目。AI 处理文件时优先调这个。

**返回：** 同 `get_manifest()`，只含 pending 条目

---

### `get_course_info(course_code, term_id)`
查询课程基本信息，AI 在处理文件前用于确认课程存在。

**参数：**
```
course_code         必填
term_id             必填
```

**返回：**
```
course_id
course_code
term_id
name
syllabus_ref
syllabus_parsed_at
```

---

### `get_week_range(course_code, term_id, week_number)`
查询指定 week 的日期区间，用于确认文件归属。

**参数：**
```
course_code
term_id
week_number
```

**返回：**
```
week_number
start_date
end_date
```

---

## Write 操作

### `move_file(canvas_file_id, dest_path, rename_to?)`
将文件从 buffer 移动到正式存储位置，并原子性地更新 manifest status 为 moved。
AI 可在移动时通过 `rename_to` 指定更规范的文件名。

**参数：**
```
canvas_file_id      必填
dest_path           必填，格式：{course_code}/{term_id}/week{N}
                    无法归属 week 的文件：{course_code}/{term_id}/general
rename_to           选填，新文件名。不填则保留原文件名。
```

**返回：**
```
success             true | false
final_path          实际存储的完整路径
error_code          失败时填写，见下方错误码
```

**错误码：**

| error_code | 含义 | AI 应对 |
|------------|------|---------|
| `dest_exists` | 目标路径已有同名文件 | 调用 `move_file_to_error()` |
| `source_not_found` | buffer 中找不到该文件 | 检查 manifest 是否过期，重新调用 `get_pending_files()` |
| `io_error` | 文件系统错误 | 调用 `move_file_to_error()` |

---

### `redirect_file(canvas_file_id, new_path)`
将数据库中指定文件的存储路径更新为新路径，仅做数据库同步，不移动实际文件。
供人工操作使用：当文件已手动移动到新位置后，用此操作同步数据库记录。
调用前文件必须已在新路径上。

**参数：**
```
canvas_file_id      必填
new_path            必填，文件的新完整路径，必须实际存在
```

**返回：**
```
success             true | false
error_code          失败时填写，见下方错误码
```

**错误码：**

| error_code | 含义 | 应对 |
|------------|------|------|
| `file_not_found` | new_path 指向的文件不存在 | 确认文件已移动到新路径后再调用 |
| `not_in_db` | canvas_file_id 在数据库中不存在 | 检查 canvas_file_id 是否正确 |
| `db_error` | 数据库更新失败 | 重试一次 |

---

### `insert_file_record(canvas_file_id, file_path)`
将文件信息插入 `files` 表，并将 manifest 中该条目 status 更新为 `inserted`。两步操作原子执行。

**参数：**
```
canvas_file_id      必填
file_path           必填，move_file() 返回的 final_path
```

**返回：**
```
success             true | false
file_id             插入成功后的数据库 id
error_code          失败时填写
```

**错误码：**

| error_code | 含义 | AI 应对 |
|------------|------|---------|
| `already_exists` | 数据库中已有该 canvas_file_id | 跳过，视为正常 |
| `course_not_found` | course_id 在数据库中不存在 | 停止处理该文件，记录错误 |
| `db_error` | 数据库写入失败 | 重试一次，仍失败则调用 `move_file_to_error()` |

---

### `parse_course(course_id, term_id, provider_id)`
触发指定课程的 syllabus 解析，提取隐性任务写入 `tasks` 表。详见 parse-interface.md。

**参数：**
```
course_id           必填
term_id             必填
provider_id         选填，不填则使用配置默认值
```

**返回：**
```
status              success | failed | partial | skipped
tasks_added
tasks_updated
tasks_skipped
tasks_ai_resolved
error_message
sync_log_id
```

---

### `update_task_status(task_id, status)`
更新任务状态。

**参数：**
```
task_id             必填
status              必填，pending | completed | dismissed
```

**返回：**
```
success             true | false
error_code
```

---

## Destructive 操作

### `move_file_to_error(canvas_file_id, reason)`
将文件从 buffer 或正式位置移动到 error 目录，manifest 中该条目 status 更新为 `error`。不可撤销。

**参数：**
```
canvas_file_id      必填
reason              必填，记录移入 error 目录的原因，写入 manifest 和 files 表
```

**返回：**
```
success             true | false
error_path          文件在 error 目录的最终路径
```

---

### `clear_inserted_from_manifest()`
从 manifest 中清除所有 `status = inserted` 的条目。通常在一批文件全部处理完后调用。不可撤销。

**参数：** 无

**返回：**
```
cleared_count       清除的条目数
```

---

## 标准处理流程

AI 处理 buffer 文件时应按以下顺序调用：

```
1. get_pending_files()
        ↓ 无 pending 文件则结束
2. 对每个 pending 文件：
        ↓
   get_course_info(course_code, term_id)
        ↓ course 不存在则 move_file_to_error()
   get_week_range(course_code, term_id, week_number)
        ↓
   move_file(canvas_file_id, dest_path, rename_to?)   ← rename_to 由 AI 自行决定是否填写
        ↓ 失败则 move_file_to_error()
   insert_file_record(canvas_file_id, final_path)
        ↓ 失败则 move_file_to_error()
3. clear_inserted_from_manifest()
```
