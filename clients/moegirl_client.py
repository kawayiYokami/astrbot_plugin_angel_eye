"""
Angel Eye 插件 - 萌娘百科客户端
"""
import httpx
from typing import List, Dict, Optional
from urllib.parse import urljoin, quote
from bs4 import BeautifulSoup

from astrbot.core import logger

class MoegirlClient:
    """
    用于与萌娘百科进行交互的客户端。
    """
    BASE_URL = "https://zh.moegirl.org.cn/"
    API_PATH = "api.php"

    async def search(self, query: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        搜索萌娘百科，返回相关词条列表。

        Args:
            query (str): 搜索关键词。
            limit (int): 返回结果数量上限。

        Returns:
            List[Dict[str, str]]: 包含标题和链接的词条列表。
        """
        search_url = urljoin(self.BASE_URL, self.API_PATH)
        params = {
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(search_url, params=params)
                response.raise_for_status()
                data = response.json()

                if "query" not in data or "search" not in data["query"]:
                    return []

                results = []
                for item in data["query"]["search"]:
                    pageid = item['pageid']
                    # 构建完整的URL
                    url = f"https://zh.moegirl.org.cn/index.php?curid={pageid}"
                    results.append({
                        "title": item['title'],
                        "url": url
                    })
                return results
        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[MoegirlClient]: 搜索API请求失败，状态码: {e.response.status_code}")
            return []
        except Exception as e:
            logger.error(f"AngelEye[MoegirlClient]: 搜索时发生未知错误: {e}", exc_info=True)
            return []

    async def get_page_content(self, title: str) -> Optional[str]:
        """
        获取指定词条页面的纯文本内容。

        Args:
            title (str): 词条标题。

        Returns:
            Optional[str]: 页面的纯文本内容，失败则返回 None。
        """
        page_url = urljoin(self.BASE_URL, quote(title))

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(page_url)
                response.raise_for_status()

                # 使用 BeautifulSoup 解析 HTML 并提取纯文本
                soup = BeautifulSoup(response.text, 'html.parser')

                # 移除不需要的元素，如导航、侧边栏、编辑按钮等
                for element in soup.select(".navbox, .editsection, .mw-editsection, #siteSub, #jump-to-nav, .mw-jump-link"):
                    element.decompose()

                # 主要内容通常在 #mw-content-text 中
                content_div = soup.select_one("#mw-content-text .mw-parser-output")
                if content_div:
                    return content_div.get_text(separator='\n', strip=True)
                else:
                    return soup.get_text(separator='\n', strip=True)

        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[MoegirlClient]: 获取页面内容失败，状态码: {e.response.status_code}，页面: {title}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[MoegirlClient]: 获取页面内容时发生未知错误: {e}", exc_info=True)
            return None