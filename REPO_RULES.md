# ai-agent-showcase 仓库规则

## 定位
对外展示 AI Agent 工程能力的作品集仓库。

## 禁止提交的内容

- ❌ 硬编码的账号、密码、API Key、回测ID
- ❌ 内部运维脚本（如 Cookie 刷新、定时任务配置）
- ❌ 临时修复补丁
- ❌ 个人记忆文件、日记
- ❌ 未经验证的实验性代码

## 允许提交的内容

- ✅ 核心功能代码（去除了硬编码凭据，使用环境变量）
- ✅ README、文档、使用说明
- ✅ 可运行的 Demo 和示例
- ✅ 测试用例
- ✅ 架构图、流程图

## 提交前检查清单

```bash
# 1. 确认当前仓库
git remote get-url origin
# 应该是：git@github.com:swordnewne/ai-agent-showcase.git

# 2. 检查硬编码敏感信息
git diff --cached | grep -i "password\|api_key\|token\|secret\|backtestId"
# 应该无输出

# 3. 确认代码属于展示范围
echo "这个提交是给潜在雇主/客户看的吗？"
```

## 凭据管理规范

所有凭据必须通过环境变量传入：

```python
import os

# ✅ 正确
backtest_id = os.environ.get("JQ_BACKTEST_ID")

# ❌ 错误
backtest_id = "<32位回测ID占位符>"
```
