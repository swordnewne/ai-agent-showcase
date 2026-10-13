#!/usr/bin/env python3
"""LLM 配置单一权威加载器（轨道 1 候选 3 收敛，2026-10-12）。

历史四份副本（preopen_report._load_deepseek_config / news_fetcher /
firsthand_news_fetcher / finance_p0a1 news_fetcher.staging）统一收敛到此模块。

纪律（RFC 依赖策略 D 类）：
- 密钥只从环境变量或 .env 文件读取，绝不硬编码；
- 配置对象加载后冻结（frozen dataclass），调用方不得修改；
- 解析来源记入 source 字段，供回执与审计。
"""
import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_REASONING_EFFORT = "high"  # V4 思考模式强度；显式化而非依赖 API 默认值。"none" 关闭思考。

_ENV_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_BACKUP", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "DEEPSEEK_REASONING_EFFORT")


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    backup_key: str
    reasoning_effort: str  # none / low / high / max（映射表见 DeepSeek 官方 thinking_mode 文档）
    source: str  # "env" / 具体 .env 路径 / "unconfigured"


def _parse_env_file(path) -> dict:
    """解析 .env 中的 DEEPSEEK_* 键；文件不存在或不可读返回 {}。"""
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for key in _ENV_KEYS:
                    if line.startswith(key + "="):
                        values[key] = line.split("=", 1)[1]
    except OSError:
        return {}
    return values


def load_llm_config(env_paths=()) -> LLMConfig:
    """环境变量优先；api_key 为空时按 env_paths 顺序读 .env 补齐。

    与历史副本语义逐点一致：.env 内四键都会覆盖（不只 api_key）；
    找到 api_key 即停止遍历后续候选文件；全程不读 env_paths 之外的文件。
    """
    cfg = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_API_KEY_BACKUP": os.environ.get("DEEPSEEK_API_KEY_BACKUP", ""),
        "DEEPSEEK_BASE_URL": os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "DEEPSEEK_REASONING_EFFORT": os.environ.get("DEEPSEEK_REASONING_EFFORT", DEFAULT_REASONING_EFFORT),
    }
    source = "env" if cfg["DEEPSEEK_API_KEY"] else "unconfigured"
    if not cfg["DEEPSEEK_API_KEY"]:
        for path in env_paths:
            values = _parse_env_file(path)
            if not values:
                continue
            cfg.update(values)
            if cfg["DEEPSEEK_API_KEY"]:
                source = str(path)
                break
    return LLMConfig(
        api_key=cfg["DEEPSEEK_API_KEY"],
        base_url=cfg["DEEPSEEK_BASE_URL"],
        model=cfg["DEEPSEEK_MODEL"],
        backup_key=cfg["DEEPSEEK_API_KEY_BACKUP"],
        reasoning_effort=cfg["DEEPSEEK_REASONING_EFFORT"],
        source=source,
    )
