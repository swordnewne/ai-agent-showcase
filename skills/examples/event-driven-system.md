# 示例：事件驱动系统

> 这个示例展示了如何用分层布局+总线布局设计一个事件驱动的Agent系统。

---

## 系统描述

- **事件源**：用户消息、定时触发、系统告警、心跳信号
- **路由器**：事件分发到不同处理器
- **处理器**：对话、情报雷达、社区学习、系统监控
- **数据层**：RAG知识库、数据清洗、模型评测、分层记忆（共享服务）
- **存储**：SQLite、JSON、FAISS、配置文件

---

## 架构图

```mermaid
graph TD
    %% 事件层
    A[👤 用户消息]
    B[⏰ 定时触发]
    C[🚨 系统告警]
    D[💓 心跳信号]
    
    %% 路由器
    R{⚡ 事件路由器}
    
    %% 执行层
    E1[💬 对话处理器]
    E2[📡 AI情报雷达]
    E3[🌐 社区学习]
    E4[🔧 系统监控]
    
    %% 共享数据层
    DS{📦 共享数据层}
    
    %% 数据服务
    S1[🔍 RAG知识库]
    S2[🧹 数据清洗]
    S3[📊 模型评测]
    S4[🧠 分层记忆]
    
    %% 存储
    DB1[(💾 SQLite)]
    DB2[(📁 JSON)]
    DB3[(⚡ FAISS)]
    DB4[(⚙️ 配置)]
    
    %% 连接
    A --> R
    B --> R
    C --> R
    D --> R
    
    R --> E1
    R --> E2
    R --> E3
    R --> E4
    
    E1 --> DS
    E2 --> DS
    E3 --> DS
    E4 --> DS
    
    DS --> S1
    DS --> S2
    DS --> S3
    DS --> S4
    
    S1 --> DB1
    S2 --> DB2
    S3 --> DB3
    S4 --> DB4
    
    %% 样式
    classDef event fill:#1f6feb,stroke:#fff,color:#fff
    classDef router fill:#f0883e,stroke:#fff,color:#000
    classDef exec fill:#3fb950,stroke:#fff,color:#000
    classDef data fill:#a371f7,stroke:#fff,color:#000
    classDef storage fill:#21262d,stroke:#8b949e,color:#c9d1d9
    
    class A,B,C,D event
    class R router
    class E1,E2,E3,E4 exec
    class DS,S1,S2,S3,S4 data
    class DB1,DB2,DB3,DB4 storage
```

---

## 设计说明

### 为什么这样布局

| 层级 | 节点数 | 布局理由 |
|------|--------|---------|
| 事件层 | 4 | 平级，都是输入源 |
| 路由器 | 1 | 单一职责：分发 |
| 执行层 | 4 | 平级处理器，互不隶属 |
| 数据层 | 4 | 共享服务，通过总线连接 |
| 存储层 | 4 | 各服务独立存储 |

### 关键设计决策

1. **告警独立**：系统告警和心跳/定时/消息平级，都是事件源
2. **共享数据总线**：4个处理器都连到同一个数据层节点，避免意大利面
3. **emoji增强**：每个节点带语义emoji，色盲也能区分
4. **颜色编码**：蓝=事件、橙=路由、绿=执行、紫=数据、灰=存储

---

## 复杂度检查

```bash
python scripts/complexity_check.py examples/event-driven-system.md
```

预期输出：
```
图 #1:
  节点数: 17 ⚠️ 建议<15
  边数: 20
  子图: 0
  问题:
    - 节点过多，建议拆图或抽象
```

**优化方案**：把数据服务和存储层合并表示：

```mermaid
graph TD
    A[👤 用户消息] --> R{⚡ 事件路由器}
    B[⏰ 定时触发] --> R
    C[🚨 系统告警] --> R
    D[💓 心跳信号] --> R
    
    R --> E1[💬 对话]
    R --> E2[📡 情报雷达]
    R --> E3[🌐 社区学习]
    R --> E4[🔧 系统监控]
    
    E1 --> DS{📦 数据层}
    E2 --> DS
    E3 --> DS
    E4 --> DS
    
    DS --> S1[🔍 RAG知识库]
    DS --> S2[🧹 数据清洗]
    DS --> S3[📊 模型评测]
    DS --> S4[🧠 分层记忆]
    
    classDef event fill:#1f6feb,stroke:#fff,color:#fff
    classDef router fill:#f0883e,stroke:#fff,color:#000
    classDef exec fill:#3fb950,stroke:#fff,color:#000
    classDef data fill:#a371f7,stroke:#fff,color:#000
    
    class A,B,C,D event
    class R router
    class E1,E2,E3,E4 exec
    class DS,S1,S2,S3,S4 data
```

节点数从17降到13，满足<15规则。
