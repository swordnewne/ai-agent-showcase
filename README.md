# AI Agent Showcase — 从需求到落地的硬成果

> **一句话定位**：不是"会调API"，是"给模糊需求→拆可执行方案→跑通并量化"的全链路AI工程能力。

---

## 🎯 四个核心项目

| 项目 | 业务场景 | 核心数据 | 技术关键词 |
|------|---------|---------|-----------|
| [金融信号预警系统](./src/financial/) | 实时行情监测+异常检测+恐慌降噪+**信号复盘对比** | **70只**股票池监测、**5分钟**级检测、**恐慌模式**自动降噪、**信号vs实盘**命中率/偏差/虚拟收益 | 新浪财经API、akshare、SQLite、阈值引擎、OpenClaw cron、虚拟收益计算 |
| [社区学习Agent](./src/community/) | AI在社区自主互动、学习、发帖 | **SSR级**76.67分、**日互动20-30条**、内容A/B实验、**150条/日**限流 | 动态素材、质量门、限流管控、自动体检 |
| [Agent自治系统](./src/agent-orchestration/) | 事件驱动的任务调度与自治运维 | **事件路由**+状态机、**自检自愈**（磁盘/API/编码）、**配置即代码** | 优先级队列、检查点、插件化 |
| [持续学习Agent](./src/self-improving/) | 从用户纠正中自动提取模式、升级本能 | **25+**纠正关键词检测、置信度**≥3次**自动升级SKILL.md规则 | 模式聚类、ECC持续学习、本能进化 |

---

## 📊 数据说话

```
金融信号：     70只监测 │ 5分钟级检测 │ 恐慌模式降噪 │ 涨跌停+大盘异动+放量
              信号复盘：命中率 / 价格偏差 / 1日虚拟收益 / 5日虚拟收益
              聚宽同步：持仓+交易日志 → SQLite → 市值计算 → 对比报告
社区Agent：    SSR级    │ 76.67分  │ 日互动20-30条 │ A/B实验+限流+自动体检
Agent调度：    事件驱动  │ 状态机管理 │ 自检自愈    │ 配置即代码
持续学习：     25+关键词 │ 置信度聚类 │ 自动升级规则 │ 越用越懂你
数据清洗：     4阶段流水线 │ 质量评分45→82 │ 异常检测  │
模型评测：     情感/板块/幻觉/延迟 │ 4维对比报告 │
RAG知识库：    本地Embedding │ FAISS向量检索 │ Top-K命中率评测 │
基础设施：     磁盘85%告警+清理 │ 30天session归档 │ 7天报告清理 │
```

---

## 🏗️ 系统架构

**核心设计**：事件驱动 + 状态机 + 共享数据层 + 持续学习闭环

- **事件层**：用户消息 / 定时触发 / 系统告警 / 心跳信号
- **路由器**：优先级队列 + 状态机（PENDING→RUNNING→SUCCESS|FAILED|TIMEOUT）
- **执行层**：对话处理器 / 金融AI / 社区学习 / 系统监控
- **数据层**：RAG知识库 / 数据清洗 / 模型评测 / 分层记忆（共享服务，按需调用）
- **学习层**：纠正检测 → 模式聚类 → 置信度升级 → SKILL.md规则写入
- **存储层**：SQLite / JSON / FAISS / 配置文件

**自检自愈**：磁盘85%自动清理、API超时指数退避、编码失败自动切换、任务失败3次重试

**持续学习**：用户说"不对""错了"→自动记录→出现3次→升级为规则→AI越用越懂你

**详细设计**：[docs/architecture.md](docs/architecture.md) | **调度器代码**：[src/agent-orchestration/scheduler.py](src/agent-orchestration/scheduler.py)

---

## 🛠️ 技术栈

**架构**：事件驱动、状态机、配置即代码、插件化、检查点机制、持续学习闭环
**AI/ML**：DeepSeek API、Sentence-Transformers(本地Embedding)、FAISS(向量检索)、凯利公式
**数据**：pandas(清洗)、SQLite(存储)、akshare(历史行情)、RAG(检索增强)
**自动化**：OpenClaw Agent框架、OpenClaw cron、HEARTBEAT巡检、A/B内容实验、自动体检
**工程**：TypeScript严格模式、Result<T,E>、接口定义优先、25+纠正关键词检测

---

## 🚀 快速运行

```bash
pip install -r requirements.txt

# ── 金融信号预警系统 ──
cd src/financial/
python3 signal_pipeline.py --mode realtime   # 实时检测（涨幅≥5% + 成交额≥10亿 / 涨跌停 / 大盘异动）
python3 signal_pipeline.py --mode summary    # 收盘总结（大盘 + 涨跌停 + TOP5）
python3 launcher.py warmup                 # 盘前预热

# 聚宽模拟盘同步 + 市值计算 + 信号复盘（工作日 15:05-15:15 自动执行）
cd scripts/
JQ_BACKTEST_ID="your_id" python3 fetch_joinquant_full.py   # 抓取持仓+交易日志
python3 update_portfolio_value_akshare.py                   # 实时市值计算
python3 daily_signal_review.py --date 2026-06-27            # 信号 vs 实盘复盘

# 手动录入信号（用于测试/复盘）
python3 add_signal.py --date 2026-06-27 --code 688256 --side buy --price 1480 --reason "测试"

# Agent调度器（事件路由+状态机演示）
python src/agent-orchestration/scheduler.py

# 持续学习系统（记录纠正+聚类升级，需先创建 self-improving/instincts/ 目录）
python src/self-improving/instinct_system.py --record "不要中英文混用" --context "之前回复"
python src/self-improving/instinct_system.py --evolve

# RAG知识库构建
python src/rag-knowledge-base/build_index.py

# 数据清洗流水线
python src/dataset-cleaning/pipeline.py

# 模型评测对比
python src/model-evaluation/framework.py

# 基础设施巡检
python src/infrastructure/disk_guard.py

# 社区内容生成
python src/community/content_generator.py
```

---

## 📂 仓库结构

```
scripts/
├── add_signal.py                  # 手动/批量信号录入 → decision_signals
├── daily_signal_review.py         # 信号 vs 实盘复盘（命中率/偏差/虚拟收益）
├── fetch_joinquant_full.py        # 聚宽模拟盘 API 抓取（持仓+交易日志）
├── update_portfolio_value_akshare.py  # 实时市值计算（akshare）
└── daily_run.py                   # 旧版定时入口（已迁移至 OpenClaw cron）

src/financial/
├── signal_pipeline.py             # 实时行情获取 + 异常检测 + 信号记录到数据库
├── launcher.py                    # 一键启动器（realtime/summary/warmup）
├── ai_client.py                   # 支持Kimi/DeepSeek/OpenAI
├── data_provider.py               # 新浪API实时 + akshare历史K线
├── market_context.py              # 真实指数数据 + 规则化摘要
├── portfolio_tracker.py           # FIFO成本 + 防超卖 + 快照
├── stock_analyzer.py              # 6维度100分制评分
├── kelly_position.py              # 凯利公式 + 大盘环境调整
├── signal_tracker.py              # 创建→验证→准确率统计
├── report_generator.py            # 纯文本微信友好格式
├── wechat_pusher.py             # 待推送消息 → 微信告警
└── news_analyzer.py             # 双轨制新闻分析（预留接口）

config/
├── stock_pool.yaml                # 70只股票监控池（大盘15+中盘30+小盘25）
├── finance_signals.json           # 信号阈值配置
└── finance_cron.txt               # 定时任务参考配置

data/
├── finance.db                     # SQLite 数据库（信号+持仓+市值+复盘）
└── db_schema.sql                  # 表结构定义

docs/
└── architecture.md                # 架构设计文档（含mermaid图）
```

---

> **不是研究AI，是用AI打硬仗。**
