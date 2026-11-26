"""Resolve only forward resources already present in an authorized message."""
from __future__ import annotations

import asyncio
import copy
import json
import time

from ..core.rich_message import format_rich_segment


class RichContentResolver:
    def __init__(self):
        self._cache = {}

    async def expand_forward(self, bot, reference, *, scope="", budget=None):
        """读一个转发资源并渲染成文本块；没展开成返回 None。

        与 enrich_chain 共用同一套抓取、缓存和读取预算，区别只在产出：
        这里给的是可以直接写进正文的文本，而不是消息链。嵌套转发按
        enrich_chain 的深度规则展开，所以预算与轮转行为都不变。
        """
        if (
            not isinstance(reference, (str, int))
            or not str(reference)
            or len(str(reference)) > 256
        ):
            return None
        if budget is None:
            budget = [1]
        segment = {"type": "forward", "data": {"id": str(reference)}}
        enriched = await self.enrich_chain(bot, [segment], scope=scope, budget=budget)
        if not enriched or not isinstance(enriched[0], dict):
            return None
        data = enriched[0].get("data")
        if not isinstance(data, dict) or not isinstance(data.get("content"), list) or not data["content"]:
            # 读取上限 / 超时 / 失败：保持正文里的占位不动，下次查询再试
            return None
        return format_rich_segment(enriched[0])

    async def enrich_messages(self, bot, messages, *, scope, budget=None):
        if budget is None:
            budget = [3]
        result = []
        for message in messages:
            if not isinstance(message, dict):
                result.append(message)
                continue
            item = dict(message)
            item["message"] = await self.enrich_chain(bot, message.get("message", []), scope=scope, budget=budget)
            result.append(item)
        return result

    async def enrich_chain(self, bot, chain, *, scope, budget=None, depth=0):
        if not isinstance(chain, list):
            return chain
        if budget is None:
            budget = [3]
        result = []
        for segment in chain[:80]:
            if not isinstance(segment, dict) or segment.get("type") != "forward":
                result.append(segment)
                continue
            item = dict(segment)
            original_data = segment.get("data")
            data = dict(original_data) if isinstance(original_data, dict) else {}
            item["data"] = data
            result.append(item)
            if isinstance(data.get("content"), list) and data["content"]:
                continue
            reference = data.get("id", data.get("message_id"))
            if not isinstance(reference, (str, int)) or not str(reference) or len(str(reference)) > 256:
                continue
            if depth >= 2:
                data["_content_status"] = "budget"
                continue
            key = (id(bot), str(scope), str(reference))
            cached = self._cache.get(key)
            nodes = copy.deepcopy(cached[1]) if cached and time.monotonic() - cached[0] < 300 else None
            total_nodes = cached[2] if nodes is not None else 0
            if nodes is None:
                if budget[0] <= 0:
                    data["_content_status"] = "budget"
                    continue
                budget[0] -= 1
                try:
                    response = await asyncio.wait_for(bot.api.call_action("get_forward_msg", id=str(reference), message_id=str(reference)), timeout=5)
                    if isinstance(response, dict) and isinstance(response.get("data"), dict):
                        response = response["data"]
                    nodes = response.get("messages") if isinstance(response, dict) else None
                    if not isinstance(nodes, list) or not nodes:
                        raise ValueError("missing forward contents")
                    # Bound retained data as well as the number of remote reads.
                    total_nodes = len(nodes)
                    nodes = nodes[:20]
                    if len(json.dumps(nodes, ensure_ascii=False)) > 262144:
                        raise ValueError("forward contents too large")
                    self._cache[key] = (time.monotonic(), copy.deepcopy(nodes), total_nodes)
                    while len(self._cache) > 24:
                        self._cache.pop(next(iter(self._cache)))
                except asyncio.TimeoutError:
                    data["_content_status"] = "timeout"
                    continue
                except Exception:
                    data["_content_status"] = "failed"
                    continue
            expanded = []
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node = dict(node)
                field = "message" if "message" in node else "content"
                node[field] = await self.enrich_chain(bot, node.get(field, []), scope=scope, budget=budget, depth=depth + 1)
                expanded.append(node)
            data["content"] = expanded
            if total_nodes > len(expanded):
                data["_content_truncated"] = True
        # The read budget must not silently delete later ordinary segments.
        result.extend(chain[80:])
        return result
