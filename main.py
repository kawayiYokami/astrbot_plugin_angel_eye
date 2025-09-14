"""
Angel Eye 插件主入口
"""
import astrbot.api.star as star
import astrbot.api.event.filter as filter
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest, Provider

from core.log import get_logger
from roles.retriever import Retriever

logger = get_logger(__name__)


class AngelEyePlugin(star.Star):
    """
    Angel Eye 插件，通过在LLM调用前注入百科知识来增强上下文。
    """
    def __init__(self, context: star.Context) -> None:
        self.context = context
        # 1. 加载配置
        self.config = self.context.get_config("astrbot_plugin_angel_eye")
        logger.info(f"AngelEye: 加载配置完成: {self.config}")

        # 2. 获取分析模型 Provider
        analyzer_provider_id = self.config.get("analyzer_provider_id", "claude-3-haiku")
        try:
            self.analyzer_provider: Provider = self.context.get_provider_by_id(analyzer_provider_id)
        except Exception as e:
            logger.error(f"AngelEye: 未能在配置中找到分析模型 Provider ID '{analyzer_provider_id}'，插件可能无法正常工作。错误: {e}")
            self.analyzer_provider = None

        # 3. 初始化所有角色和客户端
        # 注意：Classifier 将在 Retriever 内部被初始化和使用
        self.retriever = Retriever(self.analyzer_provider, self.config)
        # Filter 角色在新架构中被整合或移除，由 Classifier 和直接的标题匹配替代
        # Summarizer 角色保留
        self.summarizer = Summarizer(self.analyzer_provider)
        # Clients 保留
        self.moegirl_client = MoegirlClient()
        self.general_client = GeneralClient()

    @filter.on_llm_request(priority=100)
    async def enrich_context_before_llm_call(self, event: AstrMessageEvent, req: ProviderRequest):
        """
        在主模型请求前，执行上下文增强逻辑。
        这是 Angel Eye 的核心入口点。
        """
        if not self.analyzer_provider:
            logger.warning("AngelEye: 分析模型未配置，跳过上下文增强。")
            return

        logger.info("AngelEye: 上下文增强流程启动...")
        original_prompt = req.prompt

        # 1. 调用 Retriever，它将协调 Classifier, Clients 和 Summarizer 完成整个流程
        #    返回一个包含所有需要注入的背景知识的字符串
        background_knowledge = await self.retriever.process_context(req.contexts, original_prompt)

        if not background_knowledge:
            logger.info("AngelEye: 未发现需要补充的背景知识，流程结束。")
            return

        # 2. 安全上下文注入
        injection_text = f"\n\n[背景知识]:\n{background_knowledge}\n"
        req.system_prompt = (req.system_prompt or "") + injection_text
        logger.info("AngelEye: 成功注入背景知识！")

        # (可选) 修改 prompt，引导主模型使用补充知识
        # req.prompt = f"请参考系统提示词中提供的'背景知识'，来回答我最初的问题：'{original_prompt}'"
