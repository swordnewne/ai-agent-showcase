# Graph Report - .  (2026-07-17)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 584 nodes · 943 edges · 44 communities (41 shown, 3 thin omitted)
- Extraction: 99% EXTRACTED · 1% INFERRED · 0% AMBIGUOUS · INFERRED: 8 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `9f590b23`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- launcher.py
- signal_pipeline.py
- PortfolioTracker
- DataProvider
- MarketContextService
- SignalTracker
- StockAnalyzer
- AgentOrchestrator
- meyo_post_daily.py
- meyo_post_daily.py
- InventoryPredictor
- ModelEvaluator
- daily_signal_review.py
- meyo_ab_tracker.py
- news_fetcher.py
- KnowledgeBase
- signal_comparison.py
- DataCleaner
- firsthand_news_fetcher.py
- instinct_system.py
- import_portfolio.py
- LLMClient
- news_analyzer.py
- fetch_joinquant_captcha.py
- update_portfolio_value_akshare.py
- health_check.py
- add_signal.py
- fetch_joinquant.py
- update_portfolio_value.py
- disk_guard.py
- detect_correction.py
- fetch_joinquant_full.py
- joinquant_push_module.py
- content_generator.py
- jq_login_guard.py
- fetch_joinquant_api.py
- fetch_joinquant_cookie.py
- fetch_joinquant_v2.py
- contrast_check.py
- complexity_check.py
- alert_pusher.py
- daily_portfolio_update.sh
- setup_cron.sh

## God Nodes (most connected - your core abstractions)
1. `PortfolioTracker` - 23 edges
2. `MarketContextService` - 20 edges
3. `SignalTracker` - 20 edges
4. `DataProvider` - 17 edges
5. `run_realtime_scan()` - 15 edges
6. `TradingSystemLauncher` - 14 edges
7. `StockAnalyzer` - 14 edges
8. `AgentOrchestrator` - 11 edges
9. `DataCleaner` - 10 edges
10. `main()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `main()` --calls--> `get_launcher()`  [INFERRED]
  scripts/daily_run.py → src/financial/launcher.py
- `PortfolioOversellError` --uses--> `DataProvider`  [INFERRED]
  src/financial/portfolio_tracker.py → src/financial/data_provider.py
- `PortfolioTracker` --uses--> `DataProvider`  [INFERRED]
  src/financial/portfolio_tracker.py → src/financial/data_provider.py
- `TradingSystemLauncher` --uses--> `PortfolioTracker`  [INFERRED]
  src/financial/launcher.py → src/financial/portfolio_tracker.py
- `TradingSystemLauncher` --uses--> `SignalTracker`  [INFERRED]
  src/financial/launcher.py → src/financial/signal_tracker.py

## Import Cycles
- None detected.

## Communities (44 total, 3 thin omitted)

### Community 0 - "launcher.py"
Cohesion: 0.06
Nodes (22): main(), # TODO: 接入企业微信推送, # TODO: 实现单股测试, calculate_position(), get_kelly_calculator(), KellyPositionCalculator, Any, 凯利公式仓位计算器          凯利公式: f = (p * b - q) / b     其中:         p = 胜率 (如 0.6) (+14 more)

### Community 1 - "signal_pipeline.py"
Cohesion: 0.10
Nodes (33): check_index_anomaly(), check_limit_up_down(), check_news_signals(), check_price_move(), _default_config(), _default_stock_pool(), _extract_code_from_title(), _extract_price_from_content() (+25 more)

### Community 2 - "PortfolioTracker"
Cohesion: 0.13
Nodes (10): Exception, PortfolioOversellError, PortfolioTracker, Any, date, 记录送股/拆股                  Args:             split_ratio: 拆股比例，如 1.5 表示 10送5（每10股变, 回放计算持仓快照                  Args:             as_of_date: 截止日期，默认今天             co, 持仓追踪器          使用事件溯源（Event Sourcing）：     - 不直接存状态，只存事件     - 每天回放事件计算当前持仓 (+2 more)

### Community 3 - "DataProvider"
Cohesion: 0.13
Nodes (12): DataProvider, get_data_provider(), Any, 混合数据源提供者          实时行情 -> 新浪API（秒级，轻量）     历史K线 -> akshare（前复权）     基本面 -> aksha, 获取基本面数据（接入fundamental_data模块）, 获取个股分析所需的全部数据                  Returns:             {                 "spot": 实时, 新浪API获取个股实时行情                  URL格式: https://hq.sinajs.cn/list=sh600519, FundamentalDataProvider (+4 more)

### Community 4 - "MarketContextService"
Cohesion: 0.18
Nodes (7): get_market_context_service(), get_prompt_section(), get_today_context(), MarketContextService, Any, date, 使用规则生成大盘摘要（AI不可用时fallback）

### Community 5 - "SignalTracker"
Cohesion: 0.14
Nodes (9): Any, 验证单个信号的结果                  Args:             signal_id: 信号ID             future_, 批量验证所有待验证信号                  由定时任务调用（如每天收盘后）, 信号追踪器          流程：     1. AI分析 → 生成 DecisionSignal（含目标价/止损价）     2. 保存到数据库     3, 获取未来N天行情数据                  TODO: 接入akshare或本地数据库, 统计AI信号准确率                  Returns:             {                 "total_signals, 获取AI当前信任权重（0.0-1.0）                  基于近期信号准确率动态调整：         - 胜率>60% → 权重1.0, 创建交易信号                  Args:             stock_code: 股票代码             stock_nam (+1 more)

### Community 6 - "StockAnalyzer"
Cohesion: 0.13
Nodes (12): get_analyzer(), Any, quick_analyze(), 趋势评分（0-20）                  评分标准：         - MA5>MA10>MA20 多头排列: +8         - 价格在, 估值评分（0-15）                  评分标准：         - PE分位<30%（低估）: +5         - PE分位30-70, 个股100分综合评分器          六维度评分：     - 趋势得分 (20分): MA多头排列、价格位置     - 估值得分 (15分): PE/P, 资金评分（0-15）                  评分标准：         - 近5日主力资金净流入: +5         - 成交量较20日均量放大, 基本面评分（0-20）                  评分标准：         - 营收增速>20%: +5         - 净利润增速>20%: + (+4 more)

### Community 7 - "AgentOrchestrator"
Cohesion: 0.13
Nodes (6): Enum, AgentOrchestrator, EventType, Agent调度器：事件路由 + 任务队列 + 状态管理, Task, TaskState

### Community 8 - "meyo_post_daily.py"
Cohesion: 0.20
Nodes (18): api_call(), collect_materials(), extract_date_from_path(), generate_candidates(), generate_content(), generate_title(), is_duplicate(), load_credentials() (+10 more)

### Community 9 - "meyo_post_daily.py"
Cohesion: 0.20
Nodes (18): api_call(), collect_materials(), extract_date_from_path(), generate_candidates(), generate_content(), generate_title(), is_duplicate(), load_credentials() (+10 more)

### Community 10 - "InventoryPredictor"
Cohesion: 0.13
Nodes (9): InventoryPredictor, DataFrame, 用户评论情感分析          业务痛点：     - 纸品评论量大（年用户1亿+），人工看不过来     - 差评分散在不同平台（天猫/拼多多/抖音）, 供应链优化：前置仓库存分配          业务痛点：     - 100+前置仓，各区域需求差异大     - 调货成本高，库存积压和缺货并存, 优化库存分配                  目标：最小化总成本 = 缺货损失 + 调货成本 + 库存持有成本, 纸品库存预测器          业务痛点：     - 纸品是快消品，销量波动大（双11、春节、疫情等）     - 库存过多→仓储成本增加；库存不足→断货损, 预测未来销量                  算法：移动平均 + 季节因子 + 趋势外推         （实际业务中可替换为Prophet/LSTM/ARI, ReviewAnalyzer (+1 more)

### Community 11 - "ModelEvaluator"
Cohesion: 0.16
Nodes (10): mock_model_v1(), mock_model_v2(), mock_model_v3(), ModelEvaluator, ModelResult, 幻觉检测：输出中是否包含输入未提及的信息                  简单实现：检查输出中的实体是否在输入中出现过         实际业务中可用NER+, 运行完整评测                  model_fn: 函数，接收news_title返回ModelResult, 模拟模型v2：更 nuanced 的分析（升级版） (+2 more)

### Community 12 - "daily_signal_review.py"
Cohesion: 0.18
Nodes (16): evaluate_signal_quality(), _find_workspace(), get_next_trading_date(), get_signals(), get_stock_close_price(), _load_spot_cache(), main(), _normalize_code() (+8 more)

### Community 13 - "meyo_ab_tracker.py"
Cohesion: 0.24
Nodes (15): api_call(), check_pending_retros(), do_retro(), fetch_post_metrics(), generate_title_variants(), get_best_title_type(), load_credentials(), load_data() (+7 more)

### Community 14 - "news_fetcher.py"
Cohesion: 0.25
Nodes (14): ai_analyze_all(), apply_keyword_override(), dedup_news(), ensure_dirs(), fetch_gov(), fetch_sina(), fetch_wsj(), fetch_xinhua() (+6 more)

### Community 15 - "KnowledgeBase"
Cohesion: 0.17
Nodes (5): KnowledgeBase, 检索知识库                  返回：相关文档列表，含相似度分数, 评测检索质量                  test_queries: [(query, expected_keyword), ...]         返, RAG知识库：Embedding + 向量索引 + 元数据, 文档分块：滑动窗口策略                  为什么分块：         - 单篇文档可能很长，Embedding有长度限制         -

### Community 16 - "signal_comparison.py"
Cohesion: 0.22
Nodes (13): compare_strategies(), compute_metrics(), format_report(), load_hardcoded_trades(), load_market_benchmark(), load_signal_trades(), main(), 从 sig_decision_signals 加载信号组交易记录 (+5 more)

### Community 17 - "DataCleaner"
Cohesion: 0.27
Nodes (4): DataCleaner, generate_mock_data(), DataFrame, 缺失值填充策略                  rules: {"字段名": "策略"}         策略：mean(均值)/median(中位数)/mo

### Community 18 - "firsthand_news_fetcher.py"
Cohesion: 0.32
Nodes (11): ai_analyze_news_batch(), dedup_news(), ensure_dirs(), fetch_csrc(), fetch_gov_cn(), fetch_pbc(), fetch_xinhua(), format_alert() (+3 more)

### Community 19 - "instinct_system.py"
Cohesion: 0.35
Nodes (11): ensure_dirs(), evolve_instincts(), extract_pattern(), generate_id(), get_status(), load_index(), main(), 将高置信度本能升级为 SKILL.md 规则片段 (+3 more)

### Community 20 - "import_portfolio.py"
Cohesion: 0.33
Nodes (10): generate_sample_portfolio(), get_db(), import_events(), init_db(), interactive_input(), main(), parse_joinquant_csv(), parse_json_positions() (+2 more)

### Community 21 - "LLMClient"
Cohesion: 0.27
Nodes (5): get_llm_client(), LLMClient, quick_chat(), 通用LLM客户端          环境变量优先级：     1. DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL     2. KI, 调用LLM                  Args:             prompt: 用户提示词             system_prompt

### Community 22 - "news_analyzer.py"
Cohesion: 0.24
Nodes (9): build_news_prompt(), calculate_shares(), kelly_position(), parse_signal(), 凯利公式计算最优仓位          f = (p*b - q) / b     其中 p=胜率, q=1-p, b=盈亏比          半凯利策略：f, 构建新闻分析Prompt          核心设计：给DeepSeek结构化输入，要求结构化输出。     避免"分析一下今天的新闻"这种模糊指令。, 解析LLM输出为结构化信号          容错设计：LLM可能输出markdown代码块，需要清洗。, 计算应买入股数（整数股）          关键修复：order_value不可靠（资金不足时无法成交），     改为order(股数)+3重价格兜底。 (+1 more)

### Community 23 - "fetch_joinquant_captcha.py"
Cohesion: 0.36
Nodes (8): find_slider_gap(), get_credentials(), human_like_drag(), login_and_fetch(), main(), 高级验证码处理：识别缺口 + 模拟人类拖动, 模拟人类拖动轨迹：带随机速度、抖动、加速/减速, solve_captcha_advanced()

### Community 24 - "update_portfolio_value_akshare.py"
Cohesion: 0.42
Nodes (8): calculate(), ensure_table(), fetch_all_prices(), get_positions(), get_price_akshare(), main(), print_result(), save_result()

### Community 25 - "health_check.py"
Cohesion: 0.54
Nodes (7): check_disk_space(), check_memory(), check_ssh_key(), check_task_health(), main(), 检查 GitHub SSH 密钥一致性          防止密钥路径变更、文件丢失、指纹不匹配导致推送失败。     规则：     - 私钥必须存在且指纹匹, record_self_healing()

### Community 26 - "add_signal.py"
Cohesion: 0.46
Nodes (7): add_signal(), batch_add(), ensure_table(), _find_workspace(), interactive_add(), main(), Find project root by looking for marker files

### Community 27 - "fetch_joinquant.py"
Cohesion: 0.36
Nodes (7): get_credentials(), _load_cookies_for_api(), login_joinquant(), main(), 加载Cookie用于API请求（requests格式）, 使用Playwright登录聚宽并获取持仓数据          Args:         fetch_logs: 是否同时抓取交易日志, save_to_db()

### Community 28 - "update_portfolio_value.py"
Cohesion: 0.46
Nodes (7): calculate(), ensure_table(), get_positions(), main(), print_result(), read_prices(), save_result()

### Community 29 - "disk_guard.py"
Cohesion: 0.46
Nodes (7): clean_old_reports(), clean_raw_data(), clean_sessions(), clear_alert(), get_disk_usage(), main(), write_alert()

### Community 30 - "detect_correction.py"
Cohesion: 0.43
Nodes (7): check_last_conversation(), extract_correction_pattern(), is_correction(), main(), 检查最近对话中的纠正（用于 HEARTBEAT）, 从纠正中提取旧做法 vs 新做法          尝试解析：       "不要 A，应该是 B" → 旧=A, 新=B       "不对，A 应该是 B", record_correction()

### Community 31 - "fetch_joinquant_full.py"
Cohesion: 0.48
Nodes (5): fetch_api(), main(), parse_logs(), parse_positions(), save_to_db()

### Community 32 - "joinquant_push_module.py"
Cohesion: 0.43
Nodes (6): push_daily_summary(), _push_data(), push_portfolio_snapshot(), push_trade(), 推送每日资产汇总（替代record_daily的print版）, 推送单笔交易记录          Args:         trade: 聚宽Trade对象（来自get_trades()）

### Community 33 - "content_generator.py"
Cohesion: 0.38
Nodes (5): collect_materials(), extract_quantified_data(), quality_gate(), 动态收集素材（6个来源）          核心改进：从硬编码4个模板 → 动态扫描132+条素材, 内容质量门          硬性门槛（必须全部通过）：     - 字数 > 200     - 包含量化数据     - 包含具体方案          软

### Community 34 - "jq_login_guard.py"
Cohesion: 0.53
Nodes (5): check_cookie_valid(), get_credentials(), login_and_save_cookie(), main(), 检测 Cookie 是否有效——通过实际API请求测试

### Community 35 - "fetch_joinquant_api.py"
Cohesion: 0.60
Nodes (3): fetch_logs(), main(), parse_trades_from_logs()

### Community 36 - "fetch_joinquant_cookie.py"
Cohesion: 0.50
Nodes (4): fetch_with_cookies(), _load_cookies_for_playwright(), main(), 加载Cookie：优先从文件读取，否则返回硬编码

### Community 37 - "fetch_joinquant_v2.py"
Cohesion: 0.70
Nodes (4): get_credentials(), login_and_fetch(), main(), solve_captcha()

### Community 38 - "contrast_check.py"
Cohesion: 0.70
Nodes (4): contrast_ratio(), hex_to_rgb(), main(), relative_luminance()

### Community 39 - "complexity_check.py"
Cohesion: 0.83
Nodes (3): analyze_mermaid(), main(), print_report()

## Knowledge Gaps
- **2 isolated node(s):** `daily_portfolio_update.sh script`, `setup_cron.sh script`
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PortfolioTracker` connect `PortfolioTracker` to `launcher.py`, `DataProvider`?**
  _High betweenness centrality (0.028) - this node is a cross-community bridge._
- **Why does `SignalTracker` connect `SignalTracker` to `launcher.py`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `PortfolioTracker` (e.g. with `TradingSystemLauncher` and `DataProvider`) actually correct?**
  _`PortfolioTracker` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `DataProvider` (e.g. with `PortfolioOversellError` and `PortfolioTracker`) actually correct?**
  _`DataProvider` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `daily_portfolio_update.sh script`, `setup_cron.sh script` to the rest of the system?**
  _2 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `launcher.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06292517006802721 - nodes in this community are weakly interconnected._
- **Should `signal_pipeline.py` be split into smaller, more focused modules?**
  _Cohesion score 0.10160427807486631 - nodes in this community are weakly interconnected._