"""
Angel Eye 插件 - 摘要员角色 (Summarizer)
负责将百科全文提炼成简洁的背景知识，遵循"宁缺毋滥"原则
"""
from typing import Optional, Dict
from pathlib import Path


from astrbot.api import logger
from ..core.exceptions import AngelEyeError


class Summarizer:
    """
    摘要员角色，负责将百科全文提炼成简洁的背景知识
    核心原则：只提供与核心实体高度相关的信息，绝对不要提供不相关的内容
    """
    def __init__(self, provider: 'Provider', config: Dict):
        """
        初始化摘要员

        :param provider: 用于调用LLM的Provider
        :param config: 插件配置字典
        """
        self.provider = provider
        self.config = config
        self.max_context_length = self.config.get("max_context_length", 2000)

        prompt_path = Path(__file__).parent.parent / "prompts" / "summarizer_prompt.md"
        try:
            self.prompt_template = prompt_path.read_text(encoding="utf-8")
            logger.info("AngelEye[Summarizer]: 成功加载Prompt模板")
        except FileNotFoundError:
            logger.error(f"AngelEye[Summarizer]: 找不到Prompt文件 {prompt_path}")
            self.prompt_template = "为实体 {entity_name} 生成摘要:\n{full_content}"

    async def summarize(self, full_content: str, entity_name: str, dialogue: str) -> Optional[str]:
        """
        调用LLM对百科全文进行摘要，生成背景知识
        严格遵循"宁缺毋滥"原则，避免生成无关内容

        :param full_content: 百科页面的完整内容
        :param entity_name: 需要摘要的实体名称
        :param dialogue: 格式化后的对话历史
        :return: 总结好的背景知识文本，如果生成失败则返回None
        """
        if not self.provider:
            logger.error("AngelEye[Summarizer]: 分析模型Provider未初始化")
            return None

        # 截断内容以符合模型上下文限制
        content_to_summarize = full_content[:self.max_context_length]

        final_prompt = self.prompt_template.format(
            entity_name=entity_name,
            full_content=content_to_summarize,
            dialogue=dialogue
        )

        try:
            logger.debug(f"AngelEye[Summarizer]: 正在为实体 '{entity_name}' 生成摘要...")
            response = await self.provider.text_chat(prompt=final_prompt)
            summary_text = response.completion_text.strip()


            if summary_text:
                logger.info(f"AngelEye[Summarizer]: 成功为实体 '{entity_name}' 生成摘要，长度: {len(summary_text)} 字符")
                return summary_text
            else:
                logger.warning(f"AngelEye[Summarizer]: 实体 '{entity_name}' 的摘要为空")
                return None

        except Exception as e:
            logger.error(f"AngelEye[Summarizer]: 为实体 '{entity_name}' 生成摘要时发生错误: {e}", exc_info=True)
            raise AngelEyeError(f"Summarizer failed for entity '{entity_name}'") from e