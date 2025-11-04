"""
Angel Eye 插件主入口
实现轻量级指令驱动的知识获取架构
"""
from typing import Optional, Tuple
import astrbot.api.star as star
import astrbot.api.event.filter as filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.star.star_tools import StarTools

from astrbot.api import logger
from .core import cache_manager
from .core.exceptions import AngelEyeError
from .roles.smart_retriever import SmartRetriever
from .roles.classifier import Classifier
from .core.context.small_model_prompt_builder import SmallModelPromptBuilder
from .models.request import KnowledgeRequest


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

    def _get_dialogue_records(self, event: AstrMessageEvent, req_contexts: list, original_prompt: str) -> str:
        """
        获取对话记录的统一方法
        优先使用天使之心提供的上下文，如果没有则使用 Astar 原生上下文

        Args:
            event: AstrMessageEvent 事件对象
            req_contexts: 请求上下文列表
            original_prompt: 原始提示词

        Returns:
            str: 格式化后的对话记录字符串，如果不需要搜索则返回空字符串
        """
        # 1. 优先使用天使之心上下文
        if hasattr(event, 'angelheart_context') and event.angelheart_context:
            try:
                # 解析上下文检查是否需要搜索
                _, _, needs_search = SmallModelPromptBuilder.parse_angelheart_context(event.angelheart_context)

                if not needs_search:
                    logger.info("AngelEye: 天使之心指示不需要搜索，跳过知识检索")
                    return ""

                # 使用天使之心格式化方法
                formatted = SmallModelPromptBuilder.format_conversation_summary(event.angelheart_context)
                logger.info("AngelEye: 使用天使之心上下文")
                return formatted

            except Exception as e:
                logger.warning(f"AngelEye: 处理天使之心上下文失败: {e}")

        # 2. 回退到 Astar 原生上下文
        formatted = SmallModelPromptBuilder.format_astar_conversation(req_contexts, original_prompt)
        logger.info("AngelEye: 使用 Astar 原生上下文")
        return formatted

    @filter.on_llm_request(priority=30)
    async def enrich_context_before_llm_call(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在主模型请求前，执行上下文增强逻辑
        这是 Angel Eye 的核心入口点，使用新的智能知识获取架构
        """
        # 1. 执行前置验证
        if not await self._should_process_event(event):
            return

        # 2. 验证配置和提供商
        providers = await self._validate_and_get_providers()
        if not providers:
            return

        # 3. 执行知识增强流程
        await self._execute_knowledge_enrichment(event, req, providers)

    async def _should_process_event(self, event: AstrMessageEvent) -> bool:
        """
        检查是否应该处理此事件

        Args:
            event: AstrMessageEvent 事件对象

        Returns:
            bool: 是否应该继续处理
        """
        # 检查 is_at_or_wake_command
        if not event.is_at_or_wake_command:
            return False

        message_outline = event.get_message_outline()

        # 黑名单检查
        if await self._is_blacklisted(message_outline):
            logger.info("AngelEye: 消息命中黑名单，跳过处理。")
            return False

        # 白名单检查
        if await self._is_whitelisted(message_outline):
            logger.info("AngelEye: 消息未命中白名单，跳过处理。")
            return False

        return True

    async def _is_blacklisted(self, message_outline: str) -> bool:
        """检查消息是否在黑名单中"""
        blacklist_keywords_str = self.config.get("blacklist_keywords", "")
        if not blacklist_keywords_str:
            return False

        blacklist_keywords = [kw.strip() for kw in blacklist_keywords_str.split('|') if kw.strip()]
        return any(keyword in message_outline for keyword in blacklist_keywords)

    async def _is_whitelisted(self, message_outline: str) -> bool:
        """检查消息是否在白名单中（如果白名单启用）"""
        whitelist_enabled = self.config.get("whitelist_enabled", False)
        if not whitelist_enabled:
            return False

        whitelist_keywords_str = self.config.get("whitelist_keywords", "")
        if not whitelist_keywords_str:
            # 白名单开启但列表为空，不匹配任何消息
            return True

        whitelist_keywords = [kw.strip() for kw in whitelist_keywords_str.split('|') if kw.strip()]
        return not any(keyword in message_outline for keyword in whitelist_keywords)

    async def _validate_and_get_providers(self) -> Optional[Tuple]:
        """
        验证配置并获取所需的提供商

        Returns:
            Optional[Tuple]: (classifier_provider, filter_provider, summarizer_provider) 或 None
        """
        # 获取模型配置
        classifier_model_id = self.config.get("classifier_model_id")
        filter_model_id = self.config.get("filter_model_id")
        summarizer_model_id = self.config.get("summarizer_model_id")

        # 检查必需的模型配置
        required_model_configs = {
            "classifier_model_id": classifier_model_id,
            "filter_model_id": filter_model_id,
            "summarizer_model_id": summarizer_model_id
        }

        missing_configs = [key for key, value in required_model_configs.items() if not value]
        if missing_configs:
            logger.info(f"AngelEye: 未设置模型配置: {', '.join(missing_configs)}，跳过上下文增强")
            return None

        # 检查数据源配置
        if not self._are_data_sources_enabled():
            logger.info("AngelEye: 未启用任何数据源，跳过上下文增强")
            return None

        # 获取提供商
        classifier_provider = self.context.get_provider_by_id(classifier_model_id) if classifier_model_id else None
        filter_provider = self.context.get_provider_by_id(filter_model_id) if filter_model_id else None
        summarizer_provider = self.context.get_provider_by_id(summarizer_model_id) if summarizer_model_id else None

        # 验证提供商可用性
        if not all([classifier_provider, filter_provider, summarizer_provider]):
            missing = []
            if not classifier_provider:
                missing.append(f"分类模型({classifier_model_id})")
            if not filter_provider:
                missing.append(f"筛选模型({filter_model_id})")
            if not summarizer_provider:
                missing.append(f"摘要模型({summarizer_model_id})")
            logger.info(f"AngelEye: 以下模型未找到，跳过上下文增强: {', '.join(missing)}")
            return None

        return (classifier_provider, filter_provider, summarizer_provider)

    def _are_data_sources_enabled(self) -> bool:
        """检查是否启用了任何数据源"""
        return (
            self.config.get("moegirl_enabled", False) or
            self.config.get("wikipedia_enabled", False) or
            self.config.get("wikidata_enabled", False)
        )

    async def _execute_knowledge_enrichment(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        providers: Tuple
    ) -> None:
        """
        执行知识增强流程

        Args:
            event: AstrMessageEvent 事件对象
            req: ProviderRequest 请求对象
            providers: (classifier_provider, filter_provider, summarizer_provider) 元组
        """
        classifier_provider, filter_provider, summarizer_provider = providers

        # 创建角色实例
        classifier = Classifier(classifier_provider, self.config)
        smart_retriever = SmartRetriever(filter_provider, summarizer_provider, self.config)

        logger.info("AngelEye: 开始上下文增强流程")
        original_prompt = req.prompt

        try:
            # 1. 获取对话记录
            formatted_dialogue = self._get_dialogue_records(event, req.contexts, original_prompt)
            if not formatted_dialogue:
                logger.info("AngelEye: 无需补充知识，流程结束。")
                return

            # 2. 获取知识请求
            knowledge_request = await self._get_knowledge_request(event, req, classifier)
            if not knowledge_request:
                logger.info("AngelEye: 无需补充知识，流程结束。")
                return

            # 3. 执行智能检索
            knowledge_result = await self._perform_intelligent_retrieval(
                knowledge_request, formatted_dialogue, event, smart_retriever
            )
            if not knowledge_result or not knowledge_result.chunks:
                logger.info("AngelEye: 未检索到有效知识，流程结束。")
                return

            # 4. 注入上下文
            await self._inject_context_into_request(req, knowledge_result)
            logger.info("AngelEye: 成功注入知识，流程结束。")

        except AngelEyeError as e:
            logger.error(f"AngelEye: 在上下文增强流程中发生错误: {e}", exc_info=True)
            # 发生内部错误时，静默失败，不影响主流程
            return

    async def _get_knowledge_request(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
        classifier: Classifier
    ) -> Optional[KnowledgeRequest]:
        """
        获取知识请求，优先使用天使之心的决策

        Args:
            event: AstrMessageEvent 事件对象
            req: ProviderRequest 请求对象
            classifier: Classifier 分类器实例

        Returns:
            Optional[KnowledgeRequest]: 知识请求对象
        """
        # 检查天使之心是否已经生成了查询请求
        knowledge_request = self._try_get_angelheart_request(event)
        if knowledge_request:
            logger.info("AngelEye: 步骤 1/3 - 使用天使之心决策完成")
            logger.debug(f"  - 天使之心请求: {knowledge_request}")
            return knowledge_request

        # 使用自己的分类器
        knowledge_request = await classifier.get_knowledge_request(req.contexts, req.prompt)
        logger.info("AngelEye: 步骤 1/3 - 分类器分析完成")
        logger.debug(f"  - 分类结果 (KnowledgeRequest): {knowledge_request}")

        return knowledge_request

    def _try_get_angelheart_request(self, event: AstrMessageEvent) -> Optional[KnowledgeRequest]:
        """
        尝试从天使之心上下文中获取知识请求

        Args:
            event: AstrMessageEvent 事件对象

        Returns:
            Optional[KnowledgeRequest]: 天使之心生成的知识请求
        """
        if not hasattr(event, 'angelheart_context') or not event.angelheart_context:
            return None

        try:
            import json
            context_data = json.loads(event.angelheart_context)
            secretary_decision = context_data.get('secretary_decision', {})

            if isinstance(secretary_decision, dict) and 'angel_eye_request' in secretary_decision:
                request_data = secretary_decision['angel_eye_request']
                return KnowledgeRequest(
                    required_docs=request_data.get('required_docs', {}),
                    required_facts=request_data.get('required_facts', []),
                    chat_history=request_data.get('chat_history', {})
                )
        except Exception as e:
            logger.warning(f"AngelEye: 解析天使之心请求失败: {e}")
            return None

    async def _perform_intelligent_retrieval(
        self,
        knowledge_request: KnowledgeRequest,
        formatted_dialogue: str,
        event: AstrMessageEvent,
        smart_retriever: SmartRetriever
    ):
        """
        执行智能知识检索

        Args:
            knowledge_request: KnowledgeRequest 对象
            formatted_dialogue: 格式化的对话记录
            event: AstrMessageEvent 事件对象
            smart_retriever: SmartRetriever 实例

        Returns:
            检索结果对象
        """
        logger.debug(f"AngelEye: 准备调用SmartRetriever，knowledge_request={knowledge_request}")
        knowledge_result = await smart_retriever.retrieve(knowledge_request, formatted_dialogue, event)
        logger.info("AngelEye: 步骤 2/3 - 智能检索完成")

        if knowledge_result and knowledge_result.chunks:
            logger.debug(f"  - 检索到 {len(knowledge_result.chunks)} 个知识片段")

        return knowledge_result

    async def _inject_context_into_request(self, req: ProviderRequest, knowledge_result) -> None:
        """
        将知识结果注入到请求中

        Args:
            req: ProviderRequest 请求对象
            knowledge_result: 知识检索结果
        """
        background_knowledge = knowledge_result.to_context_string()
        if not background_knowledge:
            logger.info("AngelEye: 背景知识为空，流程结束。")
            return

        # 构建注入文本，使用[RAG-百科]标识
        rag_content = f"[RAG-百科] 相关信息参考:\n{background_knowledge}"

        logger.info("AngelEye: 步骤 3/3 - 准备注入上下文")
        logger.debug(f"  - 注入的背景知识内容: {background_knowledge[:50]}...")

        # 将百科内容作为新的用户消息添加到上下文末尾
        req.contexts.append({
            "role": "user",
            "content": rag_content
        })
