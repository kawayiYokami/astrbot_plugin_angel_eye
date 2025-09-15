"""
Angel Eye 插件主入口
实现轻量级指令驱动的知识获取架构
"""
import astrbot.api.star as star
import astrbot.api.event.filter as filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest, Provider
import random

from .core.log import get_logger, setup_llm_logger
from .core.exceptions import AngelEyeError
# 导入新的智能检索器和相关组件
from .roles.smart_retriever import SmartRetriever
from .roles.classifier import Classifier
from .roles.summarizer import Summarizer

logger = get_logger(__name__)


class AngelEyePlugin(star.Star):
    """
    Angel Eye 插件，通过在LLM调用前注入百科知识来增强上下文
    基于轻量级指令驱动的新架构
    """
    def __init__(self, context: star.Context, config: dict | None = None):
        self.context = context
        # 1. 加载配置
        self.config = config or {}
        logger.info(f"AngelEye: 加载配置完成: {self.config}")

        # 2. 初始化LLM日志记录器
        setup_llm_logger(self.config)

        # 3. 初始化新架构的所有角色和客户端 (此时不传入Provider)
        self.classifier = Classifier(None)
        self.smart_retriever = SmartRetriever(None, self.config)
        self.summarizer = Summarizer(None, self.config)

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

        # 将获取到的 Provider 更新到角色实例中
        self.classifier.provider = analyzer_provider
        self.smart_retriever.analyzer_provider = analyzer_provider
        self.summarizer.provider = analyzer_provider

        logger.info("AngelEye: 上下文增强流程启动...")
        original_prompt = req.prompt

        try:
            # 1. 调用Classifier生成轻量级知识请求指令
            knowledge_request = await self.classifier.get_knowledge_request(req.contexts, original_prompt)

            # 如果没有需要查询的知识，直接返回
            if not knowledge_request:
                logger.info("AngelEye: 未发现需要补充的背景知识，流程结束")
                return

            # 1.5. 格式化对话历史，供 Summarizer 使用
            formatted_dialogue = self.classifier._format_dialogue(req.contexts, original_prompt)

            # 2. 调用SmartRetriever执行智能知识检索
            knowledge_result = await self.smart_retriever.retrieve(knowledge_request, formatted_dialogue)

            # 如果没有获取到任何知识，直接返回
            if not knowledge_result.chunks:
                logger.info("AngelEye: 未获取到任何背景知识，流程结束")
                return

            # 3. 将知识结果格式化为可注入的文本
            background_knowledge = knowledge_result.to_context_string()

            if not background_knowledge:
                logger.info("AngelEye: 背景知识为空，流程结束")
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

            req.system_prompt = (req.system_prompt or "") + injection_text
            logger.info(f"AngelEye: 成功注入身份提醒和背景知识！(昵称: {persona_name})")

        except AngelEyeError as e:
            logger.error(f"AngelEye: 在上下文增强流程中发生错误: {e}", exc_info=True)
            # 发生内部错误时，静默失败，不影响主流程
            return