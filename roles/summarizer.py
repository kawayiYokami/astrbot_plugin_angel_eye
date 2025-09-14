"""
Angel Eye 插件 - 摘要员角色 (Summarizer)
"""
from typing import Optional
from pathlib import Path

from core.log import get_logger

logger = get_logger(__name__) # 获取 logger 实例


class Summarizer:
    """
    摘要员角色，负责将百科全文提炼成简洁的背景知识。
    """
    def __init__(self, provider: 'Provider'): # 使用字符串注解
        self.provider = provider
        prompt_path = Path(__file__).parent.parent / "prompts" / "summarizer_prompt.md"
        self.prompt_template = prompt_path.read_text(encoding="utf-8")

    async def summarize(self, full_content: str, entity_name: str) -> Optional[str]:
        """
        调用分析模型对百科全文进行摘要，生成背景知识。

        Args:
            full_content (str): 百科页面的完整内容。
            entity_name (str): 需要摘要的实体名称。

        Returns:
            Optional[str]: 总结好的背景知识文本，如果分析失败则返回 None。
        """
        if not self.provider:
            logger.error("AngelEye[Summarizer]: 分析模型Provider未初始化。")
            return None

        final_prompt = self.prompt_template.format(
            entity_name=entity_name,
            full_content=full_content
        )

        try:
            response = await self.provider.text_chat(prompt=final_prompt)
            summary_text = response.completion_text.strip()

            logger.debug(f"AngelEye[Summarizer]: 为实体 '{entity_name}' 生成摘要成功。")
            return summary_text

        except Exception as e:
            logger.error(f"AngelEye[Summarizer]: 为实体 '{entity_name}' 生成摘要时发生未知错误: {e}", exc_info=True)
            return None