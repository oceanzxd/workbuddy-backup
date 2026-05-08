---
name: agent-brain-manager
description: |
  小云的个人知识库大脑管理器。每次执行任务前，优先扫描个人知识库（93+技能索引+历史记忆），
  自动找到最相关的技能组合，避免遗漏、提高效率。
  触发词：查一下、做这个用什么技能、扫描知识库、查技能库、我的技能有哪些、记忆搜索
category: 记忆系统
agent_created: true
trigger_keywords: |
  查一下、做什么用、扫描知识库、查技能库、技能列表、知识库、
  我的技能、召回记忆、查记忆、搜记忆、技能库、索引技能、
  brain、agent_brain、skill index、搜一下
---

# agent-brain-manager — 个人知识库大脑

## 核心架构

```
~/.workbuddy/agent_brain/brain.db   ← SQLite 数据库（FTS5全文搜索）
  ├── skills表       93+技能的元数据（名称/触发词/分类/使用次数）
  ├── memories表     历史记忆索引（关键词/内容摘要）
  ├── task_history   任务执行记录
  └── skills_fts    FTS5 全文搜索索引（技能）
      memories_fts  FTS5 全文搜索索引（记忆）
```

## 脚本位置

```
~/.workbuddy/agent_brain/agent_brain.py
```

## 使用方式

每次接到新任务，按以下流程执行：

### Step 1：任务扫描（每次必做）

**自动触发：** 任何需要技能匹配的任务，先扫描数据库：
```
python ~/.workbuddy/agent_brain/agent_brain.py scan "<用户任务描述>"
```
这会返回 Top 8 最相关的技能列表，包含：名称、分类、触发词、使用次数。

**示例：**
```bash
python ~/.workbuddy/agent_brain/agent_brain.py scan "帮我分析汇源通信的走势"
python ~/.workbuddy/agent_brain/agent_brain.py scan "生成今日A股全量报告"
python ~/.workbuddy/agent_brain/agent_brain.py scan "修复Hermes QQ Bot"
```

### Step 2：记忆召回（必要时）

如果任务涉及历史决策、持仓、错误教训：
```
python ~/.workbuddy/agent_brain/agent_brain.py recall "<关键词>"
```

### Step 3：执行技能

根据扫描结果，Load 最相关的 Skill（用 `Skill` 工具）。

### Step 4：记录任务（完成后）

```
python ~/.workbuddy/agent_brain/agent_brain.py record "<任务描述>"
```
这会更新技能使用次数，供后续扫描排序参考。

## 快捷命令

| 命令 | 作用 |
|:-----|:-----|
| `python agent_brain.py init` | 初始化数据库 |
| `python agent_brain.py index` | 全量重建索引（技能+记忆） |
| `python agent_brain.py scan <任务>` | 扫描相关技能 |
| `python agent_brain.py recall <关键词>` | 搜索历史记忆 |
| `python agent_brain.py status` | 查看状态面板 |
| `python agent_brain.py stats` | 技能使用排行 |
| `python agent_brain.py record <任务>` | 记录本次任务 |

## 自动执行规则

**触发条件（每次任务开始时）：**
1. 用户提出任何需要技能配合的任务
2. 涉及股票、期货、新闻、市场分析
3. 涉及文档生成、代码开发、自动化
4. 涉及系统配置、Bot修复

**执行顺序：**
```
收到任务 → scan_task() → 展示Top技能 → Load最相关Skill → 执行 → record_task()
```

## 数据库维护

- **索引时机：** 新增技能后自动重建；记忆文件更新后重建
- **手动重建：** `python agent_brain.py index`
- **容量：** SQLite 无硬限制，记忆文件按 source 字段去重

## 技能分类映射

| 分类 | 包含关键词 | 示例技能 |
|:----:|:---------|:---------|
| 金融交易 | stock, trading, finance | china-stock-analysis, trading-system |
| 市场分析 | market, a股, 板块 | market-analysis, A股市场主线 |
| 新闻资讯 | news, digest, rss | news-aggregator, ai-daily-digest |
| 浏览器 | browser, web, 抓取 | agent-browser, playwright-cli |
| 文档办公 | document, office, word | word-docx, pdf |
| 宏观分析 | macro, 宏观, 经济 | macro-analysis, wind-macro |
| 系统工具 | system, 桌面, 自动化 | desktop-control, cron-tasks |
| 数据 | data, akshare, tdx | akshare, tdx-financials |
| 社交 | social, twitter, wechat | twitter-analysis, 腾讯文档 |

## 注意事项

- 扫描结果按 **相关度 + 使用次数** 综合排序
- agent_created=true 的技能（自建技能）优先展示
- ⚠️ **FTS5 不支持中文分词！** SQLite FTS5 对中文搜索效果很差。agent_brain.py 使用 **LIKE 查询作为主要搜索方式**，FTS5 仅作为英文关键词的补充。中文技能搜索依赖 keyword extraction（提取2-4字关键词）做 LIKE 匹配。
- **index 会重置 use_count！** 每次 `python agent_brain.py index` 会清空 skills 表重建索引，导致使用次数归零。
- 数据库路径：`~/.workbuddy/agent_brain/brain.db`
- 核心脚本：`~/.workbuddy/agent_brain/agent_brain.py`（700+行 Python）
