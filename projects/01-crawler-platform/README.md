# 项目一：数据爬虫自动化平台（"小龙虾控制台"）

> **业务目标**：自动化采集全国数据交易所的产品信息，解决"数据分散、格式不统一、反爬严格"的痛点。

---

## 📈 核心成果

| 指标 | 数据 |
|------|------|
| 覆盖交易所 | **26家** |
| 累计采集产品 | **102,043条**（广州交易所单站） |
| 存储方案 | SQLite轻量总表 + source字段区分 |
| 反爬策略 | Playwright API拦截 + 随机指纹 + 流式写入 |

---

## 🏗️ 架构

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  调度中心    │────→│  Playwright  │────→│  API拦截    │
│  (cron)     │     │  浏览器池     │     │  提取JSON   │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
                                         ┌──────▼──────┐
                                         │  流式写入   │
                                         │  OOM防护    │
                                         └──────┬──────┘
                                                │
                                         ┌──────▼──────┐
                                         │  SQLite     │
                                         │  统一总表   │
                                         └─────────────┘
```

---

## 🎯 技术亮点

### 1. Playwright API 拦截（替代Puppeteer）

传统爬虫容易被反爬，改为**拦截前端API请求**直接拿JSON数据：

```python
# 核心逻辑：拦截XHR/Fetch，提取产品列表API
with page.expect_request(lambda req: "product" in req.url) as req_info:
    page.click("text=产品中心")
response = req_info.value.response()
data = response.json()  # 直接拿结构化数据
```

### 2. 流式写入 + OOM 防护

采集大数据量时避免内存爆炸：

```python
# 流式写入：每100条commit一次，不堆积内存
BATCH_SIZE = 100
buffer = []

for item in crawler.run():
    buffer.append(item)
    if len(buffer) >= BATCH_SIZE:
        db.executemany("INSERT INTO products ...", buffer)
        buffer.clear()
        time.sleep(random.uniform(3, 5))  # 随机间隔防反爬
```

### 3. 统一总表设计

拒绝过度工程化，一张表解决：

```sql
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    source TEXT,           -- 交易所标识
    name TEXT,
    category TEXT,
    price TEXT,
    provider TEXT,
    url TEXT,
    created_at TIMESTAMP
);
```

---

## 📂 踩坑档案（真实记录）

| 时间 | 问题 | 解决方案 |
|------|------|---------|
| 2026-05 | Puppeteer被反爬 | 全面迁移Playwright+API拦截 |
| 2026-05 | 10万+数据内存溢出 | 流式写入+BATCH_SIZE控制 |
| 2026-06 | Token过期导致404 | 动态拦截+自动刷新机制 |

---

## 🔧 运行方式

```bash
pip install playwright sqlite3
playwright install chromium
python crawler.py --exchange cantonde --output products.db
```

---

## 💡 为什么这个项目"硬"

- **不是调包**：从Puppeteer→Playwright的迁移是主动技术选型
- **有真实数据**：102,043条不是demo数据，是生产级采集量
- **解决真实问题**：数据分散、反爬、OOM都是真实业务痛点
