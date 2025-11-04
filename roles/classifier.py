"""
Angel Eye 插件 - 分类器角色 (Classifier)
负责分析对话，生成轻量级知识请求指令
"""
import json
from typing import List, Dict, Optional, Any
from pathlib import Path
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrbot.api.provider import Provider

from ..models.request import KnowledgeRequest
from ..core.exceptions import ParsingError, AngelEyeError
from ..core.json_parser import safe_extract_json
from ..core.context.small_model_prompt_builder import SmallModelPromptBuilder

try:
    import tiktoken
except ImportError:
    tiktoken = None

logger = logging.getLogger(__name__)



class Classifier:
    """
    分类器角色，负责分析对话上下文，生成知识获取请求
    使用"思维链+JSON"模式，提供可解释性
    """
    def __init__(self, provider: 'Provider', config: Dict[str, Any]):
        """
        初始化分类器

        :param provider: 用于调用LLM的Provider
        :param config: 插件配置字典
        """
        self.provider = provider
        self.config = config

        # 设置最大上下文长度（默认 10 * 1024 = 10240 tokens）
        max_tokens_k = self.config.get("max_classifier_tokens_k", 10)
        self.max_tokens = max_tokens_k * 1024
        # 初始化tiktoken编码器
        try:
            if tiktoken:
                self.encoding = tiktoken.get_encoding("cl100k_base")
            else:
                self.encoding = None
                logger.warning("AngelEye[Classifier]: tiktoken 未安装，不进行内容截断。")
        except Exception:
            logger.warning("AngelEye[Classifier]: tiktoken 初始化失败，不进行内容截断。")
            self.encoding = None

        # 根据模型是否为“思考模型”来决定使用哪个提示词
        is_thought_model = self.config.get("is_classifier_thought_model", False)

        if is_thought_model:
            # 对于思考模型，使用直接输出JSON的提示词
            prompt_filename = "classifier_direct_prompt.md"
        else:
            # 对于非思考模型，使用引导其思考的提示词
            prompt_filename = "classifier_prompt.md"

        prompt_path = Path(__file__).parent.parent / "prompts" / prompt_filename

        try:
            self.prompt_template = prompt_path.read_text(encoding="utf-8")
            logger.debug(f"AngelEye[Classifier]: 成功加载Prompt模板: {prompt_filename}")
        except FileNotFoundError:
            logger.error(f"AngelEye[Classifier]: 找不到Prompt文件 {prompt_path}")
            # 提供一个非常基础的备用模板
            self.prompt_template = "分析以下对话并以JSON格式返回你的分析结果: {dialogue}"

    async def get_knowledge_request(self, contexts: List[Dict], current_prompt: str) -> Optional[KnowledgeRequest]:
        """
        调用LLM分析对话，生成知识请求

        :param contexts: 对话历史记录
        :param current_prompt: 当前用户输入
        :return: KnowledgeRequest对象，如果分析失败则返回None
        """
        if not self.provider:
            logger.error("AngelEye[Classifier]: 分析模型Provider未初始化")
            return None

        # 将 astrbot 上下文转换为统一格式
        dialogue_parts = []
        for item in contexts:
            role = item.get("role", "unknown")
            content = item.get("content", "")

            if role == "user":
                # 直接在 content 前面加上角色前缀
                dialogue_parts.append(f"[用户]{content}")
            elif role == "assistant":
                dialogue_parts.append(f"[助理]{content}")
            else:
                # 对于未知角色，可以选择跳过或标记
                dialogue_parts.append(f"[未知角色]{content}")

        # 处理当前消息 (current_prompt)
        # 注意：current_prompt 是纯净的，不包含元数据
        dialogue_parts.append(f"[用户]{current_prompt}")

        formatted_dialogue = "\n".join(dialogue_parts)

        # 截断对话内容以符合10K tokens限制
        if self.encoding:
            tokens = self.encoding.encode(formatted_dialogue)
            if len(tokens) > self.max_tokens:
                # 从末尾截取，保留最新的对话内容
                truncated_tokens = tokens[-self.max_tokens:]
                formatted_dialogue = self.encoding.decode(truncated_tokens)
                logger.debug(f"AngelEye[Classifier]: 对话内容已截断至 {self.max_tokens} tokens")

        # 使用统一的注入方法
        final_prompt = SmallModelPromptBuilder.inject_dialogue_into_template(
            self.prompt_template,
            formatted_dialogue
        )

        try:
            # 调用LLM
            logger.debug("AngelEye[Classifier]: 正在调用LLM分析对话...")
            # 可选：记录发送的核心上下文而非完整提示词
            # logger.debug(f"AngelEye[Classifier]: 发送的上下文: {formatted_dialogue}")
            response = await self.provider.text_chat(prompt=final_prompt)
            response_text = response.completion_text
            # 记录AI的原始输出
            logger.debug(f"AngelEye[Classifier]: LLM原始响应:\n{response_text}")

            # 使用新的、健壮的JSON解析器
            response_json = safe_extract_json(response_text)

            if response_json is None:
                logger.warning("AngelEye[Classifier]: 未能从模型响应中提取到有效的JSON。")
                return None

            # 转换为KnowledgeRequest对象
            request = KnowledgeRequest(
                required_docs=response_json.get("required_docs", {}),
                required_facts=response_json.get("required_facts", []),
                parameters=response_json.get("parameters", {})  # 新增 parameters 字段
            )

            # 记录生成的请求
            logger.info(f"AngelEye[Classifier]: 生成知识请求 - "
                       f"文档: {len(request.required_docs)}, "
                       f"事实: {len(request.required_facts)}, "
                       f"参数: {len(request.parameters)}")

            # 如果请求为空，返回None
            if not request.required_docs and not request.required_facts:
                logger.info("AngelEye[Classifier]: 无需查询任何知识")
                return None

            return request

        except json.JSONDecodeError as e:
            logger.error(f"AngelEye[Classifier]: 解析JSON失败: {e}")
            logger.debug(f"原始JSON文本: {response_text}")
            raise ParsingError("Failed to parse JSON from Classifier LLM response") from e
        except AngelEyeError:
            # 重新抛出自定义异常
            raise
        except Exception as e:
            logger.error(f"AngelEye[Classifier]: 调用LLM时发生错误: {e}", exc_info=True)
            raise AngelEyeError("Classifier LLM call failed") from e

    async def classify(self, contexts: List[Dict], current_prompt: str) -> Optional[KnowledgeRequest]:
        """
        向后兼容的接口，调用新的get_knowledge_request方法

        :param contexts: 对话历史记录
        :param current_prompt: 当前用户输入
        :return: KnowledgeRequest对象
        """
        return await self.get_knowledge_request(contexts, current_prompt)