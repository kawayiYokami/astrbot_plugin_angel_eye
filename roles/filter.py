"""
Angel Eye 插件 - 二次筛选角色 (Filter)
"""
import json
from typing import List, Dict, Optional
from pathlib import Path

from astrbot.api.provider import Provider
from astrbot.core.utils.io import read_file
from astrbot.core import logger

from ...models.results import FilterResult

class Filter:
    """
    二次筛选角色，负责从搜索返回的词条列表中选择最相关的一个。
    """
    def __init__(self, provider: Provider):
        self.provider = provider
        prompt_path = Path(__file__).parent.parent / "prompts" / "filter_prompt.md"
        self.prompt_template = read_file(str(prompt_path))

    def _format_search_results(self, search_results: List[Dict]) -> str:
        """
        将搜索结果格式化为字符串。
        """
        if not search_results:
            return "无搜索结果。"

        return "\n".join([f"- {item.get('title', '无标题')}" for item in search_results])

    async def select_best_entry(self, search_results: List[Dict], original_prompt: str) -> Optional[str]:
        """
        调用小模型分析并选择最佳词条。

        Args:
            search_results (List[Dict]): 搜索客户端返回的词条列表。
            original_prompt (str): 用户的原始问题。

        Returns:
            Optional[str]: 被选中的词条标题，如果分析失败或无相关词条则返回 None。
        """
        if not self.provider:
            logger.error("AngelEye[Filter]: 小模型Provider未初始化。")
            return None

        formatted_results = self._format_search_results(search_results)
        final_prompt = self.prompt_template.format(
            original_prompt=original_prompt,
            search_results=formatted_results
        )

        try:
            response = await self.provider.text_chat(prompt=final_prompt)

            json_str_match = response.completion_text[response.completion_text.find('{'):response.completion_text.rfind('}')+1]
            if not json_str_match:
                logger.warning(f"AngelEye[Filter]: 小模型未返回有效的JSON。原始返回: {response.completion_text}")
                return None

            response_json = json.loads(json_str_match)

            result = FilterResult(**response_json)
            logger.debug(f"AngelEye[Filter]: 分析结果: {result.selected_title}")
            return result.selected_title

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Filter]: 解析小模型返回的JSON失败: {e}。原始返回: {response.completion_text}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[Filter]: 调用小模型时发生未知错误: {e}", exc_info=True)
            return None