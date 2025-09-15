"""
知识请求数据模型
定义前端分类器生成的轻量级指令格式
"""
from typing import Dict, List
from pydantic import BaseModel, Field


class KnowledgeRequest(BaseModel):
    """
    轻量级知识获取请求模型
    由 Classifier 角色通过 LLM 生成，作为整个知识获取流程的输入
    """
    required_docs: Dict[str, str] = Field(
        default_factory=dict,
        description="需要查询的文档，键是实体名称，值是数据源(wikipedia/moegirl)"
    )
    required_facts: List[str] = Field(
        default_factory=list,
        description="需要查询的结构化事实，格式为'实体名.属性名'"
    )