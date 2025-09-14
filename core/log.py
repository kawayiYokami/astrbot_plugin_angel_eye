"""
Angel Eye 插件 - 内部日志系统
提供一个解耦的日志记录器，可在 AstrBot 环境中使用上游 logger，或在独立测试时回退到标准库 logger。
"""
import logging
from typing import Optional

# 尝试导入 AstrBot 的上游 logger
try:
    from astrbot.core import logger as astrbot_logger
    _HAS_UPSTREAM_LOGGER = True
except ImportError:
    _HAS_UPSTREAM_LOGGER = False
    astrbot_logger = None


def get_logger(name: str) -> logging.Logger:
    """
    获取一个日志记录器实例。

    如果在 AstrBot 环境中运行，将返回上游的 astrbot_logger。
    如果在独立环境（如测试）中运行，将返回一个配置好的标准库 logger。

    Args:
        name (str): Logger 的名称，通常传入 __name__。

    Returns:
        logging.Logger: 配置好的 logger 实例。
    """
    if _HAS_UPSTREAM_LOGGER:
        # 在 AstrBot 环境中，直接返回上游 logger
        # 这样可以确保日志格式、级别等与主程序保持一致
        return astrbot_logger
    else:
        # 在独立环境（如测试）中，创建并配置一个标准库 logger
        # 这使得模块可以独立测试，而无需 AstrBot 环境
        logger = logging.getLogger(name)
        if not logger.handlers:
            # 避免重复添加 handler
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger