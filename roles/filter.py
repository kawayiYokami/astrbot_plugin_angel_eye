"""
Angel Eye 插件 - 筛选器角色 (Filter)
负责从多个搜索结果中选择最匹配上下文的词条。
"""
import json
from typing import List, Dict, Optional
from pathlib import Path

from core.log import get_logger

logger = get_logger(__name__) # 获取 logger 实例


class Filter:
    """
    筛选器角色，负责从多个搜索结果中选择最匹配上下文的词条。
    """
    def __init__(self, provider: 'Provider'): # 使用字符串注解
        self.provider = provider
        prompt_path = Path(__file__).parent.parent / "prompts" / "filter_prompt.md"
        self.prompt_template = prompt_path.read_text(encoding="utf-8")

    def _format_dialogue(self, contexts: List[Dict], current_prompt: str) -> str:
        """
        将对话历史和当前问题格式化为单个字符串，供模型分析。
        """
        dialogue_parts = []
        for item in contexts:
            # AstrBot 的上下文通常是 {'role': 'user'/'assistant', 'content': '...'}
            role = item.get("role", "unknown").capitalize()
            content = item.get("content", "")
            dialogue_parts.append(f"{role}: {content}")

        dialogue_parts.append(f"User: {current_prompt}")
        return "\n".join(dialogue_parts)

    def _format_candidate_list(self, candidate_list: List[Dict]) -> str:
        """
        将候选词条列表格式化为字符串。
        """
        if not candidate_list:
            return "无候选词条。"

        return "\n".join([f"- {item.get('title', '无标题')}" for item in candidate_list])

    async def select_best_entry(self, contexts: List[Dict], current_prompt: str, entity_name: str, candidate_list: List[Dict]) -> Optional[str]:
        """
        调用分析模型，从候选词条中选择最匹配上下文的一个。

        Args:
            contexts (List[Dict]): 对话历史记录。
            current_prompt (str): 用户当前的输入。
            entity_name (str): 目标实体的名称。
            candidate_list (List[Dict]): 搜索客户端返回的候选词条列表。

        Returns:
            Optional[str]: 被选中的词条标题，如果分析失败或无相关词条则返回 None。
        """
        if not self.provider:
            logger.error("AngelEye[Filter]: 分析模型Provider未初始化。")
            return None

        formatted_dialogue = self._format_dialogue(contexts, current_prompt)
        formatted_candidates = self._format_candidate_list(candidate_list)

        final_prompt = self.prompt_template.format(
            dialogue=formatted_dialogue,
            entity_name=entity_name,
            candidate_list=formatted_candidates
        )

        try:
            response = await self.provider.text_chat(prompt=final_prompt)
            response_text = response.completion_text

            # 从返回的文本中提取 JSON
            json_str_start = response_text.find('{')
            json_str_end = response_text.rfind('}') + 1
            if json_str_start == -1 or json_str_end <= json_str_start:
                logger.warning(f"AngelEye[Filter]: 模型未返回有效的JSON结构。原始返回: {response_text}")
                return None

            json_str = response_text[json_str_start:json_str_end]
            response_json = json.loads(json_str)

            selected_title = response_json.get("selected_title")

            if selected_title:
                logger.debug(f"AngelEye[Filter]: 为实体 '{entity_name}' 选择的最佳词条是: '{selected_title}'")
            else:
                logger.info(f"AngelEye[Filter]: 为实体 '{entity_name}' 未找到匹配的词条。")

            return selected_title

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Filter]: 解析模型返回的JSON失败: {e}。原始返回: {response_text}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[Filter]: 调用分析模型时发生未知错误: {e}", exc_info=True)
            return None