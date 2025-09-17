"""
Angel Eye 插件 - JSON 解析工具
提供健壮的 JSON 提取功能，用于从模型返回的文本中安全地解析 JSON 数据
"""
import json
import logging
from typing import Dict, Optional, Any, List

# 模块级 logger，方便独立测试和配置
logger = logging.getLogger(__name__)


def _strip_code_fences(text: str) -> str:
    """
    去除常见的 Markdown 代码块围栏，避免干扰解析。
    例如: ```json ... ``` 或 ``` ... ```
    """
    if not text:
        return text
    # 仅移除围栏标记，不移除内部内容
    return (
        text.replace("```json", "")
            .replace("```JSON", "")
            .replace("```", "")
            .strip()
    )


def _find_json_candidates(text: str) -> List[str]:
    """
    在文本中扫描并返回所有“平衡的大括号”子串，作为潜在的 JSON 候选。
    - 跳过字符串字面量中的花括号
    - 支持嵌套
    返回顺序为出现顺序（从左到右）
    """
    candidates: List[str] = []
    if not text:
        return candidates

    in_string = False
    escape = False
    depth = 0
    start_idx: Optional[int] = None

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            # 字符串中不处理花括号
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    candidates.append(text[start_idx:i + 1])
                    start_idx = None

    return candidates


def safe_extract_json(
    text: str,
    separator: str = "---JSON---",
    required_fields: Optional[List[str]] = None,
    optional_fields: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """
    从可能包含其他文本的字符串中，智能地提取最符合条件的单个JSON对象。

    提取策略:
    1.  **分割**: 如果存在分隔符，则优先处理分隔符之后的内容。
    2.  **清理**: 自动去除常见的Markdown代码块围栏。
    3.  **扫描**: 通过“平衡大括号”算法，找出所有结构上闭合的JSON候选片段。
    4.  **筛选**: (如果提供了`required_fields`) 只保留那些包含所有必须字段的JSON对象。
    5.  **评分**: (如果提供了`optional_fields`) 根据包含的可选字段数量为每个合格的JSON对象打分。
    6.  **决策**: 返回分数最高的对象。如果分数相同，则选择在原文中位置最靠后的那一个。
    7.  **回退**: 如果上述策略找不到，则尝试一次“从第一个'{'到最后一个'}'”的大包围策略。

    :param text: 包含JSON的模型原始输出字符串。
    :param separator: 用于分割内容的分隔符。
    :param required_fields: 一个列表，JSON对象必须包含其中所有的字段才算合格。
    :param optional_fields: 一个列表，用于对合格的JSON对象进行评分，包含的可选字段越多，分数越高。
    :return: 最符合条件的JSON对象（字典），如果找不到则返回None。
    """
    if not isinstance(text, str):
        logger.warning(f"AngelEye[JSONParser]: 输入不是字符串，而是 {type(text)} 类型，无法解析")
        return None
    
    if not text.strip():
        logger.debug("AngelEye[JSONParser]: 输入为空字符串")
        return None

    # 1) 分隔符处理
    json_part = text
    if separator in text:
        logger.debug(f"AngelEye[JSONParser]: 找到分隔符 '{separator}' 进行分割")
        parts = text.split(separator, 1)
        if len(parts) > 1:
            json_part = parts[1].strip()
        else:
            logger.warning("AngelEye[JSONParser]: 分隔符后无内容")
            return None
    else:
        logger.debug(f"AngelEye[JSONParser]: 未找到分隔符 '{separator}'，将处理整个文本")

    # 2) 去掉代码围栏
    json_part = _strip_code_fences(json_part)

    # 3) 扫描所有平衡的大括号候选
    candidates = _find_json_candidates(json_part)
    logger.debug(f"AngelEye[JSONParser]: 扫描到可能的 JSON 候选数量: {len(candidates)}")

    # 4) 筛选与评分
    qualified_jsons = []
    for candidate_str in candidates:
        try:
            parsed_json = json.loads(candidate_str)
            if not isinstance(parsed_json, dict):
                continue  # 只处理对象类型的JSON

            # 硬性条件：检查必须字段
            if required_fields:
                if not all(field in parsed_json for field in required_fields):
                    logger.debug(f"候选JSON缺少必须字段，跳过: {candidate_str[:100]}...")
                    continue

            # 计算分数
            score = 0
            if optional_fields:
                score = sum(1 for field in optional_fields if field in parsed_json)
            
            qualified_jsons.append({"json": parsed_json, "score": score})
            logger.debug(f"一个候选JSON合格，得分: {score}")

        except json.JSONDecodeError:
            continue # 解析失败，不是有效的JSON，跳过

    if not qualified_jsons:
        logger.warning("AngelEye[JSONParser]: 所有候选均不满足要求（或解析失败）。")
        return None

    # 5) 决策：选择分数最高的，同分则取最后的
    # 先按分数排序（稳定排序），然后取最后一个，这样就能保证在分数相同时，选择原文中位置更靠后的
    qualified_jsons.sort(key=lambda x: x['score'])
    best_json_item = qualified_jsons[-1]
    
    logger.info(f"AngelEye[JSONParser]: 提取成功，选择了得分最高的JSON（得分: {best_json_item['score']}）。")
    return best_json_item['json']


def extract_json_field(text: str, field_name: str, separator: str = "---JSON---") -> Optional[Any]:
    """
    从文本中提取JSON对象的指定字段值。
    这是 safe_extract_json 的便利包装函数。

    :param text: 包含JSON的模型原始输出字符串
    :param field_name: 要提取的字段名
    :param separator: 分隔符，默认为 '---JSON---'
    :return: 指定字段的值，如果不存在或解析失败则返回 None
    """
    json_data = safe_extract_json(text, separator)
    if json_data is None:
        return None
    
    field_value = json_data.get(field_name)
    if field_value is not None:
        logger.debug(f"AngelEye[JSONParser]: 成功提取字段 '{field_name}': {field_value}")
    else:
        logger.debug(f"AngelEye[JSONParser]: 字段 '{field_name}' 不存在于JSON中")
    
    return field_value


def validate_required_fields(json_data: Dict[str, Any], required_fields: list) -> bool:
    """
    验证JSON数据是否包含所有必需的字段。

    :param json_data: 已解析的JSON数据字典
    :param required_fields: 必需字段名称列表
    :return: 如果所有必需字段都存在则返回 True，否则返回 False
    """
    if not isinstance(json_data, dict):
        logger.warning("AngelEye[JSONParser]: 输入不是字典类型")
        return False
    
    missing_fields = [field for field in required_fields if field not in json_data]
    
    if missing_fields:
        logger.warning(f"AngelEye[JSONParser]: 缺少必需字段: {missing_fields}")
        return False
    
    logger.debug(f"AngelEye[JSONParser]: 所有必需字段 {required_fields} 都存在")
    return True