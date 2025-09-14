"""
Angel Eye 插件 - 检索员角色 (Retriever)
"""
import json
from typing import List, Dict, Optional
from pathlib import Path

from astrbot.api.provider import Provider
from astrbot.core.utils.io import read_file
from astrbot.core import logger

from ...models.results import RetrieverResult

class Retriever:
    """
    检索员角色，负责分析对话，判断是否需要启动搜索流程。
    """
    def __init__(self, provider: Provider):
        self.provider = provider
        # 在初始化时直接加载Prompt模板
        prompt_path = Path(__file__).parent.parent / "prompts" / "retriever_prompt.md"
        self.prompt_template = read_file(str(prompt_path))

    def _format_dialogue(self, contexts: List[Dict], current_prompt: str) -> str:
        """
        将对话历史和当前问题格式化为单个字符串。
        """
        dialogue_parts = []
        for item in contexts:
            role = "User" if item.get("role") == "user" else "Assistant"
            dialogue_parts.append(f"{role}: {item.get('content')}")

        dialogue_parts.append(f"User: {current_prompt}")
        return "\n".join(dialogue_parts)

    async def analyze(self, contexts: List[Dict], current_prompt: str) -> Optional[RetrieverResult]:
        """
        调用小模型分析对话，并返回结构化的决策结果。

        Args:
            contexts (List[Dict]): 对话历史记录。
            current_prompt (str): 用户当前的输入。

        Returns:
            Optional[RetrieverResult]: 包含决策结果的数据对象，如果分析失败则返回 None。
        """
        if not self.provider:
            logger.error("AngelEye[Retriever]: 小模型Provider未初始化。")
            return None

        formatted_dialogue = self._format_dialogue(contexts, current_prompt)
        final_prompt = self.prompt_template.format(dialogue=formatted_dialogue)

        try:
            # 假设 provider 有一个可以直接返回 json 的方法
            response = await self.provider.text_chat(prompt=final_prompt)

            # 从返回的文本中提取 JSON
            json_str_match = response.completion_text[response.completion_text.find('{'):response.completion_text.rfind('}')+1]
            if not json_str_match:
                logger.warning(f"AngelEye[Retriever]: 小模型未返回有效的JSON。原始返回: {response.completion_text}")
                return None

            response_json = json.loads(json_str_match)

            logger.debug(f"AngelEye[Retriever]: 分析结果: {response_json}")
            return RetrieverResult(**response_json)

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Retriever]: 解析小模型返回的JSON失败: {e}。原始返回: {response.completion_text}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[Retriever]: 调用小模型时发生未知错误: {e}", exc_info=True)
            return None