# canvas-syllabus-sync

从 Canvas LMS 同步课程数据到本地 SQLite，并通过 AI 解析 syllabus 提取没有明确列出的隐性任务。数据库通过 MCP 暴露给 Claude Desktop / Cowork 等 AI 客户端查询，AI 调用层支持多个 provider（Anthropic、DeepSeek、OpenAI、Kimi）。

---

## 文档

| 文档 | 受众 | 说明 |
|------|------|------|
| [docs/schema.md](./docs/schema.md) | 所有人 | 数据库表结构、去重逻辑、term_id 格式约定 |
| [docs/provider-interface.md](./docs/provider-interface.md) | 开发者 | AI provider 抽象层接口，支持多 provider 切换 |
| [docs/canvas-sync.md](./docs/canvas-sync.md) | 开发者 | Canvas 原生数据同步程序说明，字段映射与写库规则 |
| [docs/parse-interface.md](./docs/parse-interface.md) | 开发者 | Syllabus 解析层接口，AI 提取隐性任务的完整流程 |
| [docs/agent-interface.md](./docs/agent-interface.md) | AI agent | AI 可调用的所有操作，agent-operation 的接口总表 |

---

## 架构概览

```
Canvas API
    │
    ▼
canvas-sync（无 AI，定时/手动触发）
    │ 下载文件到 buffer，写 manifest
    ▼
buffer/  ◄── agent-operation（AI 驱动）
                   │
              读 manifest
              move_file()
              insert_file_record()
              parse_course()
                   │
                   ▼
                SQLite
                   │
                   ▼
           mcp-sqlite（MCP server）
                   │
                   ▼
    Claude Desktop / Cowork / Cursor / 其他 MCP 客户端
```

**两个程序的职责边界：**

| | canvas-sync | agent-operation |
|---|---|---|
| Canvas API 调用 | ✓ | ✗ |
| 文件下载到 buffer | ✓ | ✗ |
| manifest 初始写入 | ✓ | ✗ |
| manifest 状态更新 | ✗ | ✓ |
| 文件移动到正式位置 | ✗ | ✓ |
| 数据库写入（files 表） | ✗ | ✓ |
| syllabus 解析 | ✗ | ✓ |
| AI 调用 | ✗ | ✓ |

---

## 目录结构

```
project/
    file/
        week.config         ← week 配置（手动维护）
        manifest.json       ← buffer 状态
        storage/            ← syllabus 原文
            {term_id}/
                {course_code}.html
    buffer/                 ← 下载文件暂存区
    storage/                ← 课程文件正式存储区
        {course_code}/
            {term_id}/
                week{N}/
                general/
    error/                  ← 处理失败的文件
    docs/
        schema.md
        provider-interface.md
        canvas-sync.md
        parse-interface.md
        agent-interface.md
    src/
        canvas-sync/            ← 同步程序
            main.py             ← 入口，按顺序调四个 sync 模块
            config.py           ← 读取 .env 配置
            db.py               ← 建表、索引
            utils.py            ← course_code 标准化、week 编号计算
            sync_terms.py       ← 同步学期
            sync_courses.py     ← 同步课程 + 下载 syllabus 原文
            sync_tasks.py       ← 同步原生 assignment
            sync_files.py       ← 下载文件到 buffer、写 manifest
        agent-operation/        ← AI 驱动的文件处理程序
            main.py             ← AI agent 唯一入口，暴露所有可调用操作
            config.py           ← 读取 .env 配置，含 provider 配置加载
            manifest.py         ← manifest 读写、状态更新
            file_ops.py         ← move_file、move_file_to_error、redirect_file
            db_ops.py           ← insert_file_record、update_task_status、get_* 查询
            parse_ops.py        ← parse_course 完整实现，含 AI 提取和去重决策
        provider/               ← AI provider 抽象层
            __init__.py
            base.py             ← 抽象基类、dataclass 定义、schema_to_prompt
            retry.py            ← 指数退避重试逻辑，所有 provider 共用
            anthropic_provider.py
            openai_provider.py  ← OpenAI / DeepSeek / Kimi 共用
            factory.py          ← 根据 provider_id 返回实例
    canvas.db               ← SQLite 数据库
    .env                    ← 本地配置（不提交 git）
    .env.example            ← 配置模板
    requirements.txt
```

---

## 快速开始

**1. 安装依赖**

```bash
pip install -r requirements.txt
```

**2. 配置环境变量**

```bash
cp .env.example .env
```

编辑 `.env`，填入以下必填项：

```
CANVAS_API_TOKEN=     # Canvas → Account → Settings → New Access Token
CANVAS_BASE_URL=      # 如 https://canvas.wisc.edu
ANTHROPIC_API_KEY=    # 或其他 provider 的 key
ANTHROPIC_MODEL=      # 如 claude-sonnet-4-20250514
```

**3. 配置学期 week 起始日期**

编辑 `file/week.config`，填入当前学期信息：

```json
{
  "25F": {
    "week1_start": "2025-09-03",
    "total_weeks": 16
  }
}
```

**4. 运行 Canvas 同步**

```bash
# 全量同步（首次运行）
python src/canvas-sync/main.py --type full

# 增量同步（日常使用）
python src/canvas-sync/main.py --type incremental
```

同步完成后，buffer 目录会有新下载的文件，`file/manifest.json` 会列出待处理条目。

**5. 运行 agent-operation 处理文件**

在 Cursor、Claude Cowork 或其他 AI agent 中，指向 `src/agent-operation/main.py`，
按 `agent-interface.md` 中的标准处理流程执行：

```
get_pending_files()
  → move_file()
  → insert_file_record()
  → （可选）parse_course()
  → clear_inserted_from_manifest()
```

**6. 配置 mcp-sqlite 连接 Claude Desktop**

安装 mcp-sqlite：

```bash
pip install mcp-sqlite
```

在 Claude Desktop 的 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "canvas": {
      "command": "mcp-sqlite",
      "args": ["canvas.db"]
    }
  }
}
```

重启 Claude Desktop 后即可直接用自然语言查询课程任务。

---

## 配置项说明

完整配置项见 `.env.example`。关键配置：

| 变量 | 说明 | 必填 |
|------|------|------|
| `CANVAS_API_TOKEN` | Canvas API 访问令牌 | ✓ |
| `CANVAS_BASE_URL` | Canvas 机构域名 | ✓ |
| `DEFAULT_PROVIDER_ID` | 默认 AI provider | 默认 `anthropic` |
| `{PROVIDER}_API_KEY` | 对应 provider 的 API key | 至少配一个 |
| `{PROVIDER}_MODEL` | 对应 provider 的模型名称 | 至少配一个 |
