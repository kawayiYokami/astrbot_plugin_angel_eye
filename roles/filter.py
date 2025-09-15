"""
Angel Eye 插件 - 筛选器角色 (Filter)
负责从多个搜索结果中选择最匹配上下文的词条
遵循"宁缺毋滥"原则，避免提供不相关的知识
"""
import json
from typing import List, Dict, Optional
from pathlib import Path

from astrbot.api import logger
from ..core.exceptions import ParsingError, AngelEyeError



class Filter:
    """
    筛选器角色，负责从多个搜索结果中选择最匹配上下文的词条
    核心原则：宁缺毋滥，不相关的内容绝对不要提供
    """
    def __init__(self, provider: 'Provider'):
        """
        初始化筛选器

        :param provider: 用于调用LLM的Provider
        """
        self.provider = provider
        prompt_path = Path(__file__).parent.parent / "prompts" / "filter_prompt.md"
        try:
            self.prompt_template = prompt_path.read_text(encoding="utf-8")
            logger.info("AngelEye[Filter]: 成功加载Prompt模板")
        except FileNotFoundError:
            logger.error(f"AngelEye[Filter]: 找不到Prompt文件 {prompt_path}")
            self.prompt_template = "筛选最匹配的词条。对话: {dialogue}\n实体: {entity_name}\n候选: {candidate_list}"

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
        将候选词条列表格式化为包含标题和摘要的JSON字符串。
        """
        if not candidate_list:
            return "[]"

        # 只提取 title 和 snippet 以优化上下文
        formatted_list = [
            {"title": item.get("title", "无标题"), "snippet": item.get("snippet", "")}
            for item in candidate_list
        ]
        return json.dumps(formatted_list, ensure_ascii=False, indent=2)

    async def select_best_entry(self, contexts: List[Dict], current_prompt: str, entity_name: str, candidate_list: List[Dict]) -> Optional[str]:
        """
        调用LLM从候选词条中选择最匹配上下文的一个
        严格遵循"宁缺毋滥"原则

        :param contexts: 对话历史记录
        :param current_prompt: 用户当前的输入
        :param entity_name: 目标实体的名称
        :param candidate_list: 搜索客户端返回的候选词条列表
        :return: 被选中的词条标题，如果无相关词条则返回None
        """
        if not self.provider:
            logger.error("AngelEye[Filter]: 分析模型Provider未初始化")
            return None

        if not candidate_list:
            logger.info(f"AngelEye[Filter]: 实体 '{entity_name}' 的候选列表为空")
            return None

        logger.debug(f"AngelEye[Filter]: 为实体 '{entity_name}' 从 {len(candidate_list)} 个候选中筛选")

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

            # 提取JSON字符串
            json_str_start = response_text.find('{')
            json_str_end = response_text.rfind('}') + 1

            if json_str_start == -1 or json_str_end <= json_str_start:
                logger.warning(f"AngelEye[Filter]: 模型未返回有效的JSON结构")
                logger.debug(f"原始返回: {response_text}")
                return None

            json_str = response_text[json_str_start:json_str_end]
            response_json = json.loads(json_str)

            selected_title = response_json.get("selected_title")

            if selected_title:
                logger.info(f"AngelEye[Filter]: 为实体 '{entity_name}' 选择了词条: '{selected_title}'")
                # 验证选择的词条确实在候选列表中
                candidate_titles = [item.get('title', '') for item in candidate_list]
                if selected_title not in candidate_titles:
                    logger.warning(f"AngelEye[Filter]: 选择的词条 '{selected_title}' 不在候选列表中，忽略")
                    return None
            else:
                logger.info(f"AngelEye[Filter]: 为实体 '{entity_name}' 未找到匹配的词条，遵循宁缺毋滥原则")

            return selected_title

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Filter]: 解析JSON失败: {e}")
            logger.debug(f"JSON文本: {json_str if 'json_str' in locals() else response_text}")
            raise ParsingError("Failed to parse JSON from Filter LLM response") from e
        except Exception as e:
            logger.error(f"AngelEye[Filter]: 调用LLM时发生错误: {e}", exc_info=True)
            raise AngelEyeError("Filter LLM call failed") from e