"""
Angel Eye 插件 - 筛选器角色 (Filter)
负责从多个搜索结果中选择最匹配上下文的词条
遵循"宁缺毋滥"原则，避免提供不相关的知识
"""
import json
from typing import List, Dict, Optional
from pathlib import Path

import logging
logger = logging.getLogger(__name__)
from ..core.exceptions import ParsingError, AngelEyeError
from ..core.formatter import format_unified_message
from ..core.json_parser import safe_extract_json



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
        # 存储模板文件路径，用于在每次调用时重新加载，防止状态污染
        self.prompt_path = Path(__file__).parent.parent / "prompts" / "filter_prompt.md"

        # (可选) 在初始化时检查文件是否存在，实现快速失败
        if not self.prompt_path.exists():
            logger.error(f"AngelEye[Filter]: 关键的Prompt文件缺失，路径: {self.prompt_path}")
            # 这里可以选择抛出异常，但为了保持向后兼容性，仅记录错误
            # raise FileNotFoundError(f"Filter prompt file not found at {self.prompt_path}")

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
            logger.info(f"AngelEye[Filter]: 实体 '{entity_name}' 的候选列表为空，跳过筛选")
            return None

        logger.info(f"AngelEye[Filter]: 为实体 '{entity_name}' 从 {len(candidate_list)} 个候选中进行筛选...")

        # 将 astrbot 上下文转换为统一格式
        dialogue_parts = []
        for item in contexts:
            dialogue_parts.append(format_unified_message(item))
        # 处理当前消息
        current_message_dict = {
            "role": "user",
            "content": current_prompt
        }
        dialogue_parts.append(format_unified_message(current_message_dict))
        formatted_dialogue = "\n".join(dialogue_parts)
        formatted_candidates = self._format_candidate_list(candidate_list)

        # 从文件重新加载模板，防止因复用导致的状态污染
        try:
            prompt_template = self.prompt_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"AngelEye[Filter]: 运行时读取Prompt文件失败: {e}", exc_info=True)
            # 使用备用模板作为降级策略
            prompt_template = "筛选最匹配的词条。对话: {dialogue}\n实体: {entity_name}\n候选: {candidate_list}"

        final_prompt = prompt_template.format(
            dialogue=formatted_dialogue,
            entity_name=entity_name,
            candidate_list=formatted_candidates
        )

        try:
            logger.debug(f"AngelEye[Filter]: 正在调用LLM进行筛选...")
            # 可选：记录发送的核心上下文
            # logger.debug(f"AngelEye[Filter]: 发送的上下文: {formatted_dialogue[:200]}...")
            # logger.debug(f"AngelEye[Filter]: 候选列表: {formatted_candidates}")
            response = await self.provider.text_chat(prompt=final_prompt)
            response_text = response.completion_text
            logger.debug(f"AngelEye[Filter]: LLM原始响应:\n{response_text}")

            # 使用新的、健壮的JSON解析器，并指定必须包含 "selected_title" 字段
            response_json = safe_extract_json(
                response_text,
                required_fields=["selected_title"]
            )

            if response_json is None:
                logger.warning("AngelEye[Filter]: 未能从模型响应中提取到包含'selected_title'的有效JSON。")
                return None

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