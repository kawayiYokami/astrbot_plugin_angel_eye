# Angel Eye - AstrBot 知识增强插件

[![License](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-green)](https://github.com/kawayiYokami/astrbot)

> 为 AI 装上"知识僚机"，让对话更智能、更实时

## 📖 概述

Angel Eye 是一个 AstrBot 插件，旨在解决大语言模型（LLM）的"记忆鸿沟"问题。通过在 LLM 调用前自动识别对话中的专有名词并链接百科知识，为大模型补充上下文，使其能够理解最新的网络流行语、游戏角色、动漫人物等实时性内容。

## ✨ 核心特性

- 🤖 **智能上下文分析**: 自动识别对话中需要补充知识的专有名词。
- 💬 **群聊历史分析**: 可通过自然语言指令，回顾和总结群聊历史记录。
- 🔍 **多源知识检索**: 支持维基百科、萌娘百科、维基数据等多个知识源。
- ⚡ **可插拔多模型架构**: 支持为分析、筛选、摘要等不同任务配置独立的大语言模型，实现成本与效果的最佳平衡。
- 🎯 **精准匹配**: 基于对话上下文智能选择最相关的知识条目。
- 💾 **智能缓存**: 本地缓存机制减少重复网络请求。
- 🔧 **高度可配置**: 灵活的配置选项，支持按需启用/禁用不同数据源和调整模型。

## 🏗️ 架构设计

Angel Eye 采用轻量级指令驱动的架构，包含三个核心角色：

### 🎭 角色系统

1. **Classifier (分类器)**
   - **核心职责**: 分析对话上下文，生成知识请求指令。
   - **绑定模型**: `classifier_model_id` (推荐使用高智能模型，如 Claude 3 Sonnet)。

2. **Filter (筛选器)**
   - **核心职责**: 从多个搜索结果中筛选出与对话最相关的条目。
   - **绑定模型**: `filter_model_id` (推荐使用快速、低成本的模型，如 Claude 3 Haiku)。

3. **Summarizer (摘要器)**
   - **核心职责**: 将百科全文或聊天记录提炼成简洁的背景知识。
   - **绑定模型**: `summarizer_model_id` (推荐使用兼具理解和生成能力均衡的模型)。

4. **SmartRetriever (智能检索器)**
   - **核心职责**: 作为调度中心，执行多源知识检索、处理缓存和网络请求。

## 🚀 快速开始

### 前置要求

- Python 3.8+
- AstrBot 框架
- 可用的 LLM Provider (如 Claude Haiku、GPT-3.5-turbo 等)

### 安装

1. 将插件目录放置到 AstrBot 的 `plugins` 目录中
2. 安装依赖：

```bash
pip install httpx beautifulsoup4 pydantic diskcache
```

3. 在 AstrBot 配置中启用插件

### 配置

插件的核心配置在 `_conf_schema.json` 文件中定义，你也可以通过 AstrBot 的网页端进行配置。

**模型配置**

| 配置项 | 默认模型 | 职责 | 推荐 |
| :--- | :--- | :--- | :--- |
| `classifier_model_id` | `gemini-2.5-flash` | **意图分析**：分析用户输入，决定是否需要以及需要何种知识。 | 使用能力最强的模型，保证分析的准确性。 |
| `filter_model_id` | `gemini-2.5-flash-lite` | **结果筛选**：从多个搜索结果中选出最匹配的一项。 | 使用速度快、成本低的模型，追求效率。 |
| `summarizer_model_id` | `gemini-2.5-flash-lite` | **内容摘要**：将长文本（百科、聊天记录）总结为简洁的背景知识。 | 使用理解和生成能力均衡的模型。 |

**其他主要配置**

| 配置项 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `wiki_summarizer_enabled` | `true` | 是否使用AI模型对过长的百科内容进行摘要。关闭后，将直接使用经过基础清洗的原文。 |
| `chat_summarizer_enabled` | `true` | 是否使用AI模型对QQ聊天记录进行摘要。关闭后，将直接使用原始聊天记录。 |

```json
{
  "persona_name": "fairy|仙灵",
  "classifier_model_id": "gemini-2.5-flash",
  "filter_model_id": "gemini-2.5-flash-lite",
  "summarizer_model_id": "gemini-2.5-flash-lite",
  "max_context_length": 2000,
  "moegirl_enabled": true,
  "wikipedia_enabled": true,
  "wikidata_enabled": true,
  "text_length_threshold": 2000,
  "max_search_results": 3,
  "timeout_seconds": 10,
  "llm_log_max_size_mb": 100,
  "max_history_chars": 50000,
  "wiki_summarizer_enabled": true,
  "chat_summarizer_enabled": true
}
```

## 📁 项目结构

```
astrbot_plugin_angel_eye/
├── main.py                 # 插件主入口
├── metadata.yaml           # 插件元数据
├── requirements.txt        # 依赖列表
├── _conf_schema.json       # 配置架构
├── clients/                # 知识源客户端
│   ├── base_client.py      # 基础客户端类
│   ├── moegirl_client.py   # 萌娘百科客户端
│   ├── wikipedia_client.py # 维基百科客户端
│   └── wikidata_client.py  # 维基数据客户端
├── core/                   # 核心功能模块
│   ├── cache_manager.py    # 缓存管理
│   ├── exceptions.py       # 异常处理
│   ├── log.py             # 日志管理
│   ├── validators.py      # 输入验证
│   └── wikitext_cleaner.py # Wiki文本清理
├── docs/                   # 文档
│   ├── api_usage.md       # API使用指南
│   └── plugin_*.md        # 插件指南
├── models/                 # 数据模型
│   ├── __init__.py
│   ├── knowledge.py       # 知识相关模型
│   ├── models.py          # 基础模型
│   ├── request.py         # 请求模型
│   └── results.py         # 结果模型
├── prompts/               # LLM提示模板
│   ├── classifier_prompt.md
│   ├── filter_prompt.md
│   └── summarizer_prompt.md
├── research/              # 研究测试文件
│   └── *.py              # 各种测试脚本
├── roles/                 # 核心角色实现
│   ├── classifier.py     # 分类器角色
│   ├── filter.py         # 过滤器角色
│   ├── smart_retriever.py # 智能检索器
│   └── summarizer.py     # 摘要器角色
└── tests/                 # 测试套件
    └── test_*.py         # 单元测试
```

## 🎯 使用场景

### 场景 1: ACG 内容理解

**用户输入**: "我觉得芙宁娜的角色塑造比甘雨要复杂多了"

**Angel Eye 处理**:
1. 识别 "芙宁娜" 和 "甘雨" 为需要查询的实体
2. 从萌娘百科获取角色信息
3. 为大模型注入背景知识

**结果**: AI 能够深入讨论角色特点，而不是回答"我不知道芙宁娜"

### 场景 2: 实时信息查询

**用户输入**: "帮我查一下朱祁镇的父亲是谁"

**Angel Eye 处理**:
1. 识别结构化事实查询需求
2. 从维基数据查询精确信息
3. 同时获取维基百科背景知识

**结果**: AI 提供准确的历史事实和背景信息

### 场景 3: 群聊历史回顾与总结

**用户输入**: "帮我看看最近2小时大家都在聊些什么，总结一下"

**Angel Eye 处理**:
1. **Classifier** 识别出这是一个关于 `qq_chat_history` 的查询请求。
2. **Classifier** 提取出关键参数，如 `time_range_hours: 2` 和 `summarize: true`。
3. **SmartRetriever** 调用 `QQChatHistoryService` 获取最近2小时的聊天记录。
4. **Summarizer** 将获取到的聊天记录进行提炼和总结。
5. 最终的摘要作为背景知识注入，供主模型参考。

**结果**: AI 能够对近期群聊内容给出一个简洁的总结，快速跟上话题。

## 🔧 开发指南

### 添加新的知识源

1. 在 `clients/` 目录下创建新的客户端类，继承 `BaseWikiClient`
2. 实现 `search()` 和 `get_page_content()` 方法
3. 在 `SmartRetriever` 中添加对新客户端的支持
4. 更新配置架构以支持新的数据源开关

### 自定义处理逻辑

修改相应角色类的实现：

- **Classifier**: 调整知识识别逻辑
- **Filter**: 修改候选条目选择策略
- **Summarizer**: 定制摘要生成规则

## 📊 性能优化

- **缓存策略**: 使用磁盘缓存减少重复网络请求
- **阈值控制**: 根据文本长度决定是否调用AI归纳
- **超时设置**: 配置网络请求超时时间
- **日志管理**: 可配置的LLM交互日志记录

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！


## 📄 许可证

本项目采用 GPL-3.0 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

- [AstrBot](https://github.com/kawayiYokami/astrbot) - 提供优秀的插件框架
- [MediaWiki API](https://www.mediawiki.org/wiki/API:Main_page) - 知识检索基础
- 所有贡献者和用户

---

**让AI对话不再有知识盲区** 🚀