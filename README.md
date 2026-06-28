# Showcase 股票交易系统 - 构建状态

## 当前完成度

### 核心模块（全部完成）

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 数据库 | data/db_schema.sql | ✅ | 6张表已初始化 |
| 数据适配 | data_provider.py | ✅ | 新浪API实时 + akshare历史K线 |
| 大盘上下文 | market_context.py | ✅ | 真实指数数据 + 规则化摘要 |
| AI客户端 | ai_client.py | ✅ | 支持Kimi/DeepSeek/OpenAI |
| 持仓追踪 | portfolio_tracker.py | ✅ | FIFO成本 + 防超卖 + 快照 |
| 评分器 | stock_analyzer.py | ✅ | 6维度100分制 |
| 凯利仓位 | kelly_position.py | ✅ | 大盘环境调整 + 硬约束 |
| 信号追踪 | signal_tracker.py | ✅ | 创建→验证→准确率统计 |
| 报告生成 | report_generator.py | ✅ | 纯文本微信友好格式 |
| 统一调度 | launcher.py | ✅ | 盘前/盘后一键执行 |
| 定时脚本 | scripts/daily_run.py | ✅ | cron就绪 |
| 股票池 | config/stock_pool.yaml | ✅ | 20只持仓已配置 |

### 当前已能产生真实数据驱动的报告

```bash
# 测试盘前分析（5只股票示例）
cd /root/.openclaw/workspace/showcase
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace')
from showcase.src.financial.launcher import get_launcher
launcher = get_launcher()
launcher.stock_pool = launcher.stock_pool[:5]  # 只跑前5只
print(launcher.cmd_premarket())
"
```

**输出示例**（2026-06-27 周六）：
- 大盘：上证指数 4027点，跌2.26%，情绪恐慌，建议轻仓（3成以内）
- 茅台：评分1.9/100（D级），MACD死叉，RSI 31.83，建议回避
- 宁德时代：评分2.5/100（D级），趋势空头排列

### 数据源状态

| 数据类型 | 来源 | 状态 | 备注 |
|----------|------|------|------|
| 实时价格 | 新浪API | ✅ 可用 | 需Referer头 |
| 历史K线 | akshare | ✅ 可用 | 前复权 |
| MA/MACD/RSI | 本地计算 | ✅ 可用 | 基于历史K线 |
| 换手率 | ❌ 缺失 | 新浪不返回 | 待补充 |
| PE/PB/ROE | ❌ 缺失 | 待接入akshare | 待补充 |
| 营收/利润增速 | ❌ 缺失 | 需财报数据 | 待补充 |
| 大盘AI摘要 | 规则化 | ✅ 可用 | AI key 401，已fallback |

### 已知问题

1. **LLM API 401**: Kimi key 无效，大盘摘要使用规则化生成（基于涨跌幅）
2. **基本面数据缺失**: PE/PB/ROE/增速均为空，导致评分器"估值"和"基本面"维度得分偏低
3. **持仓为0**: portfolio_events表为空，需录入历史交易

### 下一步建议

**P0（立刻做）**：
- 录入聚宽模拟盘历史交易 → 持仓快照才能正确计算

**P1（近期）**：
- 补充akshare基本面接口（PE/PB/ROE）
- 修复LLM API key或切换到DeepSeek
- 接入换手率数据

**P2（优化）**：
- 企业微信推送集成
- 盘后复盘自动验证信号
- 新闻舆情接入评分
