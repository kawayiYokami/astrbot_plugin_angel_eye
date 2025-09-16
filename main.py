"""
Angel Eye 插件主入口
实现轻量级指令驱动的知识获取架构
"""
import json
import astrbot.api.star as star
import astrbot.api.event.filter as filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest, Provider
import random
from astrbot.core.star.star_tools import StarTools

from astrbot.api import logger
from .services.qq_history_service import QQChatHistoryService
from .core import cache_manager
from .core.exceptions import AngelEyeError
from .roles.smart_retriever import SmartRetriever
from .roles.classifier import Classifier
from .roles.summarizer import Summarizer
from .core.formatter import format_unified_message


class AngelEyePlugin(star.Star):
    """
    Angel Eye 插件，通过在LLM调用前注入百科知识来增强上下文
    基于轻量级指令驱动的新架构
    """
    def __init__(self, context: star.Context, config: dict | None = None):
        self.context = context
        # 1. 加载配置
        self.config = config or {}
        logger.debug(f"AngelEye: 加载配置完成: {self.config}")

        # 初始化缓存管理器
        data_dir = str(StarTools.get_data_dir())
        cache_manager.init_cache(data_dir)

    @filter.on_llm_request(priority=100)
    async def enrich_context_before_llm_call(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在主模型请求前，执行上下文增强逻辑
        这是 Angel Eye 的核心入口点，使用新的智能知识获取架构
        """
        # 在事件处理时即时获取 Provider
        analyzer_provider_id = self.config.get("analyzer_provider_id", "claude-3-haiku")
        analyzer_provider: Provider = self.context.get_provider_by_id(analyzer_provider_id)


        if not analyzer_provider:
            logger.warning(f"AngelEye: 分析模型Provider '{analyzer_provider_id}' 未找到或未配置，跳过上下文增强")
            return

        # 在每次请求时创建新的角色实例，避免状态污染
        classifier = Classifier(analyzer_provider)
        smart_retriever = SmartRetriever(analyzer_provider, self.config)

        logger.info("AngelEye: 开始上下文增强流程")
        original_prompt = req.prompt

        try:
            # 1. 调用Classifier生成轻量级知识请求指令
            knowledge_request = await classifier.get_knowledge_request(req.contexts, original_prompt)
            logger.info(f"AngelEye: 步骤 1/3 - 分类器分析完成")
            logger.debug(f"  - 分类结果 (KnowledgeRequest): {knowledge_request}")

            # 如果没有需要查询的知识，直接返回
            if not knowledge_request:
                logger.info("AngelEye: 无需补充知识，流程结束。")
                return

            # 1.5. 格式化对话历史，供 Summarizer 使用
            # 将 astrbot 上下文转换为统一格式
            dialogue_parts = []
            for item in req.contexts:
                dialogue_parts.append(format_unified_message(item))
            # 处理当前消息
            current_message_dict = {
                "role": "user",
                "content": original_prompt
            }
            dialogue_parts.append(format_unified_message(current_message_dict))
            formatted_dialogue = "\n".join(dialogue_parts)

            # 2. 调用SmartRetriever执行智能知识检索
            knowledge_result = await smart_retriever.retrieve(knowledge_request, formatted_dialogue, event) # 传入 event 参数
            logger.info(f"AngelEye: 步骤 2/3 - 智能检索完成")
            if knowledge_result and knowledge_result.chunks:
                logger.debug(f"  - 检索到 {len(knowledge_result.chunks)} 个知识片段")

            # 如果没有获取到任何知识，直接返回
            if not knowledge_result.chunks:
                logger.info("AngelEye: 未检索到有效知识，流程结束。")
                return

            # 3. 将知识结果格式化为可注入的文本
            background_knowledge = knowledge_result.to_context_string()

            if not background_knowledge:
                logger.info("AngelEye: 背景知识为空，流程结束。")
                return

            # 4. 安全上下文注入
            # 从配置中读取 persona_name，如果不存在则使用默认值 'fairy|仙灵'
            persona_names_str = self.config.get("persona_name", "fairy|仙灵")
            # 如果包含 '|'，则随机选择一个
            if '|' in persona_names_str:
                persona_name = random.choice(persona_names_str.split('|')).strip()
            else:
                persona_name = persona_names_str

            # 构建包含身份提醒和背景知识的注入文本
            injection_text = f"\n\n---\n[系统提醒] 你的名字是 {persona_name}。请根据以下背景知识进行回复。\n\n[背景知识]:\n{background_knowledge}\n---"

            logger.info("AngelEye: 步骤 3/3 - 准备注入上下文")
            logger.debug(f"  - 注入的背景知识内容: {background_knowledge}")

            # 原有的注入代码
            req.system_prompt = (req.system_prompt or '') + injection_text
            logger.info(f"AngelEye: 成功注入知识，流程结束。")

        except AngelEyeError as e:
            logger.error(f"AngelEye: 在上下文增强流程中发生错误: {e}", exc_info=True)
            # 发生内部错误时，静默失败，不影响主流程
            return
