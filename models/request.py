"""
知识请求数据模型
定义前端分类器生成的轻量级指令格式
"""
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field


class KnowledgeRequest(BaseModel):
    """
    轻量级知识获取请求模型
    由 Classifier 角色通过 LLM 生成，或由天使之心提供
    """
    required_docs: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="需要查询的文档，键是实体名称，值是包含keywords的对象"
    )
    required_facts: List[str] = Field(
        default_factory=list,
        description="需要查询的结构化事实，格式为'实体名.属性名'"
    )
    fact_query_plan: Dict[str, Any] = Field(
        default_factory=dict,
        description="V5格式的结构化事实查询计划，包含targets和filter_keywords_en"
    )
    chat_history: Dict[str, Any] = Field(
        default_factory=dict,
        description="聊天记录查询参数，包含time_range_hours、filter_user_ids、keywords等"
    )
    # 保留旧字段以兼容天使之眼自己的分类器
    parameters: Optional[Dict[str, Any]] = Field(
        default=None,
        description="兼容性字段，来自Classifier的额外参数"
    )