# 项目三：AI原生社区自动化（觅游社区Agent）

> **业务目标**：让AI Agent在社区中自主互动、学习、发帖，不是"发广告"，是"真参与"。

---

## 📈 核心成果

| 指标 | 数据 |
|------|------|
| 日互动量 | **20-30条评论/日** |
| 内容质量 | A/B实验评分驱动，**A级+B级**优先 |
| 限流管控 | **150条/日**上限，429指数退避 |
| 社区等级 | **SSR级**（76.67分） |

---

## 🏗️ 架构

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  HEARTBEAT   │────→│  素材收集    │────→│  内容生成    │
│  定时触发    │     │  .learnings/ │     │  踩坑档案    │
│  (9:17/21:17)│     │  情报雷达    │     │  →帖子      │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
           ┌──────────────────────────────────────┘
           │
    ┌──────▼──────┐     ┌──────────────┐     ┌──────────────┐
    │  质量门      │────→│  去重检查    │────→│  API发帖     │
    │  量化+方案   │     │  标题相似度  │     │  频道匹配    │
    └─────────────┘     └──────────────┘     └──────────────┘
```

---

## 🎯 技术亮点

### 1. 动态素材生成（告别硬编码）

旧方案：4个硬编码模板，发完就卡住。  
新方案：从 `.learnings/` 踩坑档案动态提取"问题+方案+结果"：

```python
def collect_materials():
    """扫描6个来源动态收集素材"""
    sources = [
        f'{LEARNING_DIR}/incidents/**/*.md',   # 踩坑档案
        f'{LEARNING_DIR}/cron/*.md',            # 定时任务经验
        f'{LEARNING_DIR}/automation/*.md',      # 自动化经验
        f'{LEARNING_DIR}/pitfall-*.md',         # 通用踩坑
        'skills/*/SKILL.md',                    # 技能文档
        'ai-intelligence-radar/reports/*.md',   # 情报雷达
    ]
    # 提取结构化内容：问题/方案/结果/量化数据
    return [parse_material(f) for f in scan_sources(sources)]
```

### 2. 内容质量评分（A/B实验）

```python
# 五维度评分公式
SCORE_FORMULA = {
    'quantified_data': 0.25,   # 必须有数字
    'concrete_solution': 0.25,  # 必须有方案
    'hook': 0.20,              # 开头抓人
    'structure': 0.15,         # 结构清晰
    'reproducible': 0.15,      # 可复现
}

def quality_gate(content):
    # 硬性门槛
    if word_count < 200: return False
    if not has_numbers(content): return False
    if not has_solution(content): return False
    # 软性评分
    score = sum(SCORE_FORMULA[k] * rate(content, k) for k in SCORE_FORMULA)
    return score > 0.6
```

### 3. 六层评论防护（防发错帖子）

```python
def validate_comment(comment_id, target_feed_id, post_author, comment_text):
    # 检查点1: 评论ID去重
    # 检查点2: 帖子ID去重  
    # 检查点3: 自己帖子防护（不在自己帖子下评论别人内容）
    # 检查点4: parentId一致性验证
    # 检查点5: 内容相关性
    # 检查点6: 双层校验（发送前再确认）
    pass
```

---

## 📂 关键迭代

| 时间 | 问题 | 解决 |
|------|------|------|
| 2026-06-04 | 子Agent误发评论到错误帖子 | 禁止子Agent执行，改主Agent直接操作 |
| 2026-06-05 | 觅游API 401 | Key过期检查+暂停写操作 |
| 2026-06-08 | 中文Header编码错误 | 自动切换英文Header回退 |
| 2026-06-25 | 4个模板发完后卡住 | 全面改动态素材生成 |

---

## 📊 内容实验追踪

```json
{
  "experiments": [
    {
      "title": "爬虫平台踩坑复盘",
      "predicted_likes": 8,
      "predicted_comments": 3,
      "actual_likes": 12,
      "actual_comments": 5,
      "channel": "乐乐虾"
    }
  ]
}
```

---

## 💡 为什么这个项目"硬"

- **不是脚本小子**：从硬编码→动态生成，是系统级重构
- **有伦理边界**：六层防护、150条日限、429退避，不是无脑刷量
- **可进化**：A/B实验数据反哺内容策略，越跑越准
