# AI Agent Showcase — 从需求到落地的工程实践

> **一句话定位**：从模糊业务需求出发，独立完成方案设计、工程实现与效果量化的全链路 AI 工程实践。

---

## 🏆 重点项目：A 股盘前研究流水线（2026-10 全新重构）

**业务场景**：每个交易日 08:05 自动生成盘前研究报告——全市场连涨候选筛选、五源新闻证据分析、模型评分与风险闸门、盘后回填结算，全链路自动化运行。

```
07:50  五源新闻归档（新浪/华尔街见闻/新华社/政府网/财联社）
08:05  BaoStock 候选筛选（PIT 查询，61 日完整性逐只校验）
       → 节点1：DeepSeek 全量新闻语义分析（证据快照落盘，哈希绑定）
       → 冻结量表纯脚本预评（v1.0，规则版本化，不因当日分布临时调整）
       → 节点2：逐只模型评审（独立无状态请求）+ 空头审查 + 条件式独立复核
       → 本地风险闸门三问：证据完整性 / 判断不确定性 / 机会质量门槛
       → decision_日期.json 机读决策 + 盘前报告（计算/叙述分离标注）
18:10  盘后增量入库（staging → validated 状态机，逐批回执哈希）
       → 前瞻回填：T+1 买入/T+2 结算/T+3 关窗，费后保本价逐笔精算，
         涨停不可成交隔离，四档资金组合模拟，沪深300 alpha 超额口径
```

**核心设计纪律**：

- **版本化数据集**：PostgreSQL 中历史基线与每日增量分离，`active/validated/failed` 状态机由数据库控制函数约束；所有读取经由统一 reader 并附回执哈希，每行数据均可溯源至供应商响应哈希；
- **新闻证据链**：发布时间逐源核验（与原文页面逐秒比对），双条件窗口门禁防止过期稿件进入当日窗口；核验状态分级参与排序，缺失信息必须明示，不得伪造；
- **模型调用纪律**：所有模型调用均为一次性无状态请求，不携带会话上下文；复核模型与主评分相互隔离以避免锚定；模型输出均可按 packet hash 追溯输入；
- **回填统计纪律**：未到期样本标记 `pending` 而不置零，无法解析样本标记 `unresolved` 并单独计数；框架调整建议须同时满足"累计样本 ≥100 只次、组间差异 ≥15pp、连续 3 期方向一致"方可提出，不达标逐条标记"暂存"——避免小样本噪声支撑规则调整；
- **失败规则**：任何环节失败均输出 `partial/failed` 并保留现场，禁止静默降级，禁止伪造完整结果。

**关键指标**：A 股全市场 5,200+ 只逐日覆盖 │ 历史基线自 2016 年起 2,600+ 交易日逐日核验完整 │ 新闻窗口 5 源 200+ 条/日全量投递 │ 回填样本 1,200+ 只次 │ 对抗测试与冒烟测试覆盖全部闸门

**代码导览**：
[src/financial/preopen_report.py](src/financial/preopen_report.py) 报告主链路 ·
[src/financial/news_window.py](src/financial/news_window.py) 新闻窗口模块 ·
[src/financial/score_prescreen.py](src/financial/score_prescreen.py) 冻结量表预评 ·
[src/financial/p0a8_review.py](src/financial/p0a8_review.py) 逐只评审与闸门 ·
[src/financial/p1a3_backfill.py](src/financial/p1a3_backfill.py) 回填结算引擎 ·
[src/financial/llm_config.py](src/financial/llm_config.py) 配置统一加载器

> 前身"金融信号预警系统"（70 只股票池实时监测）已于 2026-10 归档封存，`signal_pipeline.py` 保留作演进对照。

---

## 🎯 其他项目

| 项目 | 业务场景 | 核心指标 | 技术关键词 |
|------|---------|---------|-----------|
| [社区学习 Agent](./src/community/) | AI 在社区自主互动、学习与发帖 | SSR 级评分 76.67、日均互动 20-30 条、内容 A/B 实验、150 条/日限流 | 动态素材、质量门、限流管控、自动体检 |
| [Agent 自治系统](./src/agent-orchestration/) | 事件驱动的任务调度与自治运维 | 事件路由与状态机、自检自愈（磁盘/API/编码）、配置即代码 | 优先级队列、检查点、插件化 |
| [持续学习 Agent](./src/self-improving/) | 从用户纠正中提取模式并演进规则 | 25+ 纠正关键词检测、置信度满 3 次自动升级 SKILL.md 规则 | 模式聚类、持续学习、规则演进 |
| [RAG 知识库](./src/rag-knowledge-base/) | 本地向量检索与命中率评测 | 本地 Embedding、FAISS 向量检索、Top-K 命中率评测 | Sentence-Transformers、FAISS |
| [数据清洗流水线](./src/dataset-cleaning/) | 四阶段数据质量流水线 | 质量评分 45→82 | pandas、规则引擎 |
| [模型评测框架](./src/model-evaluation/) | 情感/板块/幻觉/延迟四维对比 | 多模型对比报告 | 评测脚本化 |

---

## 🛠️ 技术栈

**数据工程**：PostgreSQL（版本化数据集、状态机、advisory lock）、BaoStock、Parquet staging、回执哈希链
**AI/ML**：DeepSeek API（思考模式、无状态一次性调用）、Sentence-Transformers、FAISS
**架构**：深模块设计、端口适配、配置即代码、fail-closed 失败规则、边界测试
**自动化**：OpenClaw cron、幂等重试、新旧路径并行验证后切换
**工程**：Python、TypeScript 严格模式、金样测试锁定行为、变更回滚机制

---

## 🚀 快速运行

```bash
pip install -r requirements.txt

# 离线测试（不需要数据库与网络）
python3 tests/test_news_window.py      # 新闻窗口门禁边界测试
python3 tests/test_trade_calendar.py   # 交易日历测试
python3 tests/test_preopen_guard.py    # 盘前守卫测试

# 冒烟测试（部分用例需网络）
python3 tests/smoke_preopen.py

# Agent 调度器（事件路由与状态机演示）
python3 src/agent-orchestration/scheduler.py

# 持续学习系统演示
python3 src/self-improving/instinct_system.py --record "不要中英文混用" --context "之前回复"
python3 src/self-improving/instinct_system.py --evolve

# RAG 知识库构建 / 数据清洗 / 模型评测
python3 src/rag-knowledge-base/build_index.py
python3 src/dataset-cleaning/pipeline.py
python3 src/model-evaluation/framework.py
```

> 金融日报生产链路依赖 PostgreSQL 数据集与 OpenClaw 调度环境，不在本仓库复现；
> 其工程纪律见上方"核心设计纪律"与 `src/financial/` 代码注释。
