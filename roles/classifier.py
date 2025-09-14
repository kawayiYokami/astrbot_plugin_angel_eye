"""
Angel Eye 插件 - 分类器角色 (Classifier)
负责从对话中识别专有名词并推断其领域。
"""
import json
from typing import List, Dict, Optional, Any
from pathlib import Path

from core.log import get_logger

logger = get_logger(__name__) # 获取 logger 实例


class ClassifiedEntity:
    """
    分类器识别出的单个实体及其领域。
    """
    def __init__(self, name: str, domain: str):
        self.name = name
        self.domain = domain

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "domain": self.domain}


class Classifier:
    """
    分类器角色，负责分析对话上下文，识别专有名词并分类。
    """
    def __init__(self, provider: 'Provider'): # 使用字符串注解
        self.provider = provider
        # 在初始化时直接加载Prompt模板
        prompt_path = Path(__file__).parent.parent / "prompts" / "classifier_prompt.md"
        try:
            self.prompt_template = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error(f"AngelEye[Classifier]: 找不到Prompt文件 {prompt_path}")
            self.prompt_template = "你是一个对话分析助手。请分析以下对话并识别专有名词及其领域。对话: {dialogue}"

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

    async def classify(self, contexts: List[Dict], current_prompt: str) -> List[ClassifiedEntity]:
        """
        调用分析模型，识别对话中的实体并分类。

        Args:
            contexts (List[Dict]): 对话历史记录。
            current_prompt (str): 用户当前的输入。

        Returns:
            List[ClassifiedEntity]: 识别出的实体及其领域列表。
        """
        if not self.provider:
            logger.error("AngelEye[Classifier]: 分析模型Provider未初始化。")
            return []

        formatted_dialogue = self._format_dialogue(contexts, current_prompt)
        final_prompt = self.prompt_template.format(dialogue=formatted_dialogue)

        try:
            response = await self.provider.text_chat(prompt=final_prompt)
            response_text = response.completion_text

            # 从返回的文本中提取 JSON
            json_str_start = response_text.find('{')
            json_str_end = response_text.rfind('}') + 1
            if json_str_start == -1 or json_str_end <= json_str_start:
                logger.warning(f"AngelEye[Classifier]: 模型未返回有效的JSON结构。原始返回: {response_text}")
                return []

            json_str = response_text[json_str_start:json_str_end]
            response_json = json.loads(json_str)

            entities_data = response_json.get("entities", [])
            classified_entities = [ClassifiedEntity(**item) for item in entities_data]

            logger.debug(f"AngelEye[Classifier]: 识别到实体: {[e.to_dict() for e in classified_entities]}")
            return classified_entities

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Classifier]: 解析模型返回的JSON失败: {e}。原始返回: {response_text}")
            return []
        except Exception as e:
            logger.error(f"AngelEye[Classifier]: 调用分析模型时发生未知错误: {e}", exc_info=True)
            return []