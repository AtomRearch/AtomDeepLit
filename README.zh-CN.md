# AtomDeepLit

一个独立的 Python CLI。贴入论文 URL，解析公开元数据，对照本机 Zotero 去重，用 DeepSeek 建议集合和精读优先级，审核后写入 Zotero。

[English](README.md)

## 开始使用

需要 Python 3.11+。在源码根目录直接运行。也可以用 `python -m pip install .` 安装 `atomdeeplit` 命令。

1. 在 Zotero Desktop 的「设置 → 高级」打开允许本机应用通信的选项。用 Zotero 10+ 执行写入。
2. 如果要用 AI，在终端会话里设置 `DEEPSEEK_API_KEY`。可把 `config.example.toml` 复制到用户目录下的 `.atomdeeplit/config.toml`，填上自己的 `focus` 和标签。也可以通过 `--config 路径` 选择配置。API key 从环境变量读入。
3. 运行预览。把 URL 一行一个或整段 Markdown 一起粘贴，最后输入一个空行。也可以传 `--file urls.txt` 或直接传 URL。

```powershell
# PowerShell 7
$env:DEEPSEEK_API_KEY = Read-Host -MaskInput 'DeepSeek API key'
python -X utf8 -m atomdeeplit preview --ai --output plan.json
```

预览显示题名、DOI 或 arXiv 版本号、匹配和集合建议。用 `--no-zotero` 可独立解析公开元数据。省略 `--ai` 可用 Zotero 查重和本地关键词匹配。DeepSeek 调用失败时仍会保存可人工审核的预览，详细原因写入 `ai_error`。

```powershell
python -X utf8 -m atomdeeplit apply plan.json --select 1,3 --priority 3 --use-suggestions
```

`--select` 确认要入库的行号，`--priority` 确认要打重点标签的行号。`--use-suggestions` 选择使用预览中的集合建议，`--collection 3=COLLKEY` 可以覆盖第 3 行的集合。没有选定集合时，条目进入个人文库根。工具重新查重，显示写入内容，保存带完整性检查的写入前元数据快照，然后通过 Zotero 的一次性授权写入。首批写入最多 20 条。

```powershell
python -X utf8 -m atomdeeplit attachments receipt.json
```

附件检查在后台读取已有 PDF 记录。要补全文，可在 Zotero 中多选论文并右键「查找全文」。缺的订阅 PDF 可通过有权限的浏览器会话下载，随后附到已有条目。

## 边界

- 运行时依赖 Python、开放元数据源，以及你选择启用的 Zotero 和 DeepSeek。Python 客户端可在本地维护，DeepSeek 仍是外部模型服务。
- `--ai` 会把公开题名、摘要片段、用户配置的关注主题以及少量本地集合名称发送给 DeepSeek。库查重、授权、写入和附件检查都走 Zotero 本地 API。写入由用户选择并确认。
- 预览、回执和元数据快照包含本地文库信息，适合存放在个人目录。元数据 ZIP 用于审核和核对变更。完整库恢复请使用 Zotero 数据目录备份。
- 当前版本支持 URL 入库、建议和附件核验。PDF 搜索、Flexcil 和 AtomLeaf 对接在后续版本。

[DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/) · [Zotero Local API](https://www.zotero.org/support/dev/web_api/v3/local_api) · [Zotero 数据备份](https://www.zotero.org/support/zotero_data)
