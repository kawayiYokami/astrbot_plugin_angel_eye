"""
输入验证器模块
提供用于函数输入的装饰器
"""
from functools import wraps
import logging
logger = logging.getLogger(__name__)


def validate_input(max_length: int = None):
    """
    一个装饰器，用于验证被装饰函数的第一个字符串参数。

    :param max_length: 允许的最大字符串长度
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, content: str, *args, **kwargs):
            if not isinstance(content, str) or not content.strip():
                logger.warning(f"Validation failed for {func.__name__}: content is empty or not a string.")
                # 或者可以引发 ValidationError
                return None

            if max_length and len(content) > max_length:
                logger.warning(f"Validation failed for {func.__name__}: content exceeds max length {max_length}. Truncating.")
                content = content[:max_length]

            return await func(self, content, *args, **kwargs)
        return wrapper
    return decorator