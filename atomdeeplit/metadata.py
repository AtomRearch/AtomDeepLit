"""Resolve paper URLs to bibliographic metadata using public sources."""

from html import unescape
from html.parser import HTMLParser
import json
import re
import time
import unicodedata
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


CROSSREF = "https://api.crossref.org/works"
DATACITE = "https://api.datacite.org/dois"
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\"?#]+", re.I)
ARXIV = re.compile(r"(\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(v[1-9]\d*)?", re.I)
URL_PATTERN = r'https?://(?:[^\s<>()"\[\]]+|\([^()\s]*\))+'
URL = re.compile(r"\[[^\]\n]*\]\(<?(" + URL_PATTERN + r")>?\)|(" + URL_PATTERN + r")")
RSC_PATH = re.compile(r"^/(\w+)/article-abstract/(\d+)/(\d+)/(\d+)/\d+/([^/]+)/?$")
RSC_ISSN = {"ee": "1754-5706", "ta": "2050-7496"}
PII = re.compile(r"S\d{4}-\d{4}\(\d{2}\)\d{5}-\d", re.I)
LOOKUP_ERRORS = (OSError, ValueError, KeyError, TypeError)


def fetch(url, accept="application/json"):
    request = Request(url, headers={"Accept": accept, "User-Agent": "AtomDeepLit/0.1"})
    return urlopen(request, timeout=12)


def get_json(url):
    with fetch(url) as response:
        return json.load(response)


def crossref(path="", **params):
    # One concurrent request, paced for Crossref's public API pool.
    time.sleep(0.25)
    suffix = "?" + urlencode(params) if params else ""
    return get_json(CROSSREF + path + suffix)["message"]


def extract_urls(text):
    return [m[1] or m[2] for m in URL.finditer(text)]


def normalize_doi(value):
    match = DOI.search(unquote(value or "").strip())
    return match.group().casefold() if match else ""


def doi_from_url(url):
    parsed = urlsplit(url)
    path = unquote(parsed.path)
    if parsed.hostname == "pubs.acs.org" and "/article/doi/" in path:
        path = "/".join(path.split("/article/doi/", 1)[1].split("/")[:2])
    if parsed.hostname in {"nature.com", "www.nature.com"}:
        article = re.fullmatch(r"/articles/((?:s\d+|d\d+)-[\w-]+)/*", path)
        if article:
            return "10.1038/" + article[1].lower()
    doi = normalize_doi(path)
    # ChemRxiv DOI registrations distinguish /v1, /v2, etc.
    return doi.rstrip("/")


def arxiv_id(url):
    parsed = urlsplit(url)
    if parsed.hostname not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        return ""
    path = re.sub(r"^/(abs|pdf)/", "", parsed.path).removesuffix(".pdf")
    return path.lower() if ARXIV.fullmatch(path) else ""


def arxiv_record(identifier):
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    url = "https://export.arxiv.org/api/query?" + urlencode({"id_list": identifier})
    with fetch(url, "application/atom+xml") as response:
        root = ElementTree.fromstring(response.read(1_000_000))
    for entry in root.findall("a:entry", ns):
        resolved = arxiv_id(entry.findtext("a:id", "", ns))
        if not resolved or (resolved != identifier and re.sub(r"v\d+$", "", resolved) != identifier):
            continue
        doi = normalize_doi(entry.findtext("arxiv:doi", "", ns))
        if doi != "10.48550/arxiv." + re.sub(r"v\d+$", "", resolved):
            doi = ""
        return {
            "arxiv": resolved,
            "doi": doi,
            "title": plain(entry.findtext("a:title", "", ns)),
            "venue": "arXiv",
            "date": entry.findtext("a:published", "", ns)[:10],
            "type": "posted-content",
            "abstract": plain(entry.findtext("a:summary", "", ns)),
            "creators": [
                {"creatorType": "author", "name": name}
                for author in entry.findall("a:author", ns)
                if (name := plain(author.findtext("a:name", "", ns)))
            ],
            "source": "arXiv",
            "pdf_url": f"https://arxiv.org/pdf/{resolved}",
        }
    raise ValueError(f"arXiv metadata did not match {identifier}")


def url_key(url):
    p = urlsplit(url or "")
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))


def plain(value):
    return " ".join(unescape(re.sub(r"<[^>]*>", "", value or "")).split())


def title_key(title):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", plain(title)).casefold()))


class CitationMeta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            values = dict(attrs)
            key = (values.get("name") or values.get("property") or "").casefold()
            if values.get("content"):
                self.values.setdefault(key, values["content"])


def publisher_doi(url):
    # Network fallback is limited to supported publisher hosts.
    if urlsplit(url).hostname not in {"pubs.rsc.org", "www.cell.com", "cell.com", "pubs.acs.org"}:
        return ""
    with fetch(url, "text/html") as response:
        if response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
            return ""
        metadata = CitationMeta()
        metadata.feed(response.read(2_000_000).decode(response.headers.get_content_charset("utf-8"), errors="replace"))
    for name in ("citation_doi", "dc.identifier", "dc.identifier.doi", "prism.doi"):
        if doi := normalize_doi(metadata.values.get(name)):
            return doi
    return ""


def registry_match(url):
    parsed = urlsplit(url)
    if parsed.hostname == "pubs.rsc.org" and (m := RSC_PATH.match(parsed.path)):
        journal, volume, issue, page, slug = m.groups()
        if journal in RSC_ISSN:
            words = title_key(unquote(slug))
            works = crossref("", **{"query.bibliographic": f"{words} {page}", "filter": f"issn:{RSC_ISSN[journal]}", "rows": 5})
            candidates = [
                w for w in works["items"]
                if (w.get("volume"), w.get("issue"), w.get("page", "").split("-")[0]) == (volume, issue, page)
                and title_key(next(iter(w.get("title", [])), "")).startswith(words)
            ]
            return candidates[0] if len(candidates) == 1 else None
    if parsed.hostname in {"cell.com", "www.cell.com"} and (m := PII.search(unquote(parsed.path))):
        pii = re.sub(r"[^A-Z0-9]", "", m[0].upper())
        works = crossref("", filter=f"alternative-id:{pii}", rows=5)
        candidates = [w for w in works["items"] if pii in w.get("alternative-id", [])]
        return candidates[0] if len(candidates) == 1 else None
    return None


def crossref_record(work):
    venue = next(iter(work.get("container-title", [])), "")
    if not venue:
        venue = next(iter(work.get("institution", [])), {}).get("name", "")
    creators = []
    for author in work.get("author", []):
        if author.get("family"):
            creators.append({
                "creatorType": "author", "firstName": author.get("given", ""), "lastName": author["family"],
            })
        elif author.get("name"):
            creators.append({"creatorType": "author", "name": author["name"]})
    dates = work.get("published", work.get("issued", {})).get("date-parts", [])
    record = {
        "doi": normalize_doi(work.get("DOI")),
        "title": plain(next(iter(work.get("title", [])), "")),
        "venue": plain(venue),
        "type": work.get("type", ""),
        "abstract": plain(work.get("abstract")),
        "creators": creators,
        "date": "-".join(str(n).zfill(2) for n in dates[0]) if dates else "",
        "source": "Crossref",
    }
    if work.get("page") or work.get("article-number"):
        record["pages"] = work.get("page") or work["article-number"]
    record.update({field: work[field] for field in ("volume", "issue") if work.get(field)})
    return record


def resolve(url):
    doi = doi_from_url(url)
    try:
        if identifier := arxiv_id(url):
            return arxiv_record(identifier)
        if not doi:
            work = registry_match(url)
            if work:
                return crossref_record(work)
            doi = publisher_doi(url)
        if not doi:
            return {"error": "No supported DOI or arXiv identifier was verified"}
        encoded = quote(doi, safe="/")
        try:
            return crossref_record(crossref("/" + encoded))
        except HTTPError as error:
            if error.code != 404:
                raise
        data = get_json(DATACITE + "/" + encoded)["data"]["attributes"]
        return {
            "doi": normalize_doi(data["doi"]),
            "title": plain(next(iter(data.get("titles", [])), {}).get("title")),
            "venue": plain(data.get("publisher")),
            "type": data.get("types", {}).get("resourceTypeGeneral", ""),
            "creators": [
                {"creatorType": "author", "name": creator["name"]}
                for creator in data.get("creators", []) if creator.get("name")
            ],
            "date": str(data.get("publicationYear") or ""),
            "abstract": plain(next((d["description"] for d in data.get("descriptions", []) if d.get("descriptionType") == "Abstract"), "")),
            "source": "DataCite",
        }
    except (*LOOKUP_ERRORS, ElementTree.ParseError) as error:
        return {"detected_doi": doi, "error": str(error)}
