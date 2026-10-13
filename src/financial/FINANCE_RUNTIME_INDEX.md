# 金融任务运行架构索引

> 验证时间：2026-10-09（Asia/Shanghai）。此索引服务 Niko 定位真实入口；代码和实时 cron 配置是事实来源。

## 交易日历

`data/trade_calendar.json` 保存 2026 年逐日 `calendar_date` / `is_trading_day` 状态、日历版本、上交所来源 URL、抓取时间和响应哈希。`trade_calendar.is_trading_day()` 是报告、BaoStock 同步和连涨计算共用的读取接口；JSON 未覆盖年份或年度记录不完整时显式报错，不推测交易状态。上交所尚未发布的下一年度安排由维护者发布后手动执行 `python3 showcase/src/financial/trade_calendar.py --refresh --year YYYY` 更新。报告运行不自动联网刷新日历。

## 实际调度

| 任务 | 时间 | 类型 | 入口 | 作用 |
|---|---:|---|---|---|
| BaoStock行情-盘后增量 | 工作日 18:10 | OpenClaw cron command，isolated | `/mnt/c/Users/smk/Documents/ChatGPT/股票/finance_p0a1/baostock_daily_sync_job.py` | 读取数据库最新交易日，补齐 BaoStock staging，写入 `finance.daily_bars` 与证券元数据 |
| 金融报告-早间 | 工作日 08:15 | OpenClaw cron command，isolated | `showcase/src/financial/preopen_guard.py --slot 08:15` | 运行报告、投递主对话 |
| 金融报告-晚间重试 | 工作日 22:00 | OpenClaw cron command，isolated | `showcase/src/financial/preopen_guard.py --slot 22:00` | 重试报告生成或投递 |

## 报告调用链

`preopen_guard.execute` → `preopen_report.run` → `fetch_news_24h`（读取四来源快照，缺失时保留受限新浪回退） → `call_deepseek`（新闻/盘前数据分析） → `rising_streak.fetch_candidates`（只读 BaoStock PostgreSQL 历史库） → Markdown 报告 → `preopen_guard.deliver`（`chat.inject` 后读回确认）。

当前 cron payload 是 `command`，不是 `agentTurn`；因此调度脚本、DeepSeek 分析和固定行情公式实际执行评分，不能表述为 Niko 本体在 cron 回合中自主推理评分。报告写入主对话只是投递动作。

## BaoStock 入库调用链

`baostock_daily_sync_job.main` → `baostock_daily_update.run` → `baostock_staging_sync.run_staging_sync`（日线 staging） + `baostock_metadata_sync.run_metadata_sync`（证券快照） → 校验响应哈希与 manifest → PostgreSQL `finance.daily_bars` / `finance.securities`。

## 查询入口

Niko 主图谱：`/root/.openclaw/workspace/graphify-out/graph.json`。

施工代码（Windows）：`/mnt/c/Users/smk/Documents/ChatGPT/股票/finance_p0a1/` 和 `/mnt/c/Users/smk/Documents/ChatGPT/股票/baostock-materials/`。这些外部源通过绝对来源路径进入主图谱，不复制成第二套实现。

## 事实边界

同花顺/BaoStock 双线对比和连板筛选尚未实施；Niko `agentTurn` 评分链已接入，但需要模型连接可用才能完成最终回执。

## 当前生效的评分链（2026-10-09）

旧表格中的报告任务类型已被下面配置覆盖：

- 08:05：金融报告-早间准备，command，执行 `preopen_guard.py --slot 08:15 --prepare-for-niko --force`。
- 08:15：金融报告-早间，agentTurn，agent `main`；只读取 decision_packets，由 Niko 最终评分并执行 `--apply-niko`。
- 21:50：金融报告-晚间准备，command，执行 `preopen_guard.py --slot 22:00 --prepare-for-niko --force`。
- 22:00：金融报告-晚间重试，agentTurn，agent `main`；只读取 decision_packets，由 Niko 最终评分并执行 `--apply-niko`。
- 18:10 BaoStock 行情同步仍保持原 command，不受本次职责切换影响。

准备阶段写入带 packet_hash 的评分交接包；Niko 回执必须引用相同 hash、只包含已有候选、分数在 0 到 100 且包含理由。回执通过后才追加最终评分和投递主对话。

实时验收：2026-10-09 22:00 准备链和 Niko 评分均成功，104 条新闻、DeepSeek 分析完成、BaoStock 初选 472 只；使用精简评分输入复用完整 packet，Niko 回执通过完整 packet hash、候选和分数校验，报告追加最终评分并完成主会话读回。packet 为 /root/.openclaw/workspace/reports/preopen/decision_packets/finance_2026-10-09_2200.json，回执为 /root/.openclaw/workspace/reports/preopen/decision_receipts/finance_2026-10-09_2200.json。早晚评分 cron 已显式绑定 deepseek/deepseek-flash，备用 deepseek/deepseek-v4-pro；router-one 超时仅保留在历史运行记录中。
