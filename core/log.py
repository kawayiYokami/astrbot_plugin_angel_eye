"""
Angel Eye 插件 - 内部日志系统
提供一个解耦的日志记录器，可在 AstrBot 环境中使用上游 logger，或在独立测试时回退到标准库 logger。
"""
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
from pathlib import Path

# 尝试导入 AstrBot 的上游 logger
try:
    from astrbot.core import logger as astrbot_logger
    _HAS_UPSTREAM_LOGGER = True
except ImportError:
    _HAS_UPSTREAM_LOGGER = False
    astrbot_logger = None

# LLM日志相关的全局变量
llm_logger = None
llm_logging_enabled = True

def setup_llm_logger(config: dict = None):
    """根据配置设置LLM专用logger"""
    global llm_logger, llm_logging_enabled

    # 从配置中读取设置
    if config:
        llm_logging_enabled = config.get("llm_log_enabled", True)
        max_size_mb = config.get("llm_log_max_size_mb", 1)
    else:
        max_size_mb = 1

    # 如果日志记录已禁用，直接返回
    if not llm_logging_enabled:
        llm_logger = None
        return

    # 设置LLM专用logger
    llm_logger = logging.getLogger('llm_interactions')
    llm_logger.setLevel(logging.INFO)
    # 防止日志向上传播到root logger，避免重复输出
    llm_logger.propagate = False

    # 清除现有的处理器
    llm_logger.handlers.clear()

    # 配置滚动文件处理器
    # 创建logs目录（如果不存在）
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / "llm_interactions.log"

    # maxBytes根据配置设置, backupCount固定为1
    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=1,
        encoding='utf-8'
    )

    # 定义日志格式
    formatter = logging.Formatter('%(asctime)s - %(message)s')
    handler.setFormatter(formatter)

    # 将处理器添加到logger
    llm_logger.addHandler(handler)

def log_llm_interaction(prompt: str, response: str):
    """记录一次完整的LLM交互。"""
    # 如果日志记录已禁用或logger未设置，直接返回
    if not llm_logging_enabled or llm_logger is None:
        return

    log_message = (
        f"--- LLM Request ---\n"
        f"{prompt}\n"
        f"--- LLM Response ---\n"
        f"{response}\n"
        f"---------------------\n"
    )
    llm_logger.info(log_message)


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

# 在模块加载时初始化LLM日志记录器
setup_llm_logger()