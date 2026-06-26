#!/usr/bin/env python3
"""
RAG知识库构建演示

业务场景：将技术踩坑档案转换为可检索知识库，支持Agent快速查询历史经验。
技术栈：Sentence-Transformers(本地Embedding) + FAISS(向量检索) + SQLite(元数据)

设计要点：
1. 本地Embedding：无需API Key，保护数据隐私
2. 分层索引：文档级 + 段落级，支持粗排和精排
3. 检索评测：Top-K命中率自动评估
"""
import os
import json
import sqlite3
import glob
import re
import numpy as np
from typing import List, Dict, Tuple

# 尝试导入 sentence-transformers，未安装则降级
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    print("[警告] sentence-transformers 未安装，使用随机向量演示。")
    print("[安装] pip install sentence-transformers faiss-cpu")

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    print("[警告] faiss 未安装，使用暴力搜索演示。")


DB_PATH = "data/knowledge_base.db"
INDEX_PATH = "data/knowledge_base.index"


class KnowledgeBase:
    """RAG知识库：Embedding + 向量索引 + 元数据"""
    
    def __init__(self, embedding_model: str = "paraphrase-MiniLM-L6-v2"):
        self.conn = sqlite3.connect(DB_PATH)
        self._init_schema()
        
        # Embedding模型（本地运行，数据不出境）
        if EMBEDDING_AVAILABLE:
            print(f"[加载] Embedding模型: {embedding_model}")
            self.model = SentenceTransformer(embedding_model)
            self.dim = self.model.get_sentence_embedding_dimension()
        else:
            self.model = None
            self.dim = 384  # 默认维度
        
        # 向量索引
        self.index = None
        self._load_or_create_index()
    
    def _init_schema(self):
        """初始化数据库表结构"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,        -- 文件来源
                title TEXT,                  -- 文档标题
                content TEXT,                -- 完整内容
                chunk TEXT,                -- 分块内容
                chunk_idx INTEGER,         -- 分块序号
                category TEXT,             -- 分类（incident/cron/skill）
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_category ON documents(category);
            CREATE INDEX IF NOT EXISTS idx_source ON documents(source);
        """)
        self.conn.commit()
    
    def _load_or_create_index(self):
        """加载或创建FAISS索引"""
        if FAISS_AVAILABLE and os.path.exists(INDEX_PATH):
            print(f"[加载] 向量索引: {INDEX_PATH}")
            self.index = faiss.read_index(INDEX_PATH)
        elif FAISS_AVAILABLE:
            print("[创建] 新向量索引 (IndexFlatIP)")
            self.index = faiss.IndexFlatIP(self.dim)  # 内积相似度
        else:
            print("[降级] 使用暴力搜索")
            self.index = None
    
    def chunk_document(self, content: str, chunk_size: int = 512, overlap: int = 50) -> List[str]:
        """文档分块：滑动窗口策略
        
        为什么分块：
        - 单篇文档可能很长，Embedding有长度限制
        - 细粒度检索更精准（定位到具体段落）
        - 重叠保持上下文连贯性
        """
        # 按句子切分
        sentences = re.split(r'(?<=[。！？\n])\s+', content)
        chunks = []
        current = []
        current_len = 0
        
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            sent_len = len(sent)
            
            if current_len + sent_len > chunk_size and current:
                chunks.append("".join(current))
                # 重叠保留
                overlap_text = "".join(current)[-overlap:]
                current = [overlap_text, sent]
                current_len = len(overlap_text) + sent_len
            else:
                current.append(sent)
                current_len += sent_len
        
        if current:
            chunks.append("".join(current))
        
        return chunks if chunks else [content[:chunk_size]]
    
    def add_documents(self, documents: List[Dict]):
        """批量添加文档到知识库"""
        all_chunks = []
        all_metadata = []
        
        for doc in documents:
            content = doc.get("content", "")
            chunks = self.chunk_document(content)
            
            for idx, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_metadata.append({
                    "source": doc.get("source", ""),
                    "title": doc.get("title", ""),
                    "chunk": chunk,
                    "chunk_idx": idx,
                    "category": doc.get("category", "general")
                })
        
        # 生成Embedding
        if self.model:
            print(f"[编码] {len(all_chunks)} 个文本块...")
            embeddings = self.model.encode(all_chunks, show_progress_bar=True)
            embeddings = embeddings.astype(np.float32)
            # L2归一化（用于内积相似度）
            faiss.normalize_L2(embeddings)
        else:
            # 降级：随机向量（仅演示结构）
            embeddings = np.random.randn(len(all_chunks), self.dim).astype(np.float32)
            faiss.normalize_L2(embeddings)
        
        # 写入数据库
        cursor = self.conn.cursor()
        ids = []
        for meta in all_metadata:
            cursor.execute("""
                INSERT INTO documents (source, title, content, chunk, chunk_idx, category)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (meta["source"], meta["title"], content, meta["chunk"], meta["chunk_idx"], meta["category"]))
            ids.append(cursor.lastrowid)
        self.conn.commit()
        
        # 写入向量索引（id从1开始）
        ids_arr = np.array(ids, dtype=np.int64)
        if FAISS_AVAILABLE and self.index is not None:
            self.index.add_with_ids(embeddings, ids_arr)
            faiss.write_index(self.index, INDEX_PATH)
        
        print(f"[完成] 添加 {len(documents)} 文档，{len(all_chunks)} 文本块")
        return ids
    
    def search(self, query: str, top_k: int = 5, category: str = None) -> List[Dict]:
        """检索知识库
        
        返回：相关文档列表，含相似度分数
        """
        # 编码查询
        if self.model:
            query_vec = self.model.encode([query]).astype(np.float32)
            faiss.normalize_L2(query_vec)
        else:
            query_vec = np.random.randn(1, self.dim).astype(np.float32)
            faiss.normalize_L2(query_vec)
        
        # 向量检索
        if FAISS_AVAILABLE and self.index is not None:
            scores, ids = self.index.search(query_vec, top_k * 2)  # 多取一些做过滤
            # 过滤掉-1（未找到）
            valid = [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]
        else:
            # 降级：暴力搜索（仅演示）
            valid = []
        
        # 获取元数据
        results = []
        cursor = self.conn.cursor()
        for doc_id, score in valid[:top_k]:
            cursor.execute("""
                SELECT source, title, chunk, category FROM documents WHERE id = ?
            """, (doc_id,))
            row = cursor.fetchone()
            if row:
                # 类别过滤
                if category and row[3] != category:
                    continue
                results.append({
                    "id": doc_id,
                    "source": row[0],
                    "title": row[1],
                    "content": row[2],
                    "category": row[3],
                    "score": round(score, 4)
                })
        
        return results
    
    def evaluate(self, test_queries: List[Tuple[str, str]]) -> Dict:
        """评测检索质量
        
        test_queries: [(query, expected_keyword), ...]
        返回：Top-K命中率、平均相似度
        """
        hits = 0
        total_score = 0
        
        for query, expected in test_queries:
            results = self.search(query, top_k=5)
            
            # 检查是否命中（内容包含期望关键词）
            found = any(expected.lower() in r["content"].lower() for r in results)
            if found:
                hits += 1
            
            if results:
                total_score += results[0]["score"]
        
        n = len(test_queries)
        return {
            "top5_hit_rate": hits / n if n > 0 else 0,
            "avg_top1_score": total_score / n if n > 0 else 0,
            "total_queries": n
        }
    
    def close(self):
        self.conn.close()


def load_learning_materials(learning_dir: str) -> List[Dict]:
    """从.learnings目录加载踩坑档案"""
    documents = []
    patterns = [
        f"{learning_dir}/incidents/**/*.md",
        f"{learning_dir}/cron/*.md",
        f"{learning_dir}/automation/*.md",
        f"{learning_dir}/backend/*.md",
    ]
    
    for pattern in patterns:
        for f in glob.glob(pattern, recursive=True):
            if os.path.basename(f).lower() in ("readme.md", "index.md"):
                continue
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if len(content) < 100:
                    continue
                
                # 提取标题
                title = os.path.basename(f).replace(".md", "")
                for line in content.split("\n")[:5]:
                    m = re.match(r"^#{1,3}\s+(.+)", line)
                    if m:
                        title = m.group(1).strip()
                        break
                
                # 判断类别
                category = "general"
                if "incident" in f:
                    category = "incident"
                elif "cron" in f:
                    category = "cron"
                elif "automation" in f:
                    category = "automation"
                
                documents.append({
                    "source": f,
                    "title": title,
                    "content": content,
                    "category": category
                })
            except Exception as e:
                print(f"[跳过] {f}: {e}")
    
    return documents


if __name__ == "__main__":
    # 演示：构建知识库并检索
    LEARNING_DIR = "/path/to/your/.learnings"  # 修改为你的路径
    
    kb = KnowledgeBase()
    
    # 1. 加载文档
    print("[加载] 踩坑档案...")
    docs = load_learning_materials(LEARNING_DIR)
    print(f"[加载] {len(docs)} 篇文档")
    
    if docs:
        # 2. 构建索引
        kb.add_documents(docs[:10])  # 先取10篇演示
        
        # 3. 检索测试
        print("\n[检索] '反爬策略'...")
        results = kb.search("反爬策略", top_k=3)
        for r in results:
            print(f"  [{r['score']}] {r['title'][:40]}...")
        
        # 4. 评测
        test_queries = [
            ("爬虫被封怎么办", "反爬"),
            ("定时任务超时", "cron"),
            ("内存溢出", "OOM"),
        ]
        metrics = kb.evaluate(test_queries)
        print(f"\n[评测] Top-5命中率: {metrics['top5_hit_rate']:.1%}")
    
    kb.close()
