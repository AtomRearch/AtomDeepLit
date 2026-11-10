"""Standalone terminal workflow for literature intake and attachment checks."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tomllib
from urllib.error import HTTPError

from . import __version__, metadata, triage, zotero


SCHEMA = "atomdeeplit/plan/v1"
MAX_BATCH = 20
MAX_PREVIEW = 50
DEFAULT_CONFIG = Path.home() / ".atomdeeplit" / "config.toml"
DEFAULT_BACKUP = DEFAULT_CONFIG.parent / "backups"


def load_config(path):
    if path is None and not DEFAULT_CONFIG.exists():
        return {}
    with (path or DEFAULT_CONFIG).open("rb") as file:
        return tomllib.load(file)


def user_input(args):
    if args.file:
        if args.urls:
            raise ValueError("Provide URLs or --file, not both")
        return args.file.read_text(encoding="utf-8-sig")
    if args.urls:
        return " ".join(args.urls)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    print("Paste URLs. Submit an empty line to finish.", file=sys.stderr)
    lines = []
    while line := input().strip():
        lines.append(line)
    return "\n".join(lines)


def save_json(path, data, overwrite=False):
    with path.open("w" if overwrite else "x", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def read_plan(path):
    plan = json.loads(path.read_text(encoding="utf-8-sig"))
    if plan.get("schema") != SCHEMA:
        raise ValueError("Unsupported AtomDeepLit plan")
    rows = {row["row"]: row for row in plan["rows"]}
    if len(rows) != len(plan["rows"]):
        raise ValueError("Duplicate row numbers in the plan")
    return plan, rows


def preview(args, config):
    urls = metadata.extract_urls(user_input(args))
    if not 1 <= len(urls) <= MAX_PREVIEW:
        raise ValueError(f"Supply 1–{MAX_PREVIEW} URLs per preview")
    if args.output.exists():
        raise ValueError(f"Preview output already exists: {args.output}")
    if args.no_zotero:
        stamp, items, collections = (None, None), [], []
    else:
        stamp, items, collections = zotero.library()
    index, paths = zotero.make_index(items), zotero.collection_paths(collections)
    rows = []
    seen = {}
    resolved = {}
    for number, url in enumerate(urls, 1):
        url_id = metadata.url_key(url)
        if url_id not in resolved:
            resolved[url_id] = metadata.resolve(url)
        record = resolved[url_id]
        identity = sorted(pair for pair in zotero.identities(url, record.get("doi", ""), record.get("arxiv", ""))
                          if pair[0] != "title")
        earlier = min((seen[pair] for pair in identity if pair in seen), default=None)
        for pair in identity:
            seen.setdefault(pair, earlier or number)
        news = record.get("doi", "").startswith("10.1038/d41586-")
        duplicate = zotero.matches(index, url, record)
        rows.append({
            "row": number, "url": url, "record": record, "news": news,
            "existing": duplicate, "same_batch_row": earlier,
            "collection_candidates": triage.shortlist(record, paths) if paths and record.get("title") and not duplicate and not earlier else [],
        })
    suggestions = {}
    ai_error = None
    if args.ai:
        try:
            candidates = [row for row in rows if row["record"].get("title") and not row["existing"] and not row["same_batch_row"]]
            for start in range(0, len(candidates), MAX_BATCH):
                suggestions.update(triage.suggest(
                    candidates[start:start + MAX_BATCH], config.get("focus", []),
                    config.get("model", triage.DEFAULT_MODEL),
                ))
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            ai_error = str(error)
            print(f"DeepSeek suggestions unavailable: {ai_error}", file=sys.stderr)
    for row in rows:
        record = row["record"]
        row["suggestion"] = suggestions.get(row["row"])
        description = record.get("title") or record["error"]
        status = ("unresolved" if record.get("error") else "existing / possible match" if row["existing"]
                  else f"repeated row {row['same_batch_row']}"
                  if row["same_batch_row"] else "metadata only" if row["news"] else "review")
        print(f"{row['row']:2d}. [{status}] {description}")
        if identifier := record.get("doi") or record.get("arxiv"):
            print(f"    {identifier}")
        if choice := row["suggestion"]:
            path = paths.get(choice["collection_key"]) or "no collection suggested"
            marker = " / priority suggested" if choice["priority"] else ""
            print(f"    {path}{marker} · {choice['reason']}")
        elif row["collection_candidates"]:
            print("    " + " | ".join(f"{c['path']} [{c['key']}]" for c in row["collection_candidates"][:3]))
    save_json(args.output, {
        "schema": SCHEMA, "created_at": datetime.now(timezone.utc).isoformat(),
        "zotero_server": stamp[0], "library_checked": not args.no_zotero,
        "ai_error": ai_error, "rows": rows,
    })
    print(f"Read-only preview saved to {args.output}")


def row_numbers(value):
    return {int(part) for part in value.split(",")} if value else set()


def make_item(row, collection, priority, config):
    record = row["record"]
    is_preprint = bool(record.get("arxiv")) or record.get("type", "").casefold() in {"posted-content", "preprint"}
    item_type = "magazineArticle" if row["news"] else "preprint" if is_preprint else "journalArticle"
    tags = [config.get("unread_tag", ""), config.get("priority_tag", "💥") if priority else ""]
    item = {
        "itemType": item_type, "title": record["title"], "url": row["url"],
        "creators": record.get("creators", []), "date": record.get("date", ""),
        "abstractNote": record.get("abstract", ""), "DOI": record.get("doi", ""),
        "collections": [collection] if collection else [],
        "tags": [{"tag": tag} for tag in dict.fromkeys(tags) if tag],
    }
    if is_preprint:
        item.update(repository=record.get("venue", ""), genre="Preprint")
        if record.get("arxiv"):
            item["archiveID"] = record["arxiv"]
    else:
        item["publicationTitle"] = record.get("venue", "")
        item.update({field: record[field] for field in ("volume", "issue", "pages") if record.get(field)})
    return item


def verify_item(item, planned):
    arxiv = planned.get("archiveID")
    stored_arxiv = item.get("archiveID") or metadata.arxiv_id(item.get("url", ""))
    return (
        item.get("itemType") == planned["itemType"]
        and metadata.normalize_doi(item.get("DOI")) == metadata.normalize_doi(planned["DOI"])
        and item.get("collections") == planned["collections"]
        and (not arxiv or stored_arxiv == arxiv)
        and {tag["tag"] for tag in planned["tags"]} <= {tag["tag"] for tag in item.get("tags", [])}
    )


def apply(args, config):
    plan, rows = read_plan(args.plan)
    selected = row_numbers(args.select)
    priorities = row_numbers(args.priority)
    overrides = {}
    for choice in args.collection:
        number, separator, key = choice.partition("=")
        if not separator or int(number) in overrides:
            raise ValueError("Use --collection ROW=KEY once per row")
        overrides[int(number)] = key or None
    if not selected or len(selected) > MAX_BATCH or selected - rows.keys() or (priorities | overrides.keys()) - selected:
        raise ValueError("Select valid row numbers. Priority and collection overrides must refer to selected rows.")
    if args.receipt.exists():
        raise ValueError(f"Receipt output already exists: {args.receipt}")
    stamp, items, collections = zotero.library()
    if not stamp[0] or plan.get("zotero_server") != stamp[0]:
        raise ValueError("Run preview against this Zotero Desktop 10+ library before applying")
    paths = zotero.collection_paths(collections)
    index = zotero.make_index(items)
    payload, approved = [], []
    in_batch = set()
    for number in sorted(selected):
        row = rows[number]
        record = row["record"]
        if not record.get("title") or record.get("error") or row.get("same_batch_row"):
            raise ValueError(f"Row {number} is unresolved or repeated")
        if not record.get("doi") and not record.get("arxiv"):
            raise ValueError(f"Row {number} has no verified identifier")
        if matches := zotero.matches(index, row["url"], record):
            raise ValueError(f"Row {number} now matches {', '.join(item['key'] for item in matches)}. Run preview again.")
        identifiers = zotero.identities(row["url"], record.get("doi", ""), record.get("arxiv", ""))
        if identifiers & in_batch:
            raise ValueError(f"Row {number} repeats an identity in the selected batch")
        in_batch.update(identifiers)
        if number in priorities and row["news"]:
            raise ValueError(f"Row {number} is a news item")
        suggestion = row.get("suggestion") or {}
        target = overrides.get(number, suggestion.get("collection_key") if args.use_suggestions else None)
        if target and target not in paths:
            raise ValueError(f"Collection for row {number} does not exist")
        item = make_item(row, target, number in priorities, config)
        payload.append(item)
        approved.append(row)
        print(f"{number:2d}. {item['title']} → {paths.get(target, 'My Library')} · tags: {', '.join(t['tag'] for t in item['tags']) or 'none'}")
    if not args.yes and input("Type APPLY to write these items to Zotero: ").strip() != "APPLY":
        print("Import cancelled.")
        return
    backup = zotero.backup(args.backup_dir, stamp, items, collections)
    print(f"Pre-write metadata snapshot: {backup}", flush=True)
    response = zotero.create_items(stamp, payload)
    results = []
    for offset, row in enumerate(approved):
        key = str(offset)
        outcome = response.get("successful", {}).get(key)
        if outcome:
            results.append({"row": row["row"], "key": outcome["key"], "title": row["record"]["title"], "news": row["news"]})
        else:
            error = response.get("failed", {}).get(key, {}).get("message", "Unconfirmed result. Recheck Zotero before retrying.")
            results.append({"row": row["row"], "error": error})
    receipt = {"schema": "atomdeeplit/receipt/v1", "zotero_server": stamp[0], "results": results}
    save_json(args.receipt, receipt)
    keys = [row["key"] for row in results if "key" in row]
    actual = {item["key"]: item for item in zotero.list_items("items", itemKey=",".join(keys))} if keys else {}
    for outcome, planned in zip(results, payload, strict=True):
        if "key" not in outcome:
            continue
        item = actual.get(outcome["key"], {})
        verified = verify_item(item, planned)
        outcome["verified"] = verified
        if not verified:
            print(f"Row {outcome['row']} was saved as {outcome['key']}; metadata needs review.", file=sys.stderr)
    save_json(args.receipt, receipt, overwrite=True)
    print(f"{len(keys)}/{len(results)} added. Receipt: {args.receipt}")
    return int(any(not row.get("verified") for row in results))


def attachments(args):
    receipt = json.loads(args.receipt.read_text(encoding="utf-8-sig"))
    if receipt.get("schema") != "atomdeeplit/receipt/v1" or receipt.get("zotero_server") != zotero.identity()[0]:
        raise ValueError("Receipt does not match this Zotero library")
    for row in receipt["results"]:
        if "key" in row and not row["news"]:
            print(f"{row['row']:2d}. {zotero.pdf_count(row['key'])} PDF · {row['key']} · {row['title']}")


def main():
    parser = argparse.ArgumentParser(prog="atomdeeplit", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="TOML settings; default ~/.atomdeeplit/config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("preview", help="Resolve URLs, check duplicates and produce a read-only plan")
    command.add_argument("urls", nargs="*")
    command.add_argument("--file", type=Path)
    command.add_argument("--no-zotero", action="store_true", help="Resolve metadata without checking a library")
    command.add_argument("--ai", action="store_true", help="Send metadata and candidate collection names to DeepSeek")
    command.add_argument("--output", type=Path, default=Path("plan.json"))
    command = commands.add_parser("apply", help="Review and import selected rows")
    command.add_argument("plan", type=Path)
    command.add_argument("--select", required=True, help="Comma-separated row numbers")
    command.add_argument("--priority", help="Comma-separated row numbers to tag as priority")
    command.add_argument("--collection", action="append", default=[], metavar="ROW=KEY")
    command.add_argument("--use-suggestions", action="store_true", help="Use DeepSeek's proposed collection keys")
    command.add_argument("--yes", action="store_true", help="Explicitly approve the displayed batch")
    command.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUP)
    command.add_argument("--receipt", type=Path, default=Path("receipt.json"))
    command = commands.add_parser("attachments", help="Check PDFs for previously imported items without controlling the UI")
    command.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "attachments":
            return attachments(args)
        config = load_config(args.config)
        return preview(args, config) if args.command == "preview" else apply(args, config)
    except HTTPError as error:
        parser.exit(1, f"Request failed (HTTP {error.code}): {error.reason}\n")
    except (OSError, ValueError, TypeError, KeyError, EOFError) as error:
        parser.exit(1, f"AtomDeepLit: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
