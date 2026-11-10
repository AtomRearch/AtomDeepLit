"""Read and write through the official Zotero Desktop API."""

from collections import defaultdict
from datetime import datetime
import json
import re
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from zipfile import ZIP_DEFLATED, ZipFile

from .metadata import arxiv_id, doi_from_url, normalize_doi, title_key, url_key


BASE = "http://127.0.0.1:23119/api/"
LIBRARY = "users/0/"
OPENER = build_opener(ProxyHandler({}))


def request(route, payload=None, headers=None, timeout=45):
    req = Request(BASE + route, headers=headers or {})
    if payload is not None:
        req.data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req.add_header("Content-Type", "application/json")
    with OPENER.open(req, timeout=timeout) as response:
        return json.load(response), response.headers


def identity():
    _, headers = request(LIBRARY + "items?limit=1")
    return headers.get("Zotero-Server-ID"), headers.get("Last-Modified-Version")


def list_items(route, **params):
    rows = []
    while True:
        batch, headers = request(LIBRARY + route + "?" + urlencode({
            **params, "start": len(rows), "limit": 1000,
        }))
        total = int(headers["Total-Results"])
        rows.extend(row["data"] for row in batch)
        if len(rows) == total and len({row["key"] for row in rows}) == total:
            return rows
        if not batch or len(rows) >= total:
            raise ValueError(f"Unexpected result count in Zotero {route}")


def collection_paths(collections):
    by_key = {row["key"]: row for row in collections}
    paths = {}
    for key, row in by_key.items():
        names = [row["name"]]
        visited = {key}
        while parent := row.get("parentCollection"):
            if parent in visited:
                raise ValueError("Invalid Zotero collection hierarchy")
            visited.add(parent)
            row = by_key[parent]
            names.append(row["name"])
        paths[key] = " → ".join(reversed(names))
    return paths


def library():
    stamp = identity()
    items = list_items("items/top")
    collections = list_items("collections")
    if stamp != identity():
        raise ValueError("Zotero changed during the read. Run the command again.")
    return stamp, items, collections


def arxiv_work(identifier):
    return re.sub(r"v[1-9]\d*$", "", identifier)


def identities(url, doi, arxiv="", title=""):
    doi = normalize_doi(doi) or doi_from_url(url)
    arxiv = arxiv or arxiv_id(url)
    if doi.startswith("10.48550/arxiv."):
        arxiv = arxiv or doi.removeprefix("10.48550/arxiv.")
    values = {
        ("doi", doi), ("url", url_key(url)),
        ("arxiv", arxiv_work(arxiv)), ("title", title_key(title)),
    }
    return {pair for pair in values if pair[1]}


def make_index(items):
    index = defaultdict(list)
    for item in items:
        if item["itemType"] in {"note", "annotation"}:
            continue
        extra = re.search(r"^DOI:\s*(\S+)", item.get("extra", ""), re.M | re.I)
        archive = item.get("archiveID", "")
        if item.get("repository", "").casefold() != "arxiv":
            archive = ""
        keys = identities(item.get("url", ""), item.get("DOI", ""), archive, item.get("title", ""))
        if extra and (doi := normalize_doi(extra[1])):
            keys.add(("doi", doi))
        for pair in keys:
            index[pair].append(item)
    return index


def matches(index, url, record):
    by_key = {}
    for field, value in sorted(identities(url, record.get("doi", ""), record.get("arxiv", ""), record.get("title", ""))):
        for item in index.get((field, value), []):
            match = by_key.setdefault(item["key"], {"key": item["key"], "title": item.get("title", ""), "evidence": []})
            match["evidence"].append(field)
    return list(by_key.values())


def backup(directory, stamp, items, collections):
    versions, _ = request(LIBRARY + "items?format=versions")
    if identity() != stamp or not all(item["key"] in versions for item in items):
        raise ValueError("Library changed during the backup")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-metadata.zip")
    with ZipFile(path, "x", compression=ZIP_DEFLATED) as archive:
        for name, data in (("top-items", items), ("collections", collections), ("item-versions", versions)):
            archive.writestr(name + ".json", json.dumps(data, ensure_ascii=False))
    with ZipFile(path) as archive:
        if archive.testzip():
            raise ValueError(f"Metadata snapshot failed integrity check: {path}")
    return path


def create_items(stamp, payload):
    server, version = stamp
    if not server or version is None:
        raise ValueError("Writing requires Zotero Desktop 10+ and its local API")
    print("Approve one-time access in Zotero when the AtomDeepLit dialog appears.", flush=True)
    auth, _ = request("local/authorize", {"appName": "AtomDeepLit"},
                      {"Zotero-Server-ID": server}, timeout=180)
    if identity() != stamp:
        raise ValueError("Zotero changed while waiting for authorization")
    return request(LIBRARY + "items", payload, {
        "Zotero-Server-ID": server, "Zotero-API-Key": auth["key"],
        "If-Unmodified-Since-Version": version,
    })[0]


def pdf_count(key):
    children = list_items(f"items/{key}/children")
    return sum(child.get("contentType") == "application/pdf" for child in children)
