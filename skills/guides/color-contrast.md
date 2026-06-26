# 颜色与对比度

> 颜色不是装饰，是信息。对比度不够=信息丢失。

---

## 核心规则

### 规则1：深色背景+浅色文字（GitHub Dark Mode）

GitHub默认Dark Mode的阅读环境：
- 背景：`#0d1117`
- 默认文字：白色

**所以**：
```
❌ 浅色方块 + 默认黑字 = 在GitHub上白字看不清
✅ 深色方块 + 白字 = 在GitHub上清晰可读
```

### 规则2：WCAG 2.1 对比度标准

| 等级 | 对比度 | 用途 |
|------|--------|------|
| AA | 4.5:1 | 正文文字（最小标准） |
| AAA | 7:1 | 小号文字（最佳标准） |

**计算公式**：
```
对比度 = (L1 + 0.05) / (L2 + 0.05)
其中 L = 相对亮度
```

### 规则3：色盲友好

不要只用颜色区分状态，要配合：
- 形状（圆角/直角）
- 边框样式（实线/虚线）
- 文字标注

```
❌ 红色=失败，绿色=成功（红绿色盲看不清）
✅ 红色+❌=失败，绿色+✅=成功
```

---

## 推荐配色（GitHub Dark Mode优化）

### 主色调

| 用途 | 背景色 | 文字色 | 对比度 |
|------|--------|--------|--------|
| 事件/输入 | `#1f6feb` (蓝) | `#ffffff` | 4.6:1 ✅ |
| 处理/路由 | `#f0883e` (橙) | `#000000` | 4.7:1 ✅ |
| 业务模块 | `#3fb950` (绿) | `#000000` | 5.1:1 ✅ |
| 数据服务 | `#a371f7` (紫) | `#000000` | 4.8:1 ✅ |
| 基础设施 | `#484f58` (灰) | `#ffffff` | 4.5:1 ✅ |
| 存储 | `#21262d` (深灰) | `#c9d1d9` | 7.2:1 ✅ |

### Mermaid classDef定义

```mermaid
classDef event fill:#1f6feb,stroke:#fff,color:#fff
classDef router fill:#f0883e,stroke:#fff,color:#000
classDef business fill:#3fb950,stroke:#fff,color:#000
classDef data fill:#a371f7,stroke:#fff,color:#000
classDef infra fill:#484f58,stroke:#fff,color:#fff
classDef storage fill:#21262d,stroke:#8b949e,color:#c9d1d9
```

---

## 禁用颜色

| 颜色 | 问题 | 替代 |
|------|------|------|
| `#f9f` (浅粉) | 对比度<2:1 | `#a371f7` (紫) |
| `#bbf` (浅蓝) | 对比度<2:1 | `#1f6feb` (蓝) |
| `#ffff00` (纯黄) | 刺眼 | `#d29922` (琥珀) |
| 纯红 `#ff0000` | 警告感过强 | `#f85149` (GitHub红) |

---

## 对比度检查工具

### 在线工具
- [WebAIM Contrast Checker](https://webaim.org/resources/contrastchecker/)
- [Coolors Contrast Checker](https://coolors.co/contrast-checker)

### 本地脚本

```bash
# 检查两个颜色的对比度
python scripts/contrast_check.py --bg #21262d --fg #c9d1d9
```

---

## 实际踩坑记录

| 时间 | 错误 | 后果 | 修复 |
|------|------|------|------|
| 2026-06-26 | `style C fill:#f9f` | GitHub dark mode下文字几乎看不见 | 改用 `#a371f7` |
| 2026-06-26 | `style E fill:#bbf` | 同上 | 改用 `#1f6feb` |
| 2026-06-26 | PIL生成图用浅灰背景 | 打印和屏幕都看不清 | 改用 `#0d1117` 深色背景 |
