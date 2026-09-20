#!/bin/bash
# 金融信号预警系统 - Cron配置 (2026-06-26)
# 替换旧金融新闻系统（四层兜底版已废弃）

echo "=== 配置金融信号预警系统定时任务 ==="

FINANCE_DIR="/root/.openclaw/workspace/showcase/src/financial"

# 先清理旧系统
crontab -l | grep -v '/tmp/financial_news' | grep -v '金融新闻AI信号系统' | grep -v '四层兜底' | grep -v 'guard.sh' | grep -v 'sentinel.py' | grep -v 'update_market_data.py' > /tmp/crontab_clean

# 添加新系统
cat >> /tmp/crontab_clean << 'EOF'

# ====== 金融信号预警系统 (signal_pipeline) ======
# 交易时段每5分钟: 实时行情异动检测（涨幅≥5% + 成交额≥10亿 / 涨跌停 / 大盘异动±2%）
*/5 9-15 * * 1-5 python3 /root/.openclaw/workspace/showcase/src/financial/signal_pipeline.py --mode realtime >> /tmp/finance_signal.log 2>&1
# 收盘后15:05: 日终总结（大盘 + 涨跌停 + 涨跌幅TOP5）
5 15 * * 1-5 cd /root/.openclaw/workspace/showcase/src/financial && python3 signal_pipeline.py --mode summary >> /tmp/finance_signal.log 2>&1
# 盘前8:30: 系统预热（初始化缓存）
30 8 * * 1-5 cd /root/.openclaw/workspace/showcase/src/financial && python3 launcher.py warmup >> /tmp/finance_signal.log 2>&1 || true

EOF

crontab /tmp/crontab_clean

echo "✅ 配置完成"
echo ""
echo "定时任务："
echo "  • 8:30   盘前预热"
echo "  • 9:00-15:00 每5分钟 实时检测"
echo "  • 15:05  收盘总结"
echo ""
echo "日志: /tmp/finance_signal.log"
echo "缓存: /tmp/finance_sent_cache.json"
echo "待推送: /tmp/pending_alerts.json"
echo ""
echo "手动测试："
echo "  cd $FINANCE_DIR && python3 launcher.py realtime"
echo "  cd $FINANCE_DIR && python3 launcher.py summary"
