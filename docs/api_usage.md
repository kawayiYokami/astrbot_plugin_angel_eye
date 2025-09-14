# 萌娘百科 MediaWiki API 用法说明

本文档旨在为 `MoeGirlpediaSearcher` 插件提供萌娘百科 API 的核心用法参考。

**API 来源**: [https://zh.moegirl.org.cn/api.php](https://zh.moegirl.org.cn/api.php)

所有用法均基于 MediaWiki API，更多详细信息请参考官方文档：[MediaWiki API:Main_page](https://www.mediawiki.org/wiki/Special:MyLanguage/API:Main_page)。

---

## 核心用法：搜索 (`action=query`)

`action=query` 是 MediaWiki API 中最强大的功能之一，用于获取 Wiki 数据。我们的搜索服务主要依赖于此。

### 关键词搜索 (`list=search`)

这是实现搜索功能的核心。通过组合不同的参数，可以实现强大的搜索功能。

**基础示例**：搜索关键词“初音未来”，返回最多5条结果。

```
https://zh.moegirl.org.cn/api.php?action=query&format=json&list=search&srsearch=初音未来&srlimit=5
```

**关键参数说明**：

| 参数 | 描述 | 示例值 |
| --- | --- | --- |
| `action` | 要执行的操作。 | `query` |
| `format` | 输出的数据格式。 | `json` |
| `list` | 要获取的列表类型。 | `search` |
| `srsearch` | 要搜索的关键词。 | `初音未来` |
| `srlimit` | 返回结果的最大数量。 | `5` |
| `srprop` | 附加的属性，例如摘要。 | `snippet` |

---

## 页面内容获取 (`action=parse`)

`action=parse` 可以解析内容并返回解析器输出，是实现“read服务”的关键。我们可以用它来获取页面的HTML内容，然后从中提取正文。

**基础示例**：获取页面“初音未来”的HTML内容。

```
https://zh.moegirl.org.cn/api.php?action=parse&page=初音未来&prop=text&format=json
```

**关键参数说明**：

| 参数 | 描述 | 示例值 |
| --- | --- | --- |
| `action` | 要执行的操作。 | `parse` |
| `page` | 要解析的页面标题。 | `初音未来` |
| `prop` | 要获取的信息。`text` 表示获取页面的HTML内容。 | `text` |
| `format` | 输出的数据格式。 | `json` |

返回的JSON中，`parse.text['*']` 字段将包含完整的页面HTML。

---

## API 使用礼仪与最佳实践

为了确保我们的插件不会对萌娘百科的服务器造成过大负担，并保持良好的社区关系，请遵循以下礼仪：

1.  **设置 `User-Agent`**: 所有非浏览器的请求都应该包含一个清晰的 `User-Agent` 请求头，以表明请求的来源。
    ```python
    HEADERS = {
        "User-Agent": "AstrBot-MoeGirlSearchPlugin/1.0 (https://github.com/your-repo)"
    }
    ```

2.  **遵守请求频率**: 不要进行过于频繁的请求。在连续的请求之间最好加入短暂的延时。

3.  **缓存结果**: 对于不经常变化的数据，可以考虑在本地进行缓存，以减少不必要的API调用。

4.  **处理错误**: 优雅地处理API可能返回的错误，例如 `maxlag`（服务器高负载）错误，并在适当的时候重试。

5.  **明确来源**: 如果在您的应用中展示了来自萌娘百科的内容，请务必注明内容来源。
