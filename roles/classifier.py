"""
Angel Eye 插件 - 分类器角色 (Classifier)
负责分析对话，生成轻量级知识请求指令
"""
import json
from typing import List, Dict, Optional, Any
from pathlib import Path

from astrbot.api import logger
from ..models.request import KnowledgeRequest
from ..core.exceptions import ParsingError, AngelEyeError



class Classifier:
    """
    分类器角色，负责分析对话上下文，生成知识获取请求
    使用"思维链+JSON"模式，提供可解释性
    """
    def __init__(self, provider: 'Provider'):
        """
        初始化分类器

        :param provider: 用于调用LLM的Provider
        """
        self.provider = provider
        # 在初始化时直接加载Prompt模板
        prompt_path = Path(__file__).parent.parent / "prompts" / "classifier_prompt.md"
        try:
            self.prompt_template = prompt_path.read_text(encoding="utf-8")
            logger.debug("AngelEye[Classifier]: 成功加载Prompt模板")
        except FileNotFoundError:
            logger.error(f"AngelEye[Classifier]: 找不到Prompt文件 {prompt_path}")
            self.prompt_template = "分析对话: {dialogue}"

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
        # 动态转义模板中的所有花括号，然后恢复我们需要的占位符
        safe_template = self.prompt_template.replace('{', '{{').replace('}', '}}').replace('{{dialogue}}', '{dialogue}')
        final_prompt = safe_template.format(dialogue=formatted_dialogue)

        try:
            # 调用LLM
            logger.debug("AngelEye[Classifier]: 正在调用LLM分析对话...")
            logger.debug(f"AngelEye[Classifier]: 向分析模型发送的输入:\n---\n{final_prompt}\n---")
            response = await self.provider.text_chat(prompt=final_prompt)
            response_text = response.completion_text
            logger.debug(f"AngelEye[Classifier]: 从分析模型接收的输出:\n---\n{response_text}\n---")

            # 使用分隔符切分思考过程和JSON
            separator = "---JSON---"
            if separator not in response_text:
                logger.warning("AngelEye[Classifier]: 模型输出中未找到分隔符 '---JSON---'")
                # 尝试直接解析为JSON（向后兼容）
                json_text = response_text
                thinking_process = ""
            else:
                parts = response_text.split(separator, 1)
                thinking_process = parts[0].strip()
                json_text = parts[1].strip()

                # 记录思考过程到日志（用于调试和监控）
                if thinking_process:
                    logger.debug(f"AngelEye[Classifier] 思考过程:\n{thinking_process}")

            # 提取JSON字符串
            json_str_start = json_text.find('{')
            json_str_end = json_text.rfind('}') + 1

            if json_str_start == -1 or json_str_end <= json_str_start:
                logger.warning(f"AngelEye[Classifier]: 未找到有效的JSON结构")
                return None

            json_str = json_text[json_str_start:json_str_end]

            # 解析JSON并创建KnowledgeRequest对象
            response_json = json.loads(json_str)

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
            logger.debug(f"原始JSON文本: {json_text if 'json_text' in locals() else response_text}")
            raise ParsingError("Failed to parse JSON from Classifier LLM response") from e
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