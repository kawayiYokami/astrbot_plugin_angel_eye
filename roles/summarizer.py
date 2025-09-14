"""
Angel Eye 插件 - 整理员角色 (Summarizer)
"""
from typing import Optional
from pathlib import Path

from astrbot.api.provider import Provider
from astrbot.core.utils.io import read_file
from astrbot.core import logger

class Summarizer:
    """
    整理员角色，负责将百科全文根据用户问题进行总结和提炼。
    """
    def __init__(self, provider: Provider):
        self.provider = provider
        prompt_path = Path(__file__).parent.parent / "prompts" / "summarizer_prompt.md"
        self.prompt_template = read_file(str(prompt_path))

    async def summarize(self, full_content: str, original_prompt: str) -> Optional[str]:
        """
        调用小模型对全文进行摘要。

        Args:
            full_content (str): 百科页面的完整内容。
            original_prompt (str): 用户的原始问题。

        Returns:
            Optional[str]: 总结好的文本，如果分析失败则返回 None。
        """
        if not self.provider:
            logger.error("AngelEye[Summarizer]: 小模型Provider未初始化。")
            return None

        final_prompt = self.prompt_template.format(
            original_prompt=original_prompt,
            full_content=full_content
        )

        try:
            # 整理员直接输出纯文本
            response = await self.provider.text_chat(prompt=final_prompt)
            summary_text = response.completion_text.strip()

            logger.debug(f"AngelEye[Summarizer]: 生成摘要成功。")
            return summary_text

        except Exception as e:
            logger.error(f"AngelEye[Summarizer]: 调用小模型时发生未知错误: {e}", exc_info=True)
            return None