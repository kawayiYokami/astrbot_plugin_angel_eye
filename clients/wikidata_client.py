"""
Wikidata API客户端
用于执行结构化事实查询，包括实体链接、属性链接和递归解析
"""
import httpx
from typing import List, Dict, Optional, Set, Any
from astrbot.api import logger



class WikidataClient:
    """Wikidata客户端，提供结构化事实查询能力"""

    def __init__(self):
        self.API_ENDPOINT = "https://www.wikidata.org/w/api.php"
        self.HEADERS = {
            "User-Agent": "AstrBot-AngelEyePlugin/1.0 (https://github.com/kawayiYokami/astrbot)"
        }
        # 缓存已解析的实体标签，避免重复查询
        self.label_cache: Dict[str, str] = {}

    async def search_entity(self, keyword: str) -> Optional[Dict]:
        """
        根据关键词搜索Wikidata实体

        :param keyword: 搜索的关键词
        :return: 匹配的实体信息，如果未找到则返回None
        """
        params = {
            "action": "wbsearchentities",
            "format": "json",
            "search": keyword,
            "language": "zh",
            "limit": 5
        }

        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("search"):
                # 简单启发式：优先选择描述包含相关关键词的结果
                for item in data["search"]:
                    desc = item.get("description", "").lower()
                    # 扩展匹配规则，涵盖更多类型
                    if any(keyword in desc for keyword in ["person", "event", "dynasty", "film",
                                                           "city", "company", "mammal", "work",
                                                           "organization", "place", "species"]):
                        return item
                # 如果没有更好的匹配，返回第一个结果
                return data["search"][0]
            return None

        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[WikidataClient]: 搜索实体 '{keyword}' 时API请求失败: {e}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[WikidataClient]: 搜索实体 '{keyword}' 时发生未知错误: {e}", exc_info=True)
            return None

    async def search_property(self, keyword: str) -> Optional[Dict]:
        """
        根据关键词搜索Wikidata属性

        :param keyword: 搜索的属性名称
        :return: 匹配的属性信息，如果未找到则返回None
        """
        params = {
            "action": "wbsearchentities",
            "format": "json",
            "search": keyword,
            "language": "zh",
            "type": "property",  # 关键参数：指定搜索类型为属性
            "limit": 3
        }

        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("search"):
                return data["search"][0]  # 通常返回最匹配的一个属性
            return None

        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[WikidataClient]: 搜索属性 '{keyword}' 时API请求失败: {e}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[WikidataClient]: 搜索属性 '{keyword}' 时发生未知错误: {e}", exc_info=True)
            return None

    async def get_entity_details(self, qids: List[str]) -> Optional[Dict]:
        """
        根据QID列表批量获取实体的详细数据

        :param qids: QID列表
        :return: 实体详细数据，如果请求失败则返回None
        """
        if not qids:
            return None

        # 去重
        unique_qids = list(set(qids))

        params = {
            "action": "wbgetentities",
            "format": "json",
            "ids": "|".join(unique_qids),
            "props": "claims|labels|descriptions",
            "languages": "zh|en"
        }

        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            logger.error(f"AngelEye[WikidataClient]: 获取实体详情 '{'|'.join(unique_qids)}' 时API请求失败: {e}")
            return None
        except Exception as e:
            logger.error(f"AngelEye[WikidataClient]: 获取实体详情时发生未知错误: {e}", exc_info=True)
            return None

    def parse_claim_value(self, claim: Dict) -> Dict:
        """
        解析单个声明(claim)的值，返回一个结构化的字典

        :param claim: Wikidata的claim对象
        :return: 解析后的值字典
        """
        try:
            mainsnak = claim.get("mainsnak", {})
            if mainsnak.get("snaktype") == "value":
                datavalue = mainsnak["datavalue"]

                if datavalue["type"] == "time":
                    time_str = datavalue.get("value", {}).get("time", "")
                    # 简单格式化时间
                    formatted_time = time_str.strip('+').split('T')[0]
                    return {"type": "time", "value": formatted_time}

                elif datavalue["type"] == "wikibase-entityid":
                    entity_id = datavalue.get("value", {}).get("id", "")
                    # 返回一个指向实体的指针
                    return {"type": "entity", "qid": entity_id}

                elif datavalue["type"] == "quantity":
                    amount = datavalue.get("value", {}).get("amount", "")
                    unit = datavalue.get("value", {}).get("unit", "")
                    return {"type": "quantity", "amount": amount, "unit": unit}

                elif datavalue["type"] == "globecoordinate":
                    lat = datavalue.get("value", {}).get("latitude", "")
                    lon = datavalue.get("value", {}).get("longitude", "")
                    return {"type": "coordinate", "latitude": lat, "longitude": lon}

                else:
                    # 对于其他类型，返回其字符串表示
                    return {"type": "string", "value": str(datavalue.get("value", ""))}

        except Exception as e:
            logger.error(f"AngelEye[WikidataClient]: 解析声明时出错: {e}")
            return {"type": "error", "value": "解析失败"}

        return {"type": "unknown", "value": "未知值"}

    async def resolve_entities_recursively(self, qids_to_resolve: Set[str],
                                          resolved_cache: Dict[str, str] = None,
                                          visited: Set[str] = None,
                                          depth: int = 0,
                                          max_depth: int = 2) -> Dict[str, str]:
        """
        递归地解析一组QID，将它们转换为人类可读的标签，并缓存结果

        :param qids_to_resolve: 需要解析的QID集合
        :param resolved_cache: 已解析的QID -> 标签映射缓存
        :param visited: 已访问过的QID集合，用于防止无限循环
        :param depth: 当前递归深度
        :param max_depth: 最大递归深度
        :return: 更新后的resolved_cache
        """
        if resolved_cache is None:
            resolved_cache = self.label_cache
        if visited is None:
            visited = set()

        # 深度限制，避免递归过深
        if depth >= max_depth:
            logger.debug(f"AngelEye[WikidataClient]: 达到最大递归深度 {max_depth}")
            return resolved_cache

        if not qids_to_resolve:
            return resolved_cache

        # 从缓存中过滤掉已经解析过的QID
        qids_to_fetch = [qid for qid in qids_to_resolve
                         if qid not in resolved_cache and qid not in visited]

        if not qids_to_fetch:
            return resolved_cache

        # 将要获取的QID加入已访问集合
        visited.update(qids_to_fetch)

        # 批量获取数据
        logger.debug(f"AngelEye[WikidataClient]: 正在解析 {len(qids_to_fetch)} 个实体")
        entity_details = await self.get_entity_details(qids_to_fetch)

        if not entity_details or "entities" not in entity_details:
            logger.warning("AngelEye[WikidataClient]: 获取实体数据失败")
            return resolved_cache

        # 解析并缓存新获取的实体
        new_qids_to_resolve = set()
        for qid, entity_data in entity_details["entities"].items():
            # 获取标签，优先中文
            label = (entity_data.get("labels", {}).get("zh", {}).get("value") or
                    entity_data.get("labels", {}).get("en", {}).get("value") or
                    qid)
            resolved_cache[qid] = label

            # 如果深度还允许，查找该实体的所有声明，收集新的QID指针
            if depth + 1 < max_depth:
                claims = entity_data.get("claims", {})
                for prop_claims in claims.values():
                    for claim in prop_claims:
                        mainsnak = claim.get("mainsnak", {})
                        if mainsnak.get("snaktype") == "value":
                            datavalue = mainsnak.get("datavalue", {})
                            if datavalue.get("type") == "wikibase-entityid":
                                new_qid = datavalue.get("value", {}).get("id")
                                if new_qid and new_qid not in visited and new_qid not in resolved_cache:
                                    new_qids_to_resolve.add(new_qid)

        # 递归调用，解析新发现的QID
        if new_qids_to_resolve and depth + 1 < max_depth:
            await self.resolve_entities_recursively(
                new_qids_to_resolve, resolved_cache, visited, depth + 1, max_depth
            )

        return resolved_cache

    async def query_facts(self, entity_name: str, fact_names: List[str]) -> Dict[str, Any]:
        """
        查询实体的特定事实

        :param entity_name: 实体名称
        :param fact_names: 事实名称列表（例如：["出生日期", "父亲"]）
        :return: 事实字典，键为事实名称，值为查询结果
        """
        results = {}

        # 1. 实体链接
        entity = await self.search_entity(entity_name)
        if not entity:
            logger.warning(f"AngelEye[WikidataClient]: 未找到实体 '{entity_name}'")
            return results

        entity_qid = entity["id"]
        entity_label = entity["label"]
        logger.debug(f"AngelEye[WikidataClient]: 实体链接成功 '{entity_name}' -> {entity_label} ({entity_qid})")

        # 2. 属性链接
        fact_name_to_pid = {}
        for fact_name in fact_names:
            prop = await self.search_property(fact_name)
            if prop:
                fact_name_to_pid[fact_name] = prop["id"]
                logger.debug(f"AngelEye[WikidataClient]: 属性链接成功 '{fact_name}' -> {prop['label']} ({prop['id']})")
            else:
                logger.warning(f"AngelEye[WikidataClient]: 未找到属性 '{fact_name}'")

        if not fact_name_to_pid:
            return results

        # 3. 获取实体数据
        entity_details = await self.get_entity_details([entity_qid])
        if not entity_details or "entities" not in entity_details:
            return results

        entity_data = entity_details["entities"].get(entity_qid)
        if not entity_data:
            return results

        claims = entity_data.get("claims", {})

        # 4. 收集需要解析的QID
        qids_to_resolve = set()
        for fact_name, pid in fact_name_to_pid.items():
            claim_list = claims.get(pid)
            if claim_list:
                for claim in claim_list:
                    parsed_value = self.parse_claim_value(claim)
                    if parsed_value["type"] == "entity":
                        qids_to_resolve.add(parsed_value["qid"])

        # 5. 递归解析所有QID
        if qids_to_resolve:
            await self.resolve_entities_recursively(qids_to_resolve)

        # 6. 提取并格式化结果
        for fact_name, pid in fact_name_to_pid.items():
            claim_list = claims.get(pid)
            if not claim_list:
                results[fact_name] = None
                continue

            # 取第一个声明的值
            first_claim = claim_list[0]
            parsed_value = self.parse_claim_value(first_claim)

            # 根据值的类型进行最终格式化
            if parsed_value["type"] == "entity":
                qid_to_lookup = parsed_value["qid"]
                final_label = self.label_cache.get(qid_to_lookup, f"未知实体 ({qid_to_lookup})")
                results[fact_name] = final_label
            elif parsed_value["type"] == "time":
                results[fact_name] = parsed_value["value"]
            elif parsed_value["type"] == "quantity":
                results[fact_name] = f"{parsed_value['amount']} (单位: {parsed_value['unit']})"
            elif parsed_value["type"] == "coordinate":
                results[fact_name] = f"纬度 {parsed_value['latitude']}, 经度 {parsed_value['longitude']}"
            else:
                results[fact_name] = parsed_value.get("value", "N/A")

        return results