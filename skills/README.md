# Architecture Diagram Design Skill

> 设计清晰、准确、可读性强的系统架构图。不是"怎么画Mermaid"，是"怎么设计让人一眼看懂的架构"。

---

## 核心原则（5条）

1. **关系诚实** — 禁止纵向1:1对齐暗示不存在的因果关系
2. **共享服务用总线** — N×M条箭头 → 一条总线
3. **告警/监控独立** — 横切关注点，不是任何业务的子模块
4. **复杂度控制** — 单层≤7节点，整图≤15节点（Miller's Law）
5. **对比度优先** — 深色背景+白字，WCAG AA 4.5:1

---

## 结构（分层按需加载）

```
architecture-diagram-design/
├── SKILL.md                          # 主orchestrator + 决策树
├── guides/                           # 按需加载的指南
│   ├── principles.md                 # 核心设计原则（详细版）
│   ├── layout-patterns.md            # 5种布局模式
│   ├── color-contrast.md             # 颜色与对比度
│   └── anti-patterns.md              # 7种反模式与修复
├── scripts/                          # 实用脚本
│   ├── contrast_check.py             # WCAG对比度检查
│   └── complexity_check.py           # Mermaid复杂度检查
├── examples/                         # 完整示例
│   └── event-driven-system.md        # 事件驱动系统
└── templates/                        # 模板
    └── architecture-template.md      # 填空式模板
```

**Token优化**：只加载需要的guide（2-5KB），不加载整个skill。

---

## 快速开始

```bash
# 1. 检查你的架构图复杂度
python scripts/complexity_check.py your-diagram.md

# 2. 检查颜色对比度
python scripts/contrast_check.py --bg #21262d --fg #c9d1d9

# 3. 阅读反模式清单
cat guides/anti-patterns.md

# 4. 使用模板填空
cp templates/architecture-template.md my-architecture.md
```

---

## 决策树

```
用户请求
  ├── "帮我设计一个XX系统的架构图"
  │     → 读 guides/principles.md
  │     → 读 guides/layout-patterns.md
  │     → 读 templates/architecture-template.md
  │
  ├── "这个架构图哪里有问题"
  │     → 读 guides/anti-patterns.md
  │     → 运行 scripts/complexity_check.py
  │
  ├── "这个颜色在GitHub上能看清吗"
  │     → 读 guides/color-contrast.md
  │     → 运行 scripts/contrast_check.py
  │
  └── "我想看一个事件驱动系统的例子"
        → 读 examples/event-driven-system.md
```

---

## 踩坑档案

| 时间 | 坑 | 教训 |
|------|-----|------|
| 2026-06-26 | PIL生成架构图 | 字体/对齐/分辨率全是问题，用Mermaid |
| 2026-06-26 | 纵向1:1映射 | 平级模块别画成父子，用总线 |
| 2026-06-26 | `#f9f`浅粉+黑字 | 对比度灾难，GitHub dark mode看不清 |
| 2026-06-26 | 告警嵌套在心跳下 | 监控是横切关注点，和消息/心跳/定时平级 |
| 2026-06-26 | 单图17个节点 | 超过Miller's Law，拆图或抽象 |

---

## 对比GitHub上的Mermaid Skill

| | GitHub通用Skill | 本Skill |
|--|----------------|---------|
| 定位 | Mermaid语法教学 | 架构图设计原则 |
| 内容 | classDef/subgraph/Unicode | 关系诚实/共享服务/告警位置 |
| 工具 | 语法验证 | 对比度检查/复杂度检查 |
| 场景 | 任何Mermaid图 | 系统架构图 |
| 目标 | 语法正确 | 语义正确+可读性强 |

**互补关系**：本Skill负责"设计正确"，GitHub Skill负责"语法正确"。
