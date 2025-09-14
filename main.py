"""
Angel Eye 插件主入口
"""
import astrbot.api.star as star
import astrbot.api.event.filter as filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core import logger

from .roles.retriever import Retriever
from .roles.filter import Filter
from .roles.summarizer import Summarizer
from .clients.moegirl_client import MoegirlClient
from .clients.general_client import GeneralClient

class AngelEyePlugin(star.Star):
    """
    Angel Eye 插件，通过在LLM调用前注入百科知识来增强上下文。
    """
    def __init__(self, context: star.Context) -> None:
        self.context = context
        # 假设在 astrbot.yml 中配置了名为 'small_model' 的 Provider
        try:
            small_model_provider = self.context.get_provider_by_id('small_model')
        except Exception:
            logger.error("AngelEye: 未能在配置中找到 'small_model' Provider，插件可能无法正常工作。")
            small_model_provider = None

        # 初始化所有角色和客户端
        self.retriever = Retriever(small_model_provider)
        self.filter = Filter(small_model_provider)
        self.summarizer = Summarizer(small_model_provider)
        self.moegirl_client = MoegirlClient()
        self.general_client = GeneralClient()

    @filter.on_llm_request(priority=100)
    async def run_knowledge_injection_pipeline(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在主模型请求前，执行知识注入流水线。
        """
        logger.info("AngelEye: 知识注入流水线启动...")
        original_prompt = req.prompt

        # 1. 检索员角色：判断是否需要搜索
        retriever_result = await self.retriever.analyze(req.contexts, original_prompt)
        if not retriever_result or not retriever_result.should_search:
            logger.info("AngelEye: 检索员决策不搜索，流水线结束。")
            return

        # 2. 搜索调度
        logger.info(f"AngelEye: 检索员决策搜索。领域: {retriever_result.domain}, 查询: {retriever_result.search_query}")
        search_results = []
        if retriever_result.domain == "二次元":
            search_results = await self.moegirl_client.search(retriever_result.search_query)
        else:  # 通用
            search_results = await self.general_client.search(retriever_result.search_query)

        if not search_results:
            logger.info("AngelEye: 百科搜索无结果，流水线结束。")
            return

        # 3. 二次筛选角色：从结果中选择最相关的词条
        selected_title = await self.filter.select_best_entry(search_results, original_prompt)
        if not selected_title:
            logger.info("AngelEye: 二次筛选无结果，流水线结束。")
            return

        # 4. 获取全文并交由整理员处理
        logger.info(f"AngelEye: 筛选出的最佳词条: {selected_title}")
        full_content = ""
        if retriever_result.domain == "二次元":
            full_content = await self.moegirl_client.get_page_content(selected_title)
        else:
            full_content = await self.general_client.get_page_content(selected_title)

        if not full_content:
            logger.info("AngelEye: 获取页面全文失败，流水线结束。")
            return

        summary_text = await self.summarizer.summarize(full_content, original_prompt)
        if not summary_text:
            logger.info("AngelEye: 整理员未能生成摘要，流水线结束。")
            return

        # 5. 安全上下文注入
        injection_text = f"\n\n[补充知识]:\n---\n{summary_text}\n---\n"
        req.system_prompt += injection_text
        logger.info("AngelEye: 成功注入补充知识！")

        # (可选) 修改 prompt，引导主模型使用补充知识
        req.prompt = f"请参考系统提示词中提供的'补充知识'，来回答我最初的问题：'{original_prompt}'"