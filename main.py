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
from .core.formatter import format_unified_message, format_angelheart_message


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

    def _get_dialogue_records(self, event: AstrMessageEvent, req_contexts: list, original_prompt: str) -> list:
        """
        获取对话记录的统一方法
        优先使用天使之心提供的上下文，如果没有则使用现有逻辑

        Args:
            event: AstrMessageEvent 事件对象
            req_contexts: 请求上下文列表
            original_prompt: 原始提示词

        Returns:
            list: 格式化后的对话记录列表，如果不需要搜索则返回空列表
        """
        # 1. 检查是否存在天使之心上下文
        if hasattr(event, 'angelheart_context'):
            try:
                context = json.loads(event.angelheart_context)

                # 检查是否需要搜索
                needs_search = context.get('needs_search', False)
                if not needs_search:
                    logger.info("AngelEye: 天使之心指示不需要搜索，跳过知识检索")
                    return []

                # 使用专门的天使之心格式化工具处理聊天记录
                chat_records = context.get('chat_records', [])
                formatted_records = []
                for record in chat_records:
                    formatted_record = format_angelheart_message(record)
                    formatted_records.append(formatted_record)

                logger.info(f"AngelEye: 使用天使之心上下文，记录数: {len(formatted_records)}")
                return formatted_records

            except json.JSONDecodeError as e:
                logger.warning(f"AngelEye: 解析天使之心上下文失败: {e}")
            except Exception as e:
                logger.warning(f"AngelEye: 处理天使之心上下文时发生错误: {e}")

        # 2. 如果没有天使之心上下文，使用现有逻辑构建对话记录
        dialogue_parts = []
        for item in req_contexts:
            dialogue_parts.append(format_unified_message(item))

        # 处理当前消息
        current_message_dict = {
            "role": "user",
            "content": original_prompt
        }
        dialogue_parts.append(format_unified_message(current_message_dict))

        formatted_dialogue = "\n".join(dialogue_parts)
        logger.info(f"AngelEye: 使用现有逻辑构建对话记录，记录数: {len(dialogue_parts)}")

        return [formatted_dialogue]

    @filter.on_llm_request(priority=-50)
    async def enrich_context_before_llm_call(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在主模型请求前，执行上下文增强逻辑
        这是 Angel Eye 的核心入口点，使用新的智能知识获取架构
        """
        # ==================== 新增前置检查逻辑 START ====================

        # 1. 检查 is_at_or_wake_command
        if not event.is_at_or_wake_command:
            return

        message_outline = event.get_message_outline()

        # 2. 黑名单检查
        blacklist_keywords_str = self.config.get("blacklist_keywords", "")
        if blacklist_keywords_str:
            blacklist_keywords = [kw.strip() for kw in blacklist_keywords_str.split('|') if kw.strip()]
            if any(keyword in message_outline for keyword in blacklist_keywords):
                logger.info("AngelEye: 消息命中黑名单，跳过处理。")
                return

        # 3. 白名单检查
        whitelist_enabled = self.config.get("whitelist_enabled", False)
        if whitelist_enabled:
            whitelist_keywords_str = self.config.get("whitelist_keywords", "")
            if not whitelist_keywords_str:  # 如果白名单开启但列表为空，则不匹配任何消息
                logger.info("AngelEye: 白名单已启用但列表为空，跳过处理。")
                return

            whitelist_keywords = [kw.strip() for kw in whitelist_keywords_str.split('|') if kw.strip()]
            if not any(keyword in message_outline for keyword in whitelist_keywords):
                logger.info("AngelEye: 消息未命中白名单，跳过处理。")
                return

        # ==================== 新增前置检查逻辑 END ======================

        # 在事件处理时即时获取三个独立的 Provider
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
            return

        # 检查是否启用了任何数据源
        data_sources_enabled = (
            self.config.get("moegirl_enabled", False) or
            self.config.get("wikipedia_enabled", False) or
            self.config.get("wikidata_enabled", False)
        )

        if not data_sources_enabled:
            logger.info("AngelEye: 未启用任何数据源，跳过上下文增强")
            return

        # 安全地获取Provider
        classifier_provider = self.context.get_provider_by_id(classifier_model_id) if classifier_model_id else None
        filter_provider = self.context.get_provider_by_id(filter_model_id) if filter_model_id else None
        summarizer_provider = self.context.get_provider_by_id(summarizer_model_id) if summarizer_model_id else None

        # 验证Provider可用性
        if not all([classifier_provider, filter_provider, summarizer_provider]):
            missing = []
            if not classifier_provider: missing.append(f"分类模型({classifier_model_id})")
            if not filter_provider: missing.append(f"筛选模型({filter_model_id})")
            if not summarizer_provider: missing.append(f"摘要模型({summarizer_model_id})")
            logger.info(f"AngelEye: 以下模型未找到，跳过上下文增强: {', '.join(missing)}")
            return

        # 在每次请求时创建新的角色实例，避免状态污染
        classifier = Classifier(classifier_provider)
        smart_retriever = SmartRetriever(filter_provider, summarizer_provider, self.config)

        logger.info("AngelEye: 开始上下文增强流程")
        original_prompt = req.prompt

        try:
            # 1. 获取对话记录（统一处理天使之心上下文和现有逻辑）
            dialogue_records = self._get_dialogue_records(event, req.contexts, original_prompt)

            # 如果对话记录为空（天使之心指示不需要搜索），直接返回
            if not dialogue_records:
                logger.info("AngelEye: 无需补充知识，流程结束。")
                return

            # 2. 调用Classifier生成轻量级知识请求指令
            knowledge_request = await classifier.get_knowledge_request(req.contexts, original_prompt)
            logger.info(f"AngelEye: 步骤 1/3 - 分类器分析完成")
            logger.debug(f"  - 分类结果 (KnowledgeRequest): {knowledge_request}")

            # 如果没有需要查询的知识，直接返回
            if not knowledge_request:
                logger.info("AngelEye: 无需补充知识，流程结束。")
                return

            # 3. 格式化对话历史，供 Summarizer 使用
            # 使用统一的对话记录
            formatted_dialogue = "\n".join(dialogue_records)

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
            all_personas = [name.strip() for name in persona_names_str.split('|')]
            persona_list_str = "、".join(all_personas)

            # 构建包含身份提醒和背景知识的注入文本
            injection_text = (
                f"\n\n---\n"
                f"[天使之眼] 我的别名是 {persona_list_str}。"
                f"如下是我脑海中浮现出的上下文可能相关的信息，仅作参考。\n\n"
                f"[相关信息参考]:\n{background_knowledge}\n"
                f"---"
            )

            logger.info("AngelEye: 步骤 3/3 - 准备注入上下文")
            logger.debug(f"  - 注入的背景知识内容: {background_knowledge}")

            # 原有的注入代码
            req.system_prompt = (req.system_prompt or '') + injection_text
            logger.info(f"AngelEye: 成功注入知识，流程结束。")

        except AngelEyeError as e:
            logger.error(f"AngelEye: 在上下文增强流程中发生错误: {e}", exc_info=True)
            # 发生内部错误时，静默失败，不影响主流程
            return
