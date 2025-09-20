"""
Angel Eye 插件 - 摘要员角色 (Summarizer)
负责将百科全文提炼成简洁的背景知识，遵循"宁缺毋滥"原则
"""
from typing import Optional, Dict
from pathlib import Path


import logging
logger = logging.getLogger(__name__)
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
        self.max_history_chars = self.config.get("max_history_chars", 50000)

        # 预加载所有 prompt 模板
        self.wiki_prompt_template = self._load_prompt("summarizer_prompt.md")
        self.chat_prompt_template = self._load_prompt("qq_chat_history_summarizer_prompt.md")

    def _load_prompt(self, filename: str) -> str:
        """加载 prompt 模板的辅助函数"""
        prompt_path = Path(__file__).parent.parent / "prompts" / filename
        try:
            prompt_template = prompt_path.read_text(encoding="utf-8")
            logger.debug(f"AngelEye[Summarizer]: 成功加载Prompt模板 {filename}")
            return prompt_template
        except FileNotFoundError:
            logger.error(f"AngelEye[Summarizer]: 找不到Prompt文件 {prompt_path}")
            return "为实体 {entity_name} 生成摘要:\n{full_content}"

    # --- 这是唯一暴露给外面的方法 ---
    async def summarize(self, source: str, full_content: str, entity_name: str, dialogue: str) -> Optional[str]:
        """
        统一的摘要方法，根据数据源选择不同的 prompt 模板。

        :param source: 数据源 ("wikipedia", "moegirl", "qq_chat_history")
        :param full_content: 原始长文本内容
        :param entity_name: 实体名称
        :param dialogue: 格式化后的对话历史
        :return: 总结好的背景知识文本，如果生成失败则返回None
        """
        if not self.provider:
            logger.error("AngelEye[Summarizer]: 分析模型Provider未初始化")
            return None

        # 1. "三选一" 选择 prompt
        if source in ["wikipedia", "moegirl"]:
            prompt_template = self.wiki_prompt_template
            # 截断内容以符合模型上下文限制
            content_to_summarize = full_content[:self.max_context_length]
            final_prompt = prompt_template.format(
                full_content=content_to_summarize,
                entity_name=entity_name,
                dialogue=dialogue
            )
        elif source == "qq_chat_history":
            prompt_template = self.chat_prompt_template
            # 聊天记录的 prompt 可能需要不同的变量和长度控制
            if len(full_content) > self.max_history_chars:
                full_content = f"...(部分历史记录已省略)...\n{full_content[-self.max_history_chars:]}"
            final_prompt = prompt_template.format(
                historical_chat=full_content,
                latest_dialogue=dialogue
            )
        else:
            logger.warning(f"AngelEye[Summarizer]: 不支持的摘要源: {source}")
            return None

        # 2. "合流"：统一调用小模型
        logger.info(f"AngelEye: 使用 {source} 模板为 '{entity_name}' 生成摘要...")
        # 可选：记录发送的核心上下文
        # logger.debug(f"AngelEye[Summarizer]: 发送的实体: {entity_name}")
        response = await self.provider.text_chat(prompt=final_prompt)
        summary_text = response.completion_text.strip()
        logger.debug(f"AngelEye[Summarizer]: LLM原始响应:\n{summary_text}")

        # 3. 统一返回结果
        if summary_text:
            logger.info(f"AngelEye: 成功为实体 '{entity_name}' 生成摘要，长度: {len(summary_text)} 字符")
            return summary_text
        else:
            logger.warning(f"AngelEye: 实体 '{entity_name}' 的摘要为空")
            return None
