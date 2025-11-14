"""
Wikidata API客户端
用于执行结构化事实查询，包括实体链接、属性链接和递归解析
"""
import httpx
import asyncio
from typing import List, Dict, Optional, Set, Any, Tuple
import logging
from ..core.cache_manager import async_cache
logger = logging.getLogger(__name__)



class WikidataClient:
    """Wikidata客户端，提供结构化事实查询能力"""

    def __init__(self):
        self.API_ENDPOINT = "https://www.wikidata.org/w/api.php"
        self.HEADERS = {
            "User-Agent": "AstrBot-AngelEyePlugin/1.0 (https://github.com/kawayiYokami/astrbot)"
        }
        # 缓存已解析的实体标签，避免重复查询
        self.label_cache: Dict[str, str] = {}

    def _parse_targets(self, targets_str: str) -> Tuple[Set[str], Set[str]]:
        """
        (内部方法) 解析查询计划中的 'targets' 字符串。

        :param targets_str: 查询计划格式的 targets 字符串 (e.g., "实体.属性 | entity.property")。
        :return: 一个元组，包含两个集合：(实体名称集合, 属性名称集合)
        """
        entity_names = set()
        property_names = set()

        if not targets_str or not isinstance(targets_str, str):
            logger.warning(f"AngelEye[WikidataClient]: 无效的 targets 字符串输入: {targets_str}")
            return entity_names, property_names

        # 1. 按 '|' 分隔不同的查询目标
        target_pairs = [pair.strip() for pair in targets_str.split('|') if pair.strip()]

        for pair in target_pairs:
            # 2. 按 '.' 分隔实体和属性
            # 使用 rsplit('.', 1) 从右边分割一次，以应对属性名中可能包含 '.' 的情况（虽然不常见）
            parts = pair.rsplit('.', 1)
            if len(parts) != 2:
                logger.warning(f"AngelEye[WikidataClient]: 无法解析的查询目标对: '{pair}'，跳过。")
                continue

            entity_part = parts[0].strip()
            property_part = parts[1].strip()

            if entity_part and property_part:
                entity_names.add(entity_part)
                property_names.add(property_part)
            else:
                logger.warning(f"AngelEye[WikidataClient]: 查询目标对 '{pair}' 包含空的实体或属性名，跳过。")

        logger.debug(f"AngelEye[WikidataClient]: 解析 targets 完成。实体: {entity_names}, 属性: {property_names}")
        return entity_names, property_names

    def _parse_filters(self, filter_str: str) -> List[str]:
        """
        (内部方法) 解析查询计划中的 'filter_keywords_en' 字符串。

        :param filter_str: 查询计划格式的 filter_keywords_en 字符串 (e.g., "keyword1|keyword2")。
        :return: 一个英文关键词列表。
        """
        if not filter_str or not isinstance(filter_str, str):
            # 空字符串是正常情况，不需要任何日志输出
            return []

        # 按 '|' 分隔，并去除每个关键词的首尾空格
        keywords = [kw.strip() for kw in filter_str.split('|') if kw.strip()]
        logger.debug(f"AngelEye[WikidataClient]: 解析过滤关键词完成: {keywords}")
        return keywords

    async def execute_query(self, query_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据 V5 格式的查询计划，并发地查询实体和属性，并返回最终的事实结果。
        这是支持新查询格式的核心方法。

        :param query_plan: 查询计划格式的JSON指令，例如：
                           {
                             "targets": "苹果.创始人 | apple.founder",
                             "filter_keywords_en": "technology|company"
                           }
        :return: 包含查询结果的字典。
        """
        results = {
            "entities": {},
            "properties": {},
            "final_facts": {}
        }

        targets_str = query_plan.get("targets", "")
        filter_keywords_str = query_plan.get("filter_keywords_en", "")

        if not targets_str:
            logger.warning("AngelEye[WikidataClient]: 查询计划缺少 'targets' 字段。")
            return results

        # 1. 解析指令
        logger.debug("AngelEye[WikidataClient]: 开始解析查询计划...")
        entity_names, property_names = self._parse_targets(targets_str)
        filter_keywords = self._parse_filters(filter_keywords_str)

        if not entity_names or not property_names:
            logger.warning("AngelEye[WikidataClient]: 解析后的实体或属性列表为空。")
            return results

        logger.info(f"AngelEye[WikidataClient]: 解析完成。将并发搜索 {len(entity_names)} 个实体和 {len(property_names)} 个属性。")

        # 2. 并发搜索实体
        # 创建任务列表
        entity_search_tasks = [
            self.search_entity(name, context_hint="|".join(filter_keywords) if filter_keywords else None)
            for name in entity_names
        ]

        logger.debug(f"AngelEye[WikidataClient]: 创建了 {len(entity_search_tasks)} 个实体搜索任务。")

        # 等待所有实体搜索任务完成
        entity_results = await asyncio.gather(*entity_search_tasks, return_exceptions=True)

        # 3. 处理实体搜索结果并进行消歧义决策 (新逻辑)
        # 3.1 合并所有候选实体
        all_candidates_flat = []
        for i, result in enumerate(entity_results):
            entity_name = list(entity_names)[i]
            if isinstance(result, Exception):
                logger.error(f"AngelEye[WikidataClient]: 搜索实体 '{entity_name}' 时发生异常: {result}", exc_info=True)
                results["entities"][entity_name] = {"error": str(result)}
            elif result:
                # 如果返回的是单个实体dict，将其放入列表
                if isinstance(result, dict):
                    all_candidates_flat.append(result)
                    key = result.get("label", result.get("id", entity_name))
                    results["entities"][key] = result
                # 如果返回的是实体列表（search_entity的新行为），则直接extend
                elif isinstance(result, list):
                    all_candidates_flat.extend(result)
                    for item in result:
                        key = item.get("label", item.get("id", entity_name))
                        results["entities"][key] = item
            else:
                results["entities"][entity_name] = None

        # 3.2 决策：统计每个实体的出现次数，选择出现次数最多的
        best_entity = None
        if all_candidates_flat:
            candidate_counts = {}
            for candidate in all_candidates_flat:
                qid = candidate.get('id')
                if not qid:
                    continue
                if qid not in candidate_counts:
                    candidate_counts[qid] = {'item': candidate, 'count': 0}
                candidate_counts[qid]['count'] += 1

            if candidate_counts:
                # 按出现次数降序排序，次数相同时保持原有顺序（Wikidata的排序）
                sorted_by_count = sorted(candidate_counts.values(), key=lambda x: x['count'], reverse=True)
                best_entity = sorted_by_count[0]['item']
                logger.info(f"AngelEye[WikidataClient]: 通过计数决策选定实体 '{best_entity.get('label')}' ({best_entity.get('id')})，出现次数: {sorted_by_count[0]['count']}。")

        # 4. 并发搜索属性
        property_search_tasks = [self.search_property(name) for name in property_names]
        logger.debug(f"AngelEye[WikidataClient]: 创建了 {len(property_search_tasks)} 个属性搜索任务。")

        # 等待所有属性搜索任务完成
        property_results = await asyncio.gather(*property_search_tasks, return_exceptions=True)

        # 5. 处理属性搜索结果并去重
        # 使用属性ID作为键来存储唯一属性，实现去重
        unique_properties = {}
        for i, result in enumerate(property_results):
            prop_name = list(property_names)[i]
            if isinstance(result, Exception):
                logger.error(f"AngelEye[WikidataClient]: 搜索属性 '{prop_name}' 时发生异常: {result}", exc_info=True)
                results["properties"][prop_name] = {"error": str(result)}
            elif result:
                prop_id = result.get("id")
                if prop_id:
                    # 如果这个属性ID还没有被记录，则存储它
                    # 这样可以确保即使 "创始人" 和 "founder" 都指向 P112，我们也只处理一次
                    if prop_id not in unique_properties:
                        key = result.get("label", prop_id)
                        results["properties"][key] = result
                        unique_properties[prop_id] = result
                        logger.debug(f"AngelEye[WikidataClient]: 找到唯一属性 '{key}' (ID: {prop_id})")
                    else:
                        logger.debug(f"AngelEye[WikidataClient]: 属性 '{prop_name}' (ID: {prop_id}) 已存在，跳过去重。")
                else:
                    logger.warning(f"AngelEye[WikidataClient]: 属性 '{prop_name}' 的结果缺少ID字段。")
            else:
                results["properties"][prop_name] = None

        # 6. 如果找到了实体和属性，则获取最终的事实
        if best_entity and unique_properties:
            entity_qid = best_entity.get("id")
            logger.info(f"AngelEye[WikidataClient]: 选定实体 '{best_entity.get('label')}' ({entity_qid}) 进行事实查询。")

            # 获取实体详情
            entity_details = await self.get_entity_details([entity_qid])
            if entity_details and "entities" in entity_details:
                entity_data = entity_details["entities"].get(entity_qid)
                if entity_data:
                    claims = entity_data.get("claims", {})

                    # 为每个唯一属性提取所有事实值
                    for prop_id, prop_info in unique_properties.items():
                        claim_list = claims.get(prop_id)
                        if claim_list:
                            # 收集该属性的所有值
                            all_values = []
                            for claim in claim_list:
                                parsed_value = self.parse_claim_value(claim)

                                # 根据值的类型进行最终格式化
                                final_value = "N/A"
                                if parsed_value["type"] == "entity":
                                    # 对于实体类型的值，尝试解析其标签
                                    qid_to_lookup = parsed_value["qid"]
                                    # 确保实体标签被解析
                                    await self.resolve_entities_recursively({qid_to_lookup})
                                    final_label = self.label_cache.get(qid_to_lookup, f"未知实体 ({qid_to_lookup})")
                                    final_value = final_label
                                elif parsed_value["type"] == "time":
                                    final_value = parsed_value["value"]
                                elif parsed_value["type"] == "quantity":
                                    final_value = f"{parsed_value['amount']} (单位: {parsed_value['unit']})"
                                elif parsed_value["type"] == "coordinate":
                                    final_value = f"纬度 {parsed_value['latitude']}, 经度 {parsed_value['longitude']}"
                                else:
                                    final_value = parsed_value.get("value", "N/A")

                                if final_value != "N/A":
                                    all_values.append(final_value)

                            # 将所有值格式化为一个字符串，用逗号分隔
                            if all_values:
                                final_value_str = ", ".join(all_values)
                                # 使用属性的首选标签（通常是英文）作为键
                                prop_display_name = prop_info.get("label", prop_id)
                                results["final_facts"][prop_display_name] = final_value_str
                                logger.debug(f"AngelEye[WikidataClient]: 获取到事实 '{prop_display_name}': {final_value_str}")
                            else:
                                prop_display_name = prop_info.get("label", prop_id)
                                logger.debug(f"AngelEye[WikidataClient]: 实体 '{best_entity.get('label')}' 的属性 '{prop_display_name}' 未解析出有效值。")
                        else:
                            prop_display_name = prop_info.get("label", prop_id)
                            logger.debug(f"AngelEye[WikidataClient]: 实体 '{best_entity.get('label')}' 没有属性 '{prop_display_name}' (ID: {prop_id}) 的声明。")
                else:
                    logger.warning(f"AngelEye[WikidataClient]: 实体详情中未找到实体 '{entity_qid}' 的数据。")
            else:
                 logger.warning(f"AngelEye[WikidataClient]: 获取实体 '{entity_qid}' 的详情失败。")
        else:
            logger.info("AngelEye[WikidataClient]: 未找到足够的实体或属性来进行事实查询。")

        logger.info("AngelEye[WikidataClient]: 查询计划执行完成。")
        return results

    @async_cache("wikidata:search_entity")
    async def search_entity(self, keyword: str, context_hint: Optional[str] = None) -> Optional[Dict]:
        """
        根据关键词搜索Wikidata实体

        :param keyword: 搜索的关键词
        :param context_hint: 用于消歧义的上下文提示词。可以是单个词，也可以是用 '|' 分隔的多个词。
        :return: 匹配的实体信息，如果未找到则返回None
        """
        params = {
            "action": "wbsearchentities",
            "format": "json",
            "search": keyword,
            "language": "zh",
            "limit": 10 # 增加候选数量以提高消歧义成功率
        }

        try:
            timeout = httpx.Timeout(10.0)
            async with httpx.AsyncClient(headers=self.HEADERS, timeout=timeout) as client:
                response = await client.get(self.API_ENDPOINT, params=params)
                response.raise_for_status()
                data = response.json()

            if data.get("search"):
                # 优先使用上下文提示词进行消歧义
                if context_hint:
                    # 1. 解析 context_hint
                    #    支持单个词 ("tennis") 或多个词 ("tennis|athlete")
                    hints = [hint.strip().lower() for hint in context_hint.split('|') if hint.strip()]
                    logger.debug(f"AngelEye[WikidataClient]: 解析后的消歧义提示词: {hints}")

                    # 2. 计分和排序
                    scored_items = []
                    for item in data["search"]:
                        desc = item.get("description", "").lower()
                        score = sum(1 for hint in hints if hint in desc)
                        scored_items.append((item, score))
                        logger.debug(f"AngelEye[WikidataClient]: 候选实体 '{item.get('label', 'N/A')}' (ID: {item.get('id', 'N/A')}) 描述: '{desc}', 匹配得分: {score}")

                    # 3. 选择得分最高的实体
                    if scored_items:
                        # 按得分降序排序，得分相同则保持原有顺序
                        scored_items.sort(key=lambda x: x[1], reverse=True)
                        best_item, best_score = scored_items[0]
                        if best_score > 0:
                            logger.debug(f"AngelEye[WikidataClient]: 通过计分消歧义成功 -> '{best_item['label']}' (ID: {best_item['id']}, 得分: {best_score})")
                            return best_item
                        else:
                            logger.debug("AngelEye[WikidataClient]: 所有候选实体的匹配得分均为0。")

                # 回退到简单启发式：优先选择描述包含相关关键词的结果
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

    @async_cache("wikidata:search_property")
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

    @async_cache("wikidata:get_entity_details")
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

    async def query_facts(self, entity_name: str, fact_names: List[str], context_hint: Optional[str] = None) -> Dict[str, Any]:
        """
        查询实体的特定事实

        :param entity_name: 实体名称
        :param fact_names: 事实名称列表（例如：["出生日期", "父亲"]）
        :param context_hint: 用于实体链接消歧义的上下文提示词
        :return: 事实字典，键为事实名称，值为查询结果
        """
        results = {}

        # 1. 实体链接
        entity = await self.search_entity(entity_name, context_hint=context_hint)
        if not entity:
            return results

        entity_qid = entity.get("id") or entity.get("title") # API返回的实体ID字段可能是 'id' 或 'title'
        entity_label = entity.get("label") or entity.get("display", {}).get("label", {}).get("value") or entity_qid
        logger.debug(f"AngelEye[WikidataClient]: 实体链接成功 '{entity_name}' -> {entity_label} ({entity_qid})")

        # 2. 属性链接
        fact_name_to_pid = {}
        for fact_name in fact_names:
            prop = await self.search_property(fact_name)
            if prop:
                fact_name_to_pid[fact_name] = prop["id"]
                logger.debug(f"AngelEye[WikidataClient]: 属性链接成功 '{fact_name}' -> {prop['label']} ({prop['id']})")
            else:
                pass

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