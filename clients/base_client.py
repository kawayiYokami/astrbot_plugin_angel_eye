"""
基础Wiki客户端类
提供通用的请求处理和错误处理逻辑，减少代码重复
"""
import httpx
from typing import Dict, Optional, List
from abc import ABC, abstractmethod

from ..core.log import get_logger

logger = get_logger(__name__)


class BaseWikiClient(ABC):
    """
    Wiki客户端基类，封装通用的HTTP请求和错误处理逻辑
    """

    def __init__(self, api_endpoint: str, site_name: str, config: Dict):
        """
        初始化基础客户端

        :param api_endpoint: API端点URL
        :param site_name: 站点名称，用于日志记录
        :param config: 插件配置字典
        """
        self.API_ENDPOINT = api_endpoint
        self.site_name = site_name
        self.config = config
        self.retrieval_config = self.config.get("retrieval", {})

        self.HEADERS = {
            "User-Agent": "AstrBot-AngelEyePlugin/1.0 (https://github.com/kawayiYokami/astrbot)"
        }

        timeout_seconds = self.retrieval_config.get("timeout_seconds", 10)
        self.timeout = httpx.Timeout(timeout_seconds)

    async def _make_request(self, params: Dict) -> Optional[Dict]:
        """
        统一的HTTP请求处理方法

        :param params: 请求参数
        :return: 响应JSON数据，如果请求失败则返回None
        """
        try:
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=self.timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[{self.site_name}]: API请求失败: {e}")
            return None
        except httpx.TimeoutException as e:
            logger.error(f"AngelEye[{self.site_name}]: 请求超时: {e}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[{self.site_name}]: 发生未知错误: {e}", exc_info=True)
            return None

    @abstractmethod
    async def search(self, keyword: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        搜索方法，子类必须实现

        :param keyword: 搜索关键词
        :param limit: 返回结果的最大数量
        :return: 搜索结果列表
        """
        pass

    @abstractmethod
    async def get_page_content(self, title: str, pageid: int = None) -> Optional[str]:
        """
        获取页面内容方法，子类必须实现

        :param title: 页面标题
        :param pageid: 页面ID
        :return: 页面内容
        """
        pass