# core/cache_manager.py

from diskcache import Cache
from typing import Optional, Dict
from collections import defaultdict

# 缓存有效期：7天 (604800 秒)
CACHE_EXPIRATION = 604800

# 初始化缓存，设置 256MB 大小限制，存储在项目根目录下的 angel_eye_cache 文件夹
_cache = Cache("angel_eye_cache", size_limit=256 * 1024 * 1024)

# 缓存统计
_cache_stats = defaultdict(int)

def get_knowledge(key: str) -> Optional[str]:
    """
    根据唯一的知识键，从缓存中获取知识摘要。
    """
    global _cache_stats
    value = _cache.get(key)
    if value is not None:
        _cache_stats["hits"] += 1
    else:
        _cache_stats["misses"] += 1
    return value

def set_knowledge(key: str, value: str):
    """
    将一个知识键和对应的摘要存入缓存。
    """
    try:
        _cache.set(key, value, expire=CACHE_EXPIRATION)
    except Exception as e:
        # 记录错误但不中断流程
        pass

def build_doc_key(source: str, entity_name: str) -> str:
    """为文档知识构建缓存键"""
    return f"doc:{source}:{entity_name}"

def build_fact_key(fact_query: str) -> str:
    """为事实知识构建缓存键 (例如 '朱祁镇.父亲')"""
    return f"fact:{fact_query}"


def build_search_key(source: str, entity_name: str) -> str:
    """为搜索结果列表构建缓存键"""
    return f"search:{source}:{entity_name}"

def get_cache_stats() -> Dict[str, int]:
    """
    获取缓存统计信息
    """
    stats = dict(_cache_stats)
    # 确保hits和misses键存在
    stats.setdefault("hits", 0)
    stats.setdefault("misses", 0)
    total = stats["hits"] + stats["misses"]
    if total > 0:
        stats["hit_rate"] = stats["hits"] / total
    else:
        stats["hit_rate"] = 0.0
    return stats

def reset_cache_stats():
    """
    重置缓存统计信息
    """
    global _cache_stats
    _cache_stats = defaultdict(int)