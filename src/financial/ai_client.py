# -*- coding: utf-8 -*-
"""
LLM客户端封装
支持：Kimi / DeepSeek / OpenAI兼容API

Python 3.6+ 兼容
"""

import logging
import json
import os
import time
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class LLMClient:
    """
    通用LLM客户端
    
    环境变量优先级：
    1. DEEPSEEK_API_KEY + DEEPSEEK_BASE_URL
    2. KIMI_API_KEY + KIMI_BASE_URL
    3. OPENAI_API_KEY + OPENAI_BASE_URL
    """
    
    def __init__(self):
        self.api_key = None
        self.base_url = None
        self.model = None
        self._session = None
        self._init_from_env()
    
    def _init_from_env(self):
        """从环境变量初始化"""
        # 1. DeepSeek
        if os.environ.get("DEEPSEEK_API_KEY"):
            self.api_key = os.environ["DEEPSEEK_API_KEY"]
            self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
            self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
            logger.info("使用DeepSeek API")
            return
        
        # 2. Kimi
        if os.environ.get("KIMI_API_KEY"):
            self.api_key = os.environ["KIMI_API_KEY"]
            self.base_url = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
            self.model = os.environ.get("KIMI_MODEL", "moonshot-v1-8k")
            logger.info("使用Kimi API")
            return
        
        # 3. OpenAI
        if os.environ.get("OPENAI_API_KEY"):
            self.api_key = os.environ["OPENAI_API_KEY"]
            self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            self.model = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
            logger.info("使用OpenAI API")
            return
        
        logger.warning("未找到API Key，LLM调用将失败")
    
    def _get_session(self):
        """延迟初始化session"""
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": "Bearer {}".format(self.api_key),
                "Content-Type": "application/json",
            })
        return self._session
    
    def chat(self, prompt: str,
             system_prompt: Optional[str] = None,
             temperature: float = 0.3,
             max_tokens: int = 2000,
             retries: int = 2) -> Optional[str]:
        """
        调用LLM
        
        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词
            temperature: 温度
            max_tokens: 最大token数
            retries: 重试次数
        
        Returns:
            LLM输出文本
        """
        if not self.api_key:
            logger.error("未配置API Key")
            return None
        
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        for attempt in range(retries + 1):
            try:
                resp = self._get_session().post(
                    "{}/chat/completions".format(self.base_url),
                    json=payload,
                    timeout=60
                )
                resp.raise_for_status()
                data = resp.json()
                
                if "choices" in data and len(data["choices"]) > 0:
                    content = data["choices"][0]["message"]["content"]
                    return content.strip()
                else:
                    logger.warning("LLM返回空choices: %s", data)
                    return None
            
            except Exception as e:
                logger.error("LLM调用失败 (attempt %d/%d): %s", 
                             attempt + 1, retries + 1, e)
                if attempt < retries:
                    time.sleep(2 ** attempt)  # 指数退避
                else:
                    return None
        
        return None
    
    def is_available(self) -> bool:
        """检查LLM是否可用"""
        return self.api_key is not None


# 全局实例
_client = None


def get_llm_client() -> LLMClient:
    """获取全局LLM客户端"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def quick_chat(prompt: str, system_prompt: Optional[str] = None) -> Optional[str]:
    """快捷调用"""
    return get_llm_client().chat(prompt, system_prompt=system_prompt)
