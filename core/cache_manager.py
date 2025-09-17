# core/cache_manager.py

import asyncio
import logging
from diskcache import Cache
from typing import Optional, Dict, Any
from collections import defaultdict

# 模块级 logger
logger = logging.getLogger(__name__)

# 缓存有效期：7天 (604800 秒)
CACHE_EXPIRATION = 604800

_cache: Optional[Cache] = None
_cache_lock = asyncio.Lock() # 用于保护 _cache_stats 的锁
_cache_stats = defaultdict(int)

def init_cache(data_dir: str):
    """
    初始化缓存管理器。
    这个函数应该在插件启动时被调用一次。
    """
    global _cache
    if _cache is None:
        cache_path = f"{data_dir}/cache"
        _cache = Cache(cache_path, size_limit=256 * 1024 * 1024)
        logger.info(f"AngelEye[CacheManager]: 缓存已在路径 '{cache_path}' 初始化")

def _ensure_cache_initialized():
    """确保缓存已被初始化，否则抛出异常"""
    if _cache is None:
        raise RuntimeError(
            "Cache has not been initialized. "
            "Please call init_cache() at plugin startup."
        )

async def get(key: str) -> Optional[Any]:
    """
    【统一接口】根据键，从缓存中异步获取任何类型的数据。
    """
    _ensure_cache_initialized()
    value = await asyncio.to_thread(_cache.get, key)

    async with _cache_lock:
        if value is not None:
            _cache_stats["hits"] += 1
            logger.debug(f"AngelEye[CacheManager]: 缓存命中 (Key: {key})")
        else:
            _cache_stats["misses"] += 1
            logger.debug(f"AngelEye[CacheManager]: 缓存未命中 (Key: {key})")
    return value

async def set(key: str, value: Any, expire: int = CACHE_EXPIRATION):
    """
    【统一接口】将一个键和对应的值（任何类型）异步存入缓存。
    """
    _ensure_cache_initialized()
    try:
        await asyncio.to_thread(_cache.set, key, value, expire=expire)
        logger.debug(f"AngelEye[CacheManager]: 成功写入缓存 (Key: {key}, Expire: {expire}s)")
    except Exception as e:
        logger.error(f"AngelEye[CacheManager]: 写入缓存失败 (key: {key})", exc_info=e)

def build_doc_key(source: str, entity_name: str) -> str:
    """为文档知识构建缓存键"""
    return f"doc:{source}:{entity_name}"

def build_fact_key(fact_query: str) -> str:
    """为事实知识构建缓存键 (例如 '朱祁镇.父亲')"""
    return f"fact:{fact_query}"

def build_search_key(source: str, entity_name: str) -> str:
    """为搜索结果列表构建缓存键"""
    return f"search:{source}:{entity_name}"

async def get_cache_stats() -> Dict[str, int]:
    """
    异步获取缓存统计信息
    """
    async with _cache_lock:
        stats = dict(_cache_stats)
    # 确保hits和misses键存在
    stats.setdefault("hits", 0)
    stats.setdefault("misses", 0)
    total = stats["hits"] + stats["misses"]
    if total > 0:
        stats["hit_rate"] = round(stats["hits"] / total, 4)
    else:
        stats["hit_rate"] = 0.0
    return stats

async def reset_cache_stats():
    """
    异步重置缓存统计信息
    """
    async with _cache_lock:
        global _cache_stats
        _cache_stats = defaultdict(int)