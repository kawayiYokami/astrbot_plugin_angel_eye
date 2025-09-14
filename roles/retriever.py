"""
Angel Eye 插件 - 核心协调者 (Retriever)
负责协调整个上下文增强流程。
"""
from typing import List, Dict, Optional
from core.log import get_logger

logger = get_logger(__name__) # 获取 logger 实例

# 导入新角色和客户端
from roles.classifier import Classifier, ClassifiedEntity
from roles.filter import Filter
from roles.summarizer import Summarizer
from clients.moegirl_client import MoegirlClient
from clients.general_client import GeneralClient
from core.wikitext_cleaner import clean as clean_wikitext # 导入清理函数


class Retriever:
    """
    核心协调者，负责整个上下文增强流程的调度。
    """
    def __init__(self, analyzer_provider: 'Provider', config: Dict): # 使用字符串注解
        self.config = config
        # 初始化子角色
        self.classifier = Classifier(analyzer_provider)
        self.filter = Filter(analyzer_provider) # 初始化 Filter
        self.summarizer = Summarizer(analyzer_provider)

        # 初始化客户端
        self.moegirl_client = MoegirlClient()
        self.general_client = GeneralClient()

    async def process_context(self, contexts: List[Dict], current_prompt: str) -> Optional[str]:
        """
        执行完整的上下文增强流程。

        Args:
            contexts (List[Dict]): 对话历史记录。
            current_prompt (str): 用户当前的输入。

        Returns:
            Optional[str]: 整合后的背景知识字符串，如果没有则返回 None。
        """
        logger.info("AngelEye[Retriever]: 开始上下文增强流程...")

        # 1. 实体识别与分类
        classified_entities: List[ClassifiedEntity] = await self.classifier.classify(contexts, current_prompt)

        if not classified_entities:
            logger.info("AngelEye[Retriever]: 未识别到需要处理的实体。")
            return None

        all_background_knowledge = ""

        # 2. 遍历每个识别出的实体
        for entity in classified_entities:
            entity_name = entity.name
            domain = entity.domain
            logger.info(f"AngelEye[Retriever]: 处理实体 '{entity_name}' (领域: {domain})")

            # 3. 根据领域和配置选择客户端并执行搜索
            search_results = []
            client_name = None

            if domain == "acg" and self.config.get("domain_acg_enabled", True):
                client_name = self.config.get("domain_acg_client", "moegirl")
                if client_name == "moegirl":
                    search_results = await self.moegirl_client.search(entity_name)
            elif domain == "general" and self.config.get("domain_general_enabled", True):
                client_name = self.config.get("domain_general_client", "general")
                if client_name == "general":
                    search_results = await self.general_client.search(entity_name)

            if not search_results:
                logger.info(f"AngelEye[Retriever]: 实体 '{entity_name}' 在客户端 '{client_name}' 中未找到相关条目。")
                continue

            # 4. 调用筛选器选择最佳词条
            selected_title = await self.filter.select_best_entry(contexts, current_prompt, entity_name, search_results)
            if not selected_title:
                logger.info(f"AngelEye[Retriever]: 未能为实体 '{entity_name}' 筛选出合适的词条。")
                continue

            logger.info(f"AngelEye[Retriever]: 为实体 '{entity_name}' 选择条目 '{selected_title}' 进行摘要。")

            # 5. 获取选中词条的全文
            full_content = None
            if client_name == "moegirl":
                # 从搜索结果中找到选中词条对应的 pageid
                selected_entry = next((item for item in search_results if item['title'] == selected_title), None)
                if selected_entry and 'pageid' in selected_entry:
                    full_content = await self.moegirl_client.get_page_content(selected_title, pageid=selected_entry['pageid'])
                else:
                    logger.warning(f"AngelEye[Retriever]: 在搜索结果中找不到 '{selected_title}' 的 pageid。")
            elif client_name == "general":
                full_content = await self.general_client.get_page_content(selected_title)

            if not full_content:
                logger.warning(f"AngelEye[Retriever]: 无法获取实体 '{entity_name}' 的页面内容。")
                continue

            # 清理 wikitext
            cleaned_content = clean_wikitext(full_content)

            # 6. 调用摘要员生成背景知识
            summary = await self.summarizer.summarize(cleaned_content, entity_name)
            if not summary:
                logger.warning(f"AngelEye[Retriever]: 无法为实体 '{entity_name}' 生成摘要。")
                continue

            # 7. 整合到最终的背景知识字符串中
            all_background_knowledge += f"\n--- {entity_name} ---\n{summary}\n"

        if all_background_knowledge:
            logger.info("AngelEye[Retriever]: 上下文增强流程完成。")
            return all_background_knowledge.strip()
        else:
            logger.info("AngelEye[Retriever]: 上下文增强流程完成，但未生成有效背景知识。")
            return None