# QQ聊天记录本地缓存与区间检索实施计划

## 1. 目标与约束

### 1.1 目标
- 将历史查询从"每次直接调用QQ API翻页"升级为"本地缓存优先 + 增量同步"。
- 工具支持时间区间查询（`start_time` / `end_time`）。
- 在结果中明确返回本地覆盖范围，避免误判"已查全量历史"。

### 1.2 已确认约束（关键）
- QQ API 只能按页读取（`message_seq` 游标），无法按时间跳转，这是无解的。
- API 单页约 20 条，页与页之间天然重叠（至少 1 条重复，到达历史尽头时可能整页重复）。
- 因为消息 100% 从最新往旧灌入，仓库中的数据始终是一段连续的时间区间 `[covered_from, covered_to]`，中间不会有空洞。

### 1.3 核心设计推论
- 仓库填充只有两个方向：
  - **头部填充**：远程最新 → 仓库已有最新（`covered_to`），补上新产生的消息。
  - **尾部填充**：从仓库已有最旧（`covered_from`）→ 继续往更早翻，扩展历史深度。
- 不存在"中间补洞"的场景，因此同步逻辑比原计划简单。

## 2. 总体架构

### 2.1 分层
- `QQHistorySearchTool`：接收参数与返回格式化结果。
- `QQChatHistoryService`：业务编排（先同步、再查询、再格式化）。
- `HistoryRepository`（新增）：SQLite 读写、边界统计、去重插入、时间区间检索。

### 2.2 数据流
1. 工具接收查询参数（关键词、时间区间、数量等）。
2. 服务读取仓库当前覆盖边界（`covered_from` / `covered_to`）。
3. 根据查询需求决定同步策略：
   - 始终执行头部填充（补最新消息）。
   - 若 `start_time < covered_from`：额外执行尾部填充（往更早扩展）。
   - 若仓库为空：头部填充即为首次全量拉取。
4. 同步完成后，在本地 SQLite 执行检索并格式化。
5. 返回消息结果 + 覆盖边界说明。

## 3. 业务流程（重点）

### 3.1 查询入口流程
1. 解析参数：
   - 若传 `hours`：转换为 `start_time = now - hours`。
   - `end_time` 默认 `now`。
   - `limit` 默认值与上限做保护。
2. 从仓库读取该 group 的覆盖边界与游标信息。
3. 执行头部填充（始终执行）。
4. 若 `start_time < covered_from`：执行尾部填充。
5. 刷新覆盖边界。
6. 执行本地 SQL 查询（时间范围 + 关键词 + 用户过滤 + limit）。
7. 返回格式化消息及元信息：
   - `covered_from`, `covered_to`
   - `query_start`, `query_end`
   - `coverage_status`（FULL / PARTIAL）

### 3.2 头部填充（补最新消息）
- **起点**：`cursor_id = 0`（从远程最新页开始）。
- **逐页处理**：
  - 将每页消息通过 `UNIQUE(group_id, message_id)` 批量 INSERT OR IGNORE 到仓库。
  - 统计本页新增数 `new_in_page`。
- **停止条件**（满足任一即停）：
  - `new_in_page == 0`（整页全重复，说明已追上仓库已有数据）。
  - 本页为空（API 返回空列表）。
  - 连续失败超限 / 达到 `max_sync_pages`。
- **效果**：将 `covered_to` 推进到远程最新。
- **首次拉取**（仓库为空）：头部填充会一直拉到保护条件触发为止，相当于初始化仓库。可通过配置项 `bootstrap_rounds`（默认 1）控制首次建仓时允许的拉取轮次，每轮拉 `max_sync_pages` 页。设为 2~3 可在首次查询时获得更深的历史覆盖，降低首次查询返回 PARTIAL 的概率。

### 3.3 尾部填充（扩展历史深度）
- **前提**：仓库已有数据，且 `start_time < covered_from`。
- **起点**：使用仓库记录的 `oldest_seq`（上次尾部填充停下的位置），直接从该游标继续往更早翻。
- **逐页处理**：同头部填充，批量 INSERT OR IGNORE。
- **停止条件**（满足任一即停）：
  - 已拉取到的全局最旧消息时间 `<= start_time`（覆盖到目标起点）。
  - `new_in_page == 0`（到达历史尽头，整页全重复）。
  - 本页为空。
  - 连续失败超限 / 达到 `max_sync_pages`。
- **效果**：将 `covered_from` 向更早推进。
- **关键**：尾部填充不需要从最新页重新翻，直接从 `oldest_seq` 继续，避免重复拉取已缓存的数据。

### 3.4 游标推进规则

#### 3.4.1 message_seq 与 message_id 的职责划分（已确认）
- `message_seq`：翻页锚点，仅用于传给 API 的 `get_group_msg_history` 做分页定位。文档（NapCat / go-cqhttp）均未保证其唯一性。
- `message_id`：消息唯一标识，用作去重主键 `UNIQUE(group_id, message_id)`。
- 现有代码（`qq_history_service.py:160`）直接用 `message_id` 赋值给 `message_seq` 参数，在当前 NapCat 实现中可行。
- 仓库设计：
  - 去重主键：`UNIQUE(group_id, message_id)`，不依赖 `message_seq`。
  - 游标字段：`oldest_seq INTEGER`，语义为"传给 API 的 message_seq 值"。取值优先从响应取 `message_seq` 字段，若不存在降级取 `message_id`。
  - 停止判定不依赖"seq 唯一"，只依赖覆盖时间边界 + 每页新增量 + 保护阈值。
  - 运行时监控 seq 单调性，异常时记录警告日志。

#### 3.4.2 推进策略
- 现有代码使用 `server_messages[0]` 的 id 作为下一页游标（本页第一条，即最旧的一条）。
- 保持此策略不变，确保游标单调向旧消息推进。
- 运行时防御：若检测到本页返回的消息时间范围与上一页完全相同（游标未推进），强制停止并记录异常，避免死循环。
- 每次尾部填充结束后，更新仓库的 `oldest_seq`。

### 3.5 覆盖判定流程
- 查询区间 `[start_time, end_time]` 与 `[covered_from, covered_to]` 比较：
  - 若 `start_time >= covered_from` 且 `end_time <= covered_to` => `FULL`。
  - 否则 => `PARTIAL`，并在返回中提示具体哪一端未覆盖。
- `history_exhausted` 是独立的布尔标记，不属于 `coverage_status` 枚举。
- `coverage_status` 固定为 `FULL` 或 `PARTIAL`，含义纯粹：查询区间是否被本地数据完整覆盖。
- `history_exhausted` 仅在尾部填充模式下触发，且必须同时满足以下条件才可标记：
  1. 连续 N 页（可配置，默认 3）无新增消息（`new_in_page == 0`）。
  2. 本地最旧消息时间在这 N 页期间未发生变化（时间未推进）。
  3. 非失败退出（不是因为 API 报错停的）。
- 一旦标记 `history_exhausted = true`，后续同 group 的尾部填充直接跳过，不再浪费 API 调用。
- 返回结构示例：`{ coverage_status: "PARTIAL", history_exhausted: true, stop_reason: "history_exhausted" }`。

## 4. 数据模型设计（SQLite）

### 4.1 messages 表
- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `group_id TEXT NOT NULL`
- `message_id TEXT NOT NULL`
- `time INTEGER NOT NULL`（Unix秒）
- `user_id TEXT`
- `nickname TEXT`
- `search_text TEXT`（正文+可检索字段拼接）
- `raw_json TEXT NOT NULL`
- `created_at INTEGER NOT NULL`
- 约束：`UNIQUE(group_id, message_id)`

### 4.2 sync_state 表（新增，每个 group 一行）
- `group_id TEXT PRIMARY KEY`
- `oldest_seq INTEGER`（尾部填充的起点，传给 API 的 `message_seq` 值；优先从响应取 `message_seq`，降级取 `message_id`）
- `covered_from INTEGER`（最旧消息时间，Unix秒）
- `covered_to INTEGER`（最新消息时间，Unix秒）
- `history_exhausted INTEGER DEFAULT 0`（是否已到达平台历史极限，仅尾部填充可置 1）
- `last_sync_at INTEGER`（上次同步时间）

### 4.3 索引
- `idx_messages_group_time(group_id, time)`
- `idx_messages_group_user_time(group_id, user_id, time)`

### 4.4 可选FTS（第二阶段）
- 新增 `messages_fts`（FTS5）索引 `search_text`。
- 当前阶段先用 `LIKE` + 时间索引；数据量增大后再切FTS。

## 5. 接口改造

### 5.1 工具入参新增
- `start_time: int`（可选，Unix秒）
- `end_time: int`（可选，Unix秒）
- `user_ids: List[int]`（可选）
- `limit: int`（可选，替代/兼容 count）

### 5.2 兼容策略
- 保留 `hours` 与 `count`。
- 优先级：
  1. `start_time/end_time`（显式区间优先）
  2. `hours`（转换为 `start_time = now - hours`）
  3. 未传时间时走默认窗口（如最近24h，可配置）

## 6. 日志与可观测性

### 6.1 同步日志
- 每次同步输出：
  - `group_id`
  - `sync_direction`（head / tail）
  - `pages_fetched`
  - `inserted_count`
  - `stop_reason`（caught_up / empty / max_pages / failures / history_exhausted / target_reached）

### 6.2 查询日志
- 输出 `query_range`、`covered_range`、`coverage_status`、`result_count`。

## 7. 风险与对策

### 7.1 风险
- 历史深处数据无法一次补齐（API 机制决定，`max_sync_pages` 保护）。
- 初期缓存为空时，首次查询可能返回 PARTIAL。
- `oldest_seq` 对应的游标值理论上可能被平台清理（极端情况）。

### 7.2 对策
- 明确回传覆盖边界，避免"查全错觉"。
- 支持后台定时头部填充，让 `covered_to` 持续跟进最新。
- `oldest_seq` 失效时降级：从仓库 messages 表中查询该 group 最旧消息的 `message_seq` 字段重建游标；若 `message_seq` 字段不存在则降级取 `message_id`；若仓库也为空则走头部填充初始化。
- 可选提供管理员手动"深度同步"命令（多轮尾部填充）。

## 8. 边界清单

1. **仓库为空**：头部填充即首次拉取，拉到 `max_sync_pages` 停止，标记 PARTIAL。
2. **查询区间晚于当前时间**：`end_time > now` 时裁剪为 `now`。
3. **查询区间非法**：`start_time > end_time` 直接返回参数错误。
4. **页间天然重叠**：每页约 20 条至少 1 条重复，不能把"出现重复"当异常，判停依据为"整页无新增"。
5. **到达历史尽头**：仅在尾部填充中触发，需连续多页（默认 3）无新增且最旧时间未推进且非失败退出，才标记 `history_exhausted = true`，后续同 group 的尾部填充直接跳过。
6. **无 `start_time` 的最近查询**：只做头部填充，不触发尾部填充。
7. **本地已完整覆盖查询区间**：头部填充快速追上后直接本地查询，不触发尾部填充。
8. **大区间 + 小 `limit`**：尾部填充确保覆盖到 `start_time`，返回时按 `limit` 截取。
9. **API 连续失败**：达到失败上限停止，返回已有数据 + PARTIAL。
10. **页内时间乱序**：用全局最小时间判断覆盖，不依赖单条位置。
11. **同群并发互斥**：同一 `group_id` 的头部填充和尾部填充不可并发执行。当前假设单进程部署，使用 per-group 的 `asyncio.Lock` 保证同一时刻只有一个同步操作在跑。DB 层兜底：`sync_state` 更新使用条件写入（`UPDATE ... WHERE covered_from > ? OR covered_from IS NULL` / `WHERE covered_to < ? OR covered_to IS NULL`），确保 `covered_from` 只允许变小、`covered_to` 只允许变大，即使锁失效也不会写坏边界。若未来扩展到多 worker，需升级为分布式锁或依赖 DB 条件更新。
12. **时区**：存储/比较统一 Unix 秒，展示时再做时区转换。
13. **oldest_seq 失效**：检测到 API 返回异常时，清除 `oldest_seq`，下次尾部填充从仓库 messages 表中该 group 最旧消息的 `message_seq` 字段重建游标，若无 `message_seq` 则降级取 `message_id`。
14. **覆盖判定基于原始消息**：不基于过滤命中量，过滤是查询层的事。
15. **结果为空的歧义**：区分"无匹配消息"和"未覆盖到查询起点"，通过 `coverage_status` 明确。

## 9. 实施步骤

1. 新增 `HistoryRepository`：SQLite 初始化、messages 表、sync_state 表、批量插入、边界查询。
2. 改造 `QQChatHistoryService`：
   - 拆分同步逻辑为 `_head_fill` 和 `_tail_fill` 两个方法。
   - 查询前先执行头部填充，按需执行尾部填充。
   - 查询改为从本地 SQLite 检索。
3. 改造工具参数：支持 `start_time/end_time/limit` 并兼容旧参数 `hours/count`。
4. 返回结果中加入覆盖信息（`covered_from`、`covered_to`、`coverage_status`）。
5. 单测：
   - 头部填充：追上已有数据后停止
   - 尾部填充：覆盖到目标时间后停止
   - 尾部填充：到达历史尽头（整页重复）后停止并标记 exhausted
   - 覆盖判定 FULL / PARTIAL
   - 参数兼容（hours/count → start_time/limit）

## 10. 验收标准

- 单次查询不再依赖全量远程翻页。
- 头部填充：从最新开始，追上仓库已有数据即停。
- 尾部填充：从 `oldest_seq` 继续，覆盖到 `start_time` 或历史尽头即停。
- 二次查询同一区间时，远程请求量显著下降（头部填充快速追上，尾部无需再拉）。
- 查询返回 `coverage_status`（FULL / PARTIAL）+ 独立布尔 `history_exhausted`，语义清晰不混用。
