# AI Agent Showcase — 从需求到落地的硬成果

> **一句话定位**：不是"会调API"，是"给模糊需求→拆可执行方案→跑通并量化"的全链路AI工程能力。

---

> **闽江学院 数据科学与大数据技术 | 2026届 | AI Agent全栈实践**

## 🎯 四个核心项目

| 项目 | 业务场景 | 核心数据 | 技术关键词 |
|------|---------|---------|-----------|
| [AI金融量化信号系统](./src/financial/) | 基于新闻舆情自动生成A股交易信号 | **+11.56%**模拟盘收益、**13层**优化→0人工审核、**双轨制**新闻分析 | DeepSeek、凯利公式、量化、NLTK |
| [社区学习Agent](./src/community/) | AI在社区自主互动、学习、发帖 | **SSR级**76.67分、**日互动20-30条**、内容A/B实验、**150条/日**限流 | 动态素材、质量门、限流管控、自动体检 |
| [Agent自治系统](./src/agent-orchestration/) | 事件驱动的任务调度与自治运维 | **事件路由**+状态机、**自检自愈**（磁盘/API/编码）、**配置即代码** | 优先级队列、检查点、插件化 |
| [持续学习Agent](./src/self-improving/) | 从用户纠正中自动提取模式、升级本能 | **25+**纠正关键词检测、置信度**≥3次**自动升级SKILL.md规则 | 模式聚类、ECC持续学习、本能进化 |

---

## 📊 数据说话

```
金融AI：       +11.56%  │ 双轨制(8:30+盘中) │ 凯利公式仓位管理 │ 0人工审核
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
**数据**：pandas(清洗)、SQLite(存储)、RAG(检索增强)
**自动化**：OpenClaw Agent框架、cron、HEARTBEAT巡检、A/B内容实验、自动体检
**工程**：TypeScript严格模式、Result<T,E>、接口定义优先、25+纠正关键词检测

---

## 🚀 快速运行

```bash
pip install -r requirements.txt

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
src/
├── agent-orchestration/
│   └── scheduler.py              # 事件路由+状态机+插件化
├── community/
│   ├── content_generator.py      # 动态素材+质量门+A/B实验
│   └── meyo_post_daily.py        # 觅游社区自动发帖（素材→生成→发布）
├── self-improving/
│   ├── instinct_system.py        # 纠正检测+模式聚类+规则升级
│   └── detect_correction.py      # 25+关键词纠正检测器
├── infrastructure/
│   └── disk_guard.py             # 磁盘告警+自动清理+session归档
├── rag-knowledge-base/
│   └── build_index.py            # 本地Embedding+FAISS
├── dataset-cleaning/
│   └── pipeline.py               # 4阶段流水线+质量评分
├── model-evaluation/
│   └── framework.py              # 4维评测+幻觉检测
└── financial/
    └── news_analyzer.py          # 双轨制新闻分析+凯利公式仓位

docs/
└── architecture.md               # 架构设计文档（含mermaid图）

projects/
├── 02-financial-ai/
│   └── README.md                 # 金融量化系统详细文档
└── 03-community-agent/
    └── README.md                 # 社区Agent详细文档

config/
└── agent-system.json             # 调度器配置
```

---

> **不是研究AI，是用AI打硬仗。**
