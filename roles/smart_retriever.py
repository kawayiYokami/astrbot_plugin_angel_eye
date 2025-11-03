"""
智能知识检索器 (SmartRetriever)
实现动态执行策略，根据搜索结果智能决策处理方式
"""
import logging
from typing import List, Dict, Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent

from astrbot.api.provider import Provider
from ..models.request import KnowledgeRequest
from ..models.knowledge import KnowledgeChunk, KnowledgeResult
from ..clients.wikipedia_client import WikipediaClient
from ..clients.moegirl_client import MoegirlClient
from ..clients.wikidata_client import WikidataClient
from .filter import Filter
from .summarizer import Summarizer
# from .retriever_qqchat import QQChatHistoryRetriever # 移除旧的导入
from ..services.qq_history_service import QQChatHistoryService # 导入新的服务
from ..core.wikitext_cleaner import clean as clean_wikitext
from ..core.cache_manager import get, set, build_doc_key, build_search_key

logger = logging.getLogger(__name__)



class SmartRetriever:
    """
    智能知识检索器，实现动态执行策略
    - 根据文本长度决定是否调用AI进行归纳
    - 处理完全匹配、模糊匹配、无匹配的不同情况
    - 遵循"宁缺毋滥"原则
    """

    def __init__(self, filter_provider: Provider, summarizer_provider: Provider, config: Dict):
        """
        初始化智能检索器

        :param filter_provider: 用于 Filter 角色的 Provider
        :param summarizer_provider: 用于 Summarizer 角色的 Provider
        :param config: 配置字典
        """
        self.config = config
        self.filter_provider = filter_provider
        self.summarizer_provider = summarizer_provider

        # 从配置中获取参数，使用默认值
        self.TEXT_LENGTH_THRESHOLD = self.config.get("text_length_threshold", 500)
        self.max_search_results = self.config.get("max_search_results", 5)

        # 初始化各种客户端
        self.wikipedia_client = WikipediaClient(config)
        self.moegirl_client = MoegirlClient(config)
        self.wikidata_client = WikidataClient()

        # 初始化子角色和服务 - 延迟初始化
        self.filter: Filter | None = None
        self.summarizer: Summarizer | None = None
        # self.qq_chat_history_retriever: QQChatHistoryRetriever | None = None # 移除旧的
        self.qq_history_service: QQChatHistoryService | None = None # 初始化新的服务

    def normalize_string(self, s: str) -> str:
        """
        规范化字符串，用于健壮的标题匹配。
        """
        # 示例实现：转小写、去首尾空格
        # 可根据需要添加更多规则，如全角/半角转换、移除特殊符号等
        return s.lower().strip()

    async def retrieve(self, request: KnowledgeRequest, formatted_dialogue: str, event: 'AstrMessageEvent') -> KnowledgeResult:
        # 即时初始化子角色和服务
        if self.filter is None:
            self.filter = Filter(self.filter_provider)
        if self.summarizer is None:
            self.summarizer = Summarizer(self.summarizer_provider, self.config)
        if self.qq_history_service is None: # 初始化新的服务
            self.qq_history_service = QQChatHistoryService()

        """
        核心方法：根据知识请求执行智能检索

        :param request: 知识请求对象
        :param formatted_dialogue: 格式化后的对话历史
        :param event: 事件对象，用于获取 bot 和 session_id
        :return: 知识结果对象
        """
        result = KnowledgeResult()

        # 步骤1：优先处理精确事实查询 (如果Wikidata已启用)
        if request.required_facts and self.config.get("wikidata_enabled", True):
            logger.info(f"AngelEye: 开始处理 {len(request.required_facts)} 个事实查询...")
            fact_chunks = await self._process_facts(request.required_facts)
            result.chunks.extend(fact_chunks)
            
            # 如果只有事实查询，没有文档查询，直接返回
            if not request.required_docs:
                logger.info("AngelEye: 仅事实查询，处理完成")
                return result
        elif request.required_facts:
            logger.info("AngelEye: Wikidata未启用，跳过事实查询")


        # 步骤2：处理文档请求
        if request.required_docs:
            logger.info(f"AngelEye: 开始处理 {len(request.required_docs)} 个文档查询...")
            for entity_name, doc_info in request.required_docs.items():
                # 新格式：doc_info 是包含 keywords 的对象
                if isinstance(doc_info, dict):
                    keywords = doc_info.get("keywords", [])
                    # source 参数已忽略，内部按优先级处理
                    source = None
                else:
                    # 兼容旧格式：doc_info 是字符串（数据源名称）
                    source = doc_info
                    keywords = []
                
                # 直接调用 _process_document，内部会按优先级尝试数据源
                doc_chunk = await self._process_document(entity_name, source, keywords, formatted_dialogue, event)
                if doc_chunk:
                    result.chunks.append(doc_chunk)
        
        # 步骤3：处理聊天记录请求（新格式）
        logger.debug(f"AngelEye: 检查聊天记录参数: {request.chat_history}")
        if request.chat_history:
            logger.info("AngelEye: 处理聊天记录查询...")
            doc_chunk = await self._process_qq_chat_history_new(request.chat_history, formatted_dialogue, event)
            if doc_chunk:
                result.chunks.append(doc_chunk)
            else:
                logger.info("AngelEye: 聊天记录查询返回空结果")
        # 兼容旧格式
        elif request.parameters and "qq_chat_history" in request.required_docs.values():
            logger.info("AngelEye: 处理聊天记录查询（旧格式）...")
            entity_name = next(k for k, v in request.required_docs.items() if v == "qq_chat_history")
            doc_chunk = await self._process_qq_chat_history(entity_name, request.parameters, formatted_dialogue, event)
            if doc_chunk:
                result.chunks.append(doc_chunk)
        else:
            logger.debug("AngelEye: 无聊天记录查询参数")

        logger.info(f"AngelEye: 检索完成，共获取 {len(result.chunks)} 个知识片段")
        return result

    async def _process_facts(self, required_facts: List[str]) -> List[KnowledgeChunk]:
        """
        处理结构化事实查询 (新格式: [context].entity.property)

        :param required_facts: 事实列表，每个元素是 "[context].entity.property" 或 "entity.property" 格式的字符串
        :return: 知识片段列表
        """
        import asyncio
        import re

        # 1. 将每个事实字符串解析成一个 query_plan 对象
        query_plans = []
        for fact_str in required_facts:
            match = re.match(r"\[([^\]]+)\]\.(.+)", fact_str)
            if match:
                # 格式: [context].entity.property
                keywords = match.group(1)
                targets = match.group(2)
                query_plans.append({"targets": targets, "filter_keywords_en": keywords})
            else:
                # 格式: entity.property
                query_plans.append({"targets": fact_str, "filter_keywords_en": ""})

        # 2. 并发执行所有查询计划
        if not query_plans:
            return []
        
        tasks = [self.wikidata_client.execute_query(plan) for plan in query_plans]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 3. 将返回的结果格式化为 KnowledgeChunk 列表
        chunks = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"查询 '{required_facts[i]}' 时出错: {res}")
                continue
            
            final_facts = res.get("final_facts", {})
            if final_facts:
                content_lines = [f"- {name}: {value}" for name, value in final_facts.items()]
                # 从 targets 中提取实体名 (支持 | 分隔的多个目标)
                entity_name = query_plans[i]['targets'].split('.')[0].split('|')[0].strip()
                chunks.append(KnowledgeChunk(
                    source="wikidata",
                    entity=entity_name,
                    content="\n".join(content_lines)
                ))
        
        return chunks


    def _choose_datasource(self, entity_name: str, keywords: List[str]) -> str:
        """
        已废弃：数据源选择逻辑已集成到 _process_document 方法中
        保留此方法仅为向后兼容
        
        :param entity_name: 实体名称
        :param keywords: 关键词列表
        :return: 固定返回 "moegirl"
        """
        # 固定返回萌娘百科，实际选择逻辑在 _process_document 中处理
        return "moegirl"

    async def _process_document(self, entity_name: str, source: str, keywords: List[str] = None, formatted_dialogue: str = "", event: 'AstrMessageEvent' = None) -> Optional[KnowledgeChunk]:
        """
        处理单个文档请求，实现萌娘优先策略

        :param entity_name: 实体名称
        :param source: 数据源 (已忽略，内部优先使用萌娘)
        :param keywords: 关键词列表 (已忽略)
        :param formatted_dialogue: 格式化后的对话历史
        :param event: 事件对象 (为保持签名一致而保留，当前实现中未使用)
        :return: 知识片段，如果未找到则返回None
        """
        # 尝试数据源的优先级顺序
        sources_to_try = []
        
        # 如果指定了source且不是qq_chat_history，则优先尝试指定的source
        if source and source != "qq_chat_history":
            sources_to_try.append(source)
        
        # 默认优先级：萌娘百科 -> 维基百科
        if "moegirl" not in sources_to_try and self.config.get("moegirl_enabled", True):
            sources_to_try.append("moegirl")
        if "wikipedia" not in sources_to_try and self.config.get("wikipedia_enabled", True):
            sources_to_try.append("wikipedia")
        
        # 按优先级尝试每个数据源
        for try_source in sources_to_try:
            logger.info(f"AngelEye: 尝试在 {try_source} 搜索 '{entity_name}'...")
            
            # 1. 检查搜索结果列表的缓存
            search_cache_key = build_search_key(try_source, entity_name)
            cached_search_results = await get(search_cache_key)

            if cached_search_results:
                logger.debug(f"AngelEye: 命中搜索结果缓存 (Key: {search_cache_key})")
                search_results = cached_search_results
            else:
                # 选择对应的客户端
                client = self._get_client(try_source)
                if not client:
                    logger.warning(f"AngelEye: 不支持的数据源 '{try_source}'")
                    continue

                # 执行搜索
                search_results = await client.search(entity_name, limit=self.max_search_results)
                if search_results:
                    logger.debug(f"AngelEye: 将新搜索结果存入缓存 (Key: {search_cache_key})")
                    await set(search_cache_key, search_results) # 缓存整个列表

            if not search_results:
                logger.info(f"AngelEye: '{entity_name}' 在 {try_source} 中无搜索结果，尝试下一个数据源")
                continue
            
            # 找到结果，处理这个数据源的结果
            logger.info(f"AngelEye: 在 {try_source} 找到 {len(search_results)} 个结果")
            
            # 2. 优化筛选逻辑：单结果直接返回
            selected_entry = None
            selected_pageid = None
            
            # Case A: 检查是否有完全匹配
            for result in search_results:
                if self.normalize_string(result.get("title", "")) == self.normalize_string(entity_name):
                    selected_entry = result["title"]
                    selected_pageid = result.get("pageid")
                    logger.info(f"AngelEye: 找到完全匹配 '{selected_entry}'")
                    break
            
            # Case B: 模糊匹配
            if not selected_entry:
                if len(search_results) == 1:
                    # 只有一个结果，直接使用
                    logger.info("AngelEye: 只有一个搜索结果，直接使用")
                    selected_entry = search_results[0]["title"]
                    selected_pageid = search_results[0].get("pageid")
                elif self.config.get("filter_enabled", False):
                    # 多个结果且启用了过滤器，调用Filter筛选
                    logger.info(f"AngelEye: 无完全匹配，调用Filter从 {len(search_results)} 个结果中筛选...")
                    
                    # 构造候选列表供Filter使用
                    candidate_list = [
                        {
                            "title": r["title"],
                            "snippet": r.get("snippet", ""),
                            "url": r.get("url", "")
                        }
                        for r in search_results
                    ]

                    # 调用Filter进行筛选
                    selected_title = await self.filter.select_best_entry(
                        contexts=[],
                        current_prompt=formatted_dialogue,
                        entity_name=entity_name,
                        candidate_list=candidate_list
                    )

                    if selected_title:
                        # 找到对应的pageid
                        for result in search_results:
                            if result["title"] == selected_title:
                                selected_entry = selected_title
                                selected_pageid = result.get("pageid")
                                logger.info(f"AngelEye: Filter选择了 '{selected_entry}'")
                                break
                else:
                    # 多个结果但未启用过滤器，使用第一个
                    logger.info("AngelEye: 智能筛选功能已禁用，默认使用第一个搜索结果")
                    selected_entry = search_results[0]["title"]
                    selected_pageid = search_results[0].get("pageid")
                    logger.info(f"AngelEye: 选择了第一个结果 '{selected_entry}'")
            
            # Case C: 无匹配
            if not selected_entry:
                logger.info(f"AngelEye: '{entity_name}' 在 {try_source} 中无相关词条，尝试下一个数据源")
                continue
            
            # 3. 获取页面内容
            cache_key = build_doc_key(try_source, selected_entry)
            cached_content = await get(cache_key)

            if cached_content:
                logger.debug(f"AngelEye: 命中原始页面缓存 (Key: {cache_key})")
                full_content = cached_content
            else:
                logger.info(f"AngelEye: 缓存未命中，获取 '{selected_entry}' 的全文内容...")
                
                # 获取选中词条的全文
                full_content = await client.get_page_content(selected_entry, pageid=selected_pageid)
                if not full_content:
                    logger.warning(f"AngelEye: 无法获取 '{selected_entry}' 的内容，尝试下一个数据源")
                    continue
                else:
                    # 成功获取后，立即缓存原始内容
                    logger.debug(f"AngelEye: 成功获取 '{selected_entry}' 的全文，存入缓存...")
                    await set(cache_key, full_content)
            
            # 4. 内容处理
            if len(full_content) > self.TEXT_LENGTH_THRESHOLD:
                if self.config.get("wiki_summarizer_enabled", True):
                    logger.info(f"AngelEye: 内容过长 ({len(full_content)} > {self.TEXT_LENGTH_THRESHOLD})，调用Summarizer进行摘要...")
                    final_content = await self.summarizer.summarize(
                        source=try_source,
                        full_content=full_content,
                        entity_name=selected_entry,
                        dialogue=formatted_dialogue
                    )
                else:
                    logger.info(f"AngelEye: 内容过长 ({len(full_content)} > {self.TEXT_LENGTH_THRESHOLD})，但摘要功能已禁用，直接使用清洗后原文...")
                    final_content = clean_wikitext(full_content)
            else:
                final_content = clean_wikitext(full_content)
            
            # 5. 构建并返回 KnowledgeChunk
            if final_content:
                return KnowledgeChunk(
                    source=try_source,
                    entity=entity_name,
                    content=final_content,
                    source_url=search_results[0].get("url") if search_results else None
                )
        
        # 所有数据源都尝试失败
        logger.info(f"AngelEye: '{entity_name}' 在所有数据源中都无相关词条")
        return None

        # 2. 实现智能决策点 (完全匹配 / Filter)
        selected_entry = None
        selected_pageid = None

        # Case A: 检查是否有完全匹配
        for result in search_results:
            if self.normalize_string(result.get("title", "")) == self.normalize_string(entity_name):
                selected_entry = result["title"]
                selected_pageid = result.get("pageid")
                logger.info(f"AngelEye: 找到完全匹配 '{selected_entry}'")
                break

        # Case B: 模糊匹配，根据配置决定是否调用Filter
        if not selected_entry and len(search_results) > 0:
            if self.config.get("filter_enabled", False):
                # 启用了过滤器，调用Filter进行智能筛选
                logger.info(f"AngelEye: 无完全匹配，调用Filter从 {len(search_results)} 个结果中筛选...")

                # 构造候选列表供Filter使用
                candidate_list = [
                    {
                        "title": r["title"],
                        "snippet": r.get("snippet", ""),
                        "url": r.get("url", "")
                    }
                    for r in search_results
                ]

                # 调用Filter进行筛选
                # 将格式化后的对话历史传递给Filter
                selected_title = await self.filter.select_best_entry(
                    contexts=[],  # 保持为空，因为formatted_dialogue已经格式化
                    current_prompt=formatted_dialogue, # 直接传递格式化后的formatted_dialogue
                    entity_name=entity_name,
                    candidate_list=candidate_list
                )

                if selected_title:
                    # 找到对应的pageid
                    for result in search_results:
                        if result["title"] == selected_title:
                            selected_entry = selected_title
                            selected_pageid = result.get("pageid")
                            logger.info(f"AngelEye: Filter选择了 '{selected_entry}'")
                            break
            else:
                # 未启用过滤器，直接使用第一个搜索结果
                logger.info("AngelEye: 智能筛选功能已禁用，默认使用第一个搜索结果")
                selected_entry = search_results[0]["title"]
                selected_pageid = search_results[0].get("pageid")
                logger.info(f"AngelEye: 选择了第一个结果 '{selected_entry}'")

        # Case C: 无匹配
        if not selected_entry:
            logger.info(f"AngelEye: '{entity_name}' 无相关词条，遵循宁缺毋滥原则")
            return None

        # 3. 检查原始页面内容的缓存
        cache_key = build_doc_key(source, selected_entry)
        cached_content = await get(cache_key)

        if cached_content:
            logger.debug(f"AngelEye: 命中原始页面缓存 (Key: {cache_key})")
            full_content = cached_content
        else:
            logger.info(f"AngelEye: 缓存未命中，获取 '{selected_entry}' 的全文内容...")
            # 选择对应的客户端 (如果之前没有选择过)
            if 'client' not in locals():
                client = self._get_client(source)
                if not client:
                    logger.warning(f"AngelEye: 不支持的数据源 '{source}'")
                    return None

            # 获取选中词条的全文
            full_content = await client.get_page_content(selected_entry, pageid=selected_pageid)
            if not full_content:
                logger.warning(f"AngelEye: 无法获取 '{selected_entry}' 的内容")
                return None
            else:
                # 成功获取后，立即缓存原始内容，以便下次快速复用
                logger.debug(f"AngelEye: 成功获取 '{selected_entry}' 的全文，存入缓存...")
                await set(cache_key, full_content)


        # 4. 内容过长则根据开关决定是否调用AI进行归纳，否则只做清洗
        if len(full_content) > self.TEXT_LENGTH_THRESHOLD:
            if self.config.get("wiki_summarizer_enabled", True):
                logger.info(f"AngelEye: 内容过长 ({len(full_content)} > {self.TEXT_LENGTH_THRESHOLD})，调用Summarizer进行摘要...")
                final_content = await self.summarizer.summarize(
                    source=source,
                    full_content=full_content,
                    entity_name=selected_entry,
                    dialogue=formatted_dialogue
                )
            else:
                logger.info(f"AngelEye: 内容过长 ({len(full_content)} > {self.TEXT_LENGTH_THRESHOLD})，但摘要功能已禁用，直接使用清洗后原文...")
                final_content = clean_wikitext(full_content)
        else:
            final_content = clean_wikitext(full_content) # 如果内容不长，只做清洗

        # 5. 构建并返回 KnowledgeChunk
        if final_content:
            return KnowledgeChunk(
                source=source,
                entity=entity_name,
                content=final_content,
                source_url=search_results[0].get("url") if search_results else None
            )

        return None

    def _get_client(self, source: str):
        """
        根据数据源名称获取对应的客户端

        :param source: 数据源名称
        :return: 对应的客户端实例，对于 qq_chat_history 返回其 retriever 实例
        """
        if source == "wikipedia":
            return self.wikipedia_client
        elif source == "moegirl":
            return self.moegirl_client
        elif source == "qq_chat_history":
            # 对于 qq_chat_history，我们返回其专用的 retriever 实例
            return self.qq_chat_history_retriever
        else:
            return None

    async def _process_qq_chat_history(self, entity_name: str, parameters: Dict[str, Any], formatted_dialogue: str, event: 'AstrMessageEvent') -> Optional[KnowledgeChunk]:
        """
        处理 QQ 群聊历史记录请求。

        :param entity_name: 实体名称（用户对聊天内容的描述）
        :param parameters: 来自 Classifier 的参数，包含 time_range_hours, message_count, summarize
        :param formatted_dialogue: 格式化后的对话历史
        :param event: 事件对象，用于获取 bot 和 group_id
        :return: 知识片段，如果未找到则返回None
        """
        # 确保服务已初始化
        if self.qq_history_service is None:
            self.qq_history_service = QQChatHistoryService()

        # 从 parameters 中提取参数
        hours = parameters.get("time_range_hours")
        count = parameters.get("message_count")
        needs_summary = parameters.get("summarize", False)

        # 从 event 对象中获取 bot 和 group_id
        bot = event.bot
        group_id = event.get_group_id()

        # 防御性检查：必须在群聊中才能获取聊天记录
        if not group_id or not bot:
            logger.warning("AngelEye: 不在群聊中或无法获取bot实例，无法获取聊天记录。")
            return None

        try:
            # 1. 调用 QQChatHistoryService 获取已格式化的消息列表
            logger.info(f"AngelEye: 开始获取群 {group_id} 的聊天记录...")
            formatted_messages: List[str] = await self.qq_history_service.get_messages(
                bot=bot,
                group_id=group_id,
                hours=hours,
                count=count
            )

            if not formatted_messages:
                logger.info(f"AngelEye: 未能获取到群 {group_id} 的聊天记录 '{entity_name}'")
                return None

            # 2. 准备两个独立的上下文
            # 'formatted_dialogue' 参数本身就是最新的对话上下文
            latest_dialogue = formatted_dialogue
            # 拼接历史聊天记录
            historical_chat = "\n".join(formatted_messages)

            # 3. 根据 needs_summary 标志和开关决定是否调用 Summarizer
            if needs_summary and self.config.get("chat_summarizer_enabled", True):
                # 如果需要总结，且开关开启，才调用 Summarizer
                logger.info("AngelEye: 需要精选聊天记录，调用Summarizer...")
                final_content = await self.summarizer.summarize(
                    source="qq_chat_history",
                    full_content=historical_chat,
                    entity_name=entity_name,
                    dialogue=latest_dialogue
                )
                # 如果总结失败，提供一个降级方案（例如，返回原始记录的片段）
                if not final_content:
                    logger.warning("AngelEye: 聊天记录分析失败，返回原始记录摘要。")
                    final_content = f"关于“{entity_name}”的讨论摘要：\n" + historical_chat[:500] + "..."
            else:
                # 如果不需要总结，或开关关闭，直接使用原始记录
                action = "无需精选" if not needs_summary else "摘要功能已禁用"
                logger.info(f"AngelEye: {action}聊天记录，直接返回原文。")
                final_content = historical_chat

            # 5. 构建并返回 KnowledgeChunk
            return KnowledgeChunk(
                source="qq_chat_history",
                entity=entity_name,
                content=final_content
            )

        except Exception as e:
            logger.error(f"AngelEye: 处理群 {group_id} 的聊天记录 '{entity_name}' 时出错: {e}", exc_info=True)
            return None

    async def _process_qq_chat_history_new(self, chat_params: Dict[str, Any], formatted_dialogue: str, event: 'AstrMessageEvent') -> Optional[KnowledgeChunk]:
        """
        处理 QQ 群聊历史记录请求（新格式）。

        :param chat_params: 聊天记录查询参数
        :param formatted_dialogue: 格式化后的对话历史
        :param event: 事件对象，用于获取 bot 和 group_id
        :return: 知识片段，如果未找到则返回None
        """
        # 确保服务已初始化
        if self.qq_history_service is None:
            self.qq_history_service = QQChatHistoryService()

        # 从参数中提取信息
        hours = chat_params.get("time_range_hours")
        filter_user_ids = chat_params.get("filter_user_ids", [])
        keywords = chat_params.get("keywords", [])
        
        # 从 event 对象中获取 bot 和 group_id
        bot = event.bot
        group_id = event.get_group_id()

        # 防御性检查：必须在群聊中才能获取聊天记录
        if not group_id or not bot:
            logger.warning("AngelEye: 不在群聊中或无法获取bot实例，无法获取聊天记录。")
            return None

        try:
            # 1. 调用 QQChatHistoryService 获取已格式化的消息列表
            logger.info(f"AngelEye: 开始获取群 {group_id} 的聊天记录...")
            formatted_messages: List[str] = await self.qq_history_service.get_messages(
                bot=bot,
                group_id=group_id,
                hours=hours,
                filter_user_ids=filter_user_ids,
                keywords=keywords
            )

            if not formatted_messages:
                logger.info(f"AngelEye: 未能获取到群 {group_id} 的聊天记录")
                return None

            # 2. 准备两个独立的上下文
            latest_dialogue = formatted_dialogue
            historical_chat = "\n".join(formatted_messages)

            # 3. 调用 Summarizer 进行总结
            if self.config.get("chat_summarizer_enabled", True):
                logger.info("AngelEye: 调用Summarizer总结聊天记录...")
                final_content = await self.summarizer.summarize(
                    source="qq_chat_history",
                    full_content=historical_chat,
                    entity_name=f"最近{hours}小时的聊天记录",
                    dialogue=latest_dialogue
                )
                if not final_content:
                    logger.warning("AngelEye: 聊天记录分析失败，返回原始记录摘要。")
                    final_content = f"聊天记录摘要：\n" + historical_chat[:500] + "..."
            else:
                logger.info("AngelEye: 摘要功能已禁用，直接返回原文。")
                final_content = historical_chat

            # 4. 构建并返回 KnowledgeChunk
            return KnowledgeChunk(
                source="qq_chat_history",
                entity=f"群聊记录({hours}小时)",
                content=final_content
            )

        except Exception as e:
            logger.error(f"AngelEye: 处理QQ聊天记录时出错: {e}", exc_info=True)
            return None