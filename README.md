# AtomDeepLit

[简体中文](README.zh-CN.md)

AtomDeepLit is a standalone Python CLI for literature intake. Paste paper URLs to retrieve public metadata, check for duplicates in Zotero, and review a batch before importing it. DeepSeek can suggest collections and reading priorities.

## Quick start

Use Python 3.11 or newer. Run the commands below from the source directory, or install the `atomdeeplit` command with `python -m pip install .`. Enable local application access in Zotero Desktop under **Settings → Advanced**. Writing requires Zotero 10 or later.

```powershell
python -X utf8 -m atomdeeplit preview --output plan.json
```

Paste URLs on separate lines or paste a block of Markdown links. Enter an empty line to finish. You can also pass URLs as arguments or use `--file urls.txt`. The preview shows titles, DOI or arXiv identifiers, possible duplicates, and candidate collections. Use `--no-zotero` for metadata lookup alone.

### Optional DeepSeek suggestions

Copy `config.example.toml` to `~/.atomdeeplit/config.toml` and set your research topics under `focus`. Use `--config PATH` before the subcommand to select another file.

```powershell
# PowerShell 7
$env:DEEPSEEK_API_KEY = Read-Host -MaskInput 'DeepSeek API key'
python -X utf8 -m atomdeeplit preview --ai --output plan.json
```

With `--ai`, AtomDeepLit sends public titles, abstract excerpts, your focus topics, and a shortlist of collection names to DeepSeek. If the request fails, you can still review the saved plan manually. The plan records the error under `ai_error`.

### Import reviewed items

```powershell
python -X utf8 -m atomdeeplit apply plan.json --select 1,3 --priority 3 --use-suggestions
```

`--select` chooses which rows to import, and `--priority` marks rows with the configured priority tag. `--use-suggestions` applies the proposed collections. Use `--collection 3=COLLKEY` to override the collection for row 3. An item with no collection selection goes to My Library.

AtomDeepLit checks for duplicates again, displays the pending changes, and takes a pre-write metadata snapshot. Type `APPLY` and approve the one-time authorization in Zotero. Each import batch is limited to 20 items.

### Check PDF attachments

```powershell
python -X utf8 -m atomdeeplit attachments receipt.json
```

This command checks attachments in the background. To look for missing PDFs, select the papers in Zotero and choose **Find Available PDFs** from the context menu. If a subscription PDF is available in your browser, download it there and attach it to the existing Zotero item.

## Data and scope

- The CLI runs locally and uses public metadata sources. DeepSeek is optional. Zotero library access uses the local API.
- Keep plans, receipts, and metadata snapshots in a private directory. The metadata ZIP supports review of changes. Use Zotero's data-directory backup procedure for a full restore.
- This release covers URL intake, optional triage suggestions, and attachment checks. Automated PDF retrieval, Flexcil export, and AtomLeaf handoff are future work.

[DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/) · [Zotero local API](https://www.zotero.org/support/dev/web_api/v3/local_api) · [Zotero backup documentation](https://www.zotero.org/support/zotero_data)
