#!/usr/bin/env python3
"""PubMed/PMC/Europe PMC command line helper for the pubmed-research skill.

The implementation intentionally uses the Python standard library so the skill
can run in a fresh workspace without package installation.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


NCBI_EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
PMC_ID_CONVERTER = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
PLACEHOLDER_RE = re.compile(r"^<.*>$|^\$\{[^}]+\}$")


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def text_of(node: ET.Element | None, default: str = "") -> str:
    if node is None:
        return default
    return collapse_ws("".join(node.itertext())) or default


def child(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for item in list(node):
        if strip_ns(item.tag) == name:
            return item
    return None


def children(node: ET.Element | None, name: str) -> list[ET.Element]:
    if node is None:
        return []
    return [item for item in list(node) if strip_ns(item.tag) == name]


def find_desc(node: ET.Element | None, name: str) -> ET.Element | None:
    if node is None:
        return None
    for item in node.iter():
        if strip_ns(item.tag) == name:
            return item
    return None


def find_desc_all(node: ET.Element | None, name: str) -> list[ET.Element]:
    if node is None:
        return []
    return [item for item in node.iter() if strip_ns(item.tag) == name]


def collapse_ws(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_config_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or PLACEHOLDER_RE.match(value):
            return None
    return value


def parse_scalar(value: str) -> Any:
    raw = value.strip()
    low = raw.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    return raw


def read_simple_yaml(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    data: dict[str, Any] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            data[key.strip()] = parse_scalar(value)
    return {k: clean_config_value(v) for k, v in data.items()}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    ncbi_api_key: str | None = None
    ncbi_admin_email: str | None = None
    ncbi_tool_identifier: str = "pubmed-research-skill"
    ncbi_request_delay_ms: int = 334
    ncbi_timeout_ms: int = 30000
    ncbi_max_retries: int = 4
    unpaywall_email: str | None = None
    unpaywall_timeout_ms: int = 20000
    europepmc_enabled: bool = True
    europepmc_email: str | None = None
    europepmc_timeout_ms: int = 20000
    europepmc_request_delay_ms: int = 200
    europepmc_max_retries: int = 3

    @classmethod
    def load(cls, path: str | None) -> "Config":
        raw = read_simple_yaml(path)
        cfg = cls(
            ncbi_api_key=clean_config_value(os.getenv("NCBI_API_KEY")) or raw.get("ncbi_api_key"),
            ncbi_admin_email=clean_config_value(os.getenv("NCBI_ADMIN_EMAIL"))
            or raw.get("ncbi_admin_email"),
            ncbi_tool_identifier=str(raw.get("ncbi_tool_identifier") or "pubmed-research-skill"),
            ncbi_request_delay_ms=int(raw.get("ncbi_request_delay_ms") or 334),
            ncbi_timeout_ms=int(raw.get("ncbi_timeout_ms") or 30000),
            ncbi_max_retries=int(raw.get("ncbi_max_retries") or 4),
            unpaywall_email=clean_config_value(os.getenv("UNPAYWALL_EMAIL"))
            or raw.get("unpaywall_email"),
            unpaywall_timeout_ms=int(raw.get("unpaywall_timeout_ms") or 20000),
            europepmc_enabled=env_bool(
                "EUROPEPMC_ENABLED", bool(raw.get("europepmc_enabled", True))
            ),
            europepmc_email=clean_config_value(os.getenv("EUROPEPMC_EMAIL"))
            or raw.get("europepmc_email"),
            europepmc_timeout_ms=int(raw.get("europepmc_timeout_ms") or 20000),
            europepmc_request_delay_ms=int(raw.get("europepmc_request_delay_ms") or 200),
            europepmc_max_retries=int(raw.get("europepmc_max_retries") or 3),
        )
        if cfg.ncbi_api_key and "ncbi_request_delay_ms" not in raw and not os.getenv(
            "NCBI_REQUEST_DELAY_MS"
        ):
            cfg.ncbi_request_delay_ms = 100
        if os.getenv("NCBI_REQUEST_DELAY_MS"):
            cfg.ncbi_request_delay_ms = int(os.environ["NCBI_REQUEST_DELAY_MS"])
        if os.getenv("NCBI_TIMEOUT_MS"):
            cfg.ncbi_timeout_ms = int(os.environ["NCBI_TIMEOUT_MS"])
        return cfg


class HttpClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._last_ncbi = 0.0
        self._last_epmc = 0.0

    def _sleep_for(self, kind: str) -> None:
        delay = (
            self.cfg.ncbi_request_delay_ms
            if kind == "ncbi"
            else self.cfg.europepmc_request_delay_ms
        ) / 1000.0
        last_attr = "_last_ncbi" if kind == "ncbi" else "_last_epmc"
        elapsed = time.monotonic() - getattr(self, last_attr)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        setattr(self, last_attr, time.monotonic())

    def request_text(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        *,
        method: str = "GET",
        timeout_ms: int | None = None,
        headers: dict[str, str] | None = None,
        throttle: str | None = None,
        retries: int = 3,
    ) -> str:
        params = {k: str(v) for k, v in (params or {}).items() if v is not None}
        data = None
        final_url = url
        if method == "GET" and params:
            final_url = f"{url}?{urllib.parse.urlencode(params)}"
        elif method == "POST":
            data = urllib.parse.urlencode(params).encode("utf-8")
        safe_url = re.sub(r"api_key=[^&]+", "api_key=<redacted>", final_url)
        timeout = (timeout_ms or self.cfg.ncbi_timeout_ms) / 1000.0
        for attempt in range(retries + 1):
            if throttle:
                self._sleep_for(throttle)
            req = urllib.request.Request(final_url, data=data, headers=headers or {}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:500]
                if exc.code in {400, 404, 422} or attempt >= retries:
                    raise RuntimeError(f"HTTP {exc.code} for {safe_url}: {body}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt >= retries:
                    raise RuntimeError(f"Request failed for {safe_url}: {exc}") from exc
            time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"Request failed for {safe_url}")

    def ncbi(self, endpoint: str, params: dict[str, Any], *, post: bool = False) -> str:
        final = {
            "tool": self.cfg.ncbi_tool_identifier,
            "email": self.cfg.ncbi_admin_email,
            "api_key": self.cfg.ncbi_api_key,
            **params,
        }
        suffix = "" if endpoint.endswith(".fcgi") else ".fcgi"
        url = f"{NCBI_EUTILS_BASE}/{endpoint}{suffix}"
        method = "POST" if post or len(urllib.parse.urlencode(final)) > 2000 else "GET"
        return self.request_text(
            url,
            final,
            method=method,
            throttle="ncbi",
            retries=self.cfg.ncbi_max_retries,
            timeout_ms=self.cfg.ncbi_timeout_ms,
        )

    def external_ncbi(self, url: str, params: dict[str, Any]) -> str:
        final = {
            "tool": self.cfg.ncbi_tool_identifier,
            "email": self.cfg.ncbi_admin_email,
            **params,
        }
        return self.request_text(
            url,
            final,
            throttle="ncbi",
            retries=self.cfg.ncbi_max_retries,
            timeout_ms=self.cfg.ncbi_timeout_ms,
        )


def xml_root(text: str) -> ET.Element:
    return ET.fromstring(text.encode("utf-8"))


def parse_esearch(xml_text: str) -> dict[str, Any]:
    root = xml_root(xml_text)
    return {
        "count": int(text_of(child(root, "Count"), "0")),
        "retMax": int(text_of(child(root, "RetMax"), "0")),
        "retStart": int(text_of(child(root, "RetStart"), "0")),
        "idList": [text_of(item) for item in find_desc_all(child(root, "IdList"), "Id")],
        "queryKey": text_of(child(root, "QueryKey"), "") or None,
        "webEnv": text_of(child(root, "WebEnv"), "") or None,
    }


def article_ids_from_summary(doc: ET.Element) -> dict[str, str]:
    ids: dict[str, str] = {}
    for item in find_desc_all(doc, "ArticleId"):
        id_type = (item.attrib.get("IdType") or item.attrib.get("idtype") or "").lower()
        value = text_of(item)
        if id_type and value:
            ids[id_type] = value
    for item in find_desc_all(doc, "Item"):
        if item.attrib.get("Name") == "ArticleIds":
            for sub in find_desc_all(item, "Item"):
                name = (sub.attrib.get("Name") or "").lower()
                if name and text_of(sub):
                    ids[name] = text_of(sub)
    return ids


def parse_esummary(xml_text: str) -> list[dict[str, Any]]:
    root = xml_root(xml_text)
    docs = find_desc_all(root, "DocumentSummary") or find_desc_all(root, "DocSum")
    out: list[dict[str, Any]] = []
    for doc in docs:
        pmid = doc.attrib.get("uid") or text_of(child(doc, "Id"))
        if strip_ns(doc.tag) == "DocSum":
            fields = {item.attrib.get("Name", ""): text_of(item) for item in children(doc, "Item")}
            title = fields.get("Title") or fields.get("FullJournalName")
            source = fields.get("Source") or fields.get("FullJournalName")
            pub_date = fields.get("PubDate") or fields.get("EPubDate")
            authors = fields.get("AuthorList") or fields.get("Author")
        else:
            title = text_of(child(doc, "Title"))
            source = text_of(child(doc, "Source")) or text_of(child(doc, "FullJournalName"))
            pub_date = text_of(child(doc, "PubDate")) or text_of(child(doc, "EPubDate"))
            author_names = [text_of(child(a, "Name")) for a in find_desc_all(child(doc, "Authors"), "Author")]
            authors = ", ".join([a for a in author_names[:3] if a])
            if len(author_names) > 3:
                authors += ", et al."
        ids = article_ids_from_summary(doc)
        doi = ids.get("doi")
        pmc_id = ids.get("pmc") or ids.get("pmcid")
        out.append(
            {
                "pmid": str(pmid),
                "title": title or None,
                "authors": authors or None,
                "source": source or None,
                "pubDate": pub_date or None,
                "doi": doi,
                "pmcId": pmc_id,
                "pubmedUrl": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                "pmcUrl": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/" if pmc_id else None,
            }
        )
    return out


def build_pubmed_query(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    query = args.query.strip()
    filters: dict[str, Any] = {}
    if args.min_date and args.max_date:
        date_type = args.date_type
        query += f" AND ({args.min_date.replace('-', '/')}[{date_type}] : {args.max_date.replace('-', '/')}[{date_type}])"
        filters["dateRange"] = {"minDate": args.min_date, "maxDate": args.max_date, "dateType": date_type}
    if args.publication_type:
        query += " AND (" + " OR ".join(f'"{p}"[Publication Type]' for p in args.publication_type) + ")"
        filters["publicationTypes"] = args.publication_type
    if args.author:
        query += f" AND {args.author}[Author]"
        filters["author"] = args.author
    if args.journal:
        query += f' AND "{args.journal}"[Journal]'
        filters["journal"] = args.journal
    if args.mesh:
        query += " AND (" + " AND ".join(f'"{m}"[MeSH Terms]' for m in args.mesh) + ")"
        filters["meshTerms"] = args.mesh
    if args.language:
        query += f" AND {args.language}[Language]"
        filters["language"] = args.language
    if args.has_abstract:
        query += " AND hasabstract[text word]"
        filters["hasAbstract"] = True
    if args.free_full_text:
        query += " AND free full text[filter]"
        filters["freeFullText"] = True
    if args.species:
        query += f" AND {args.species}[MeSH Terms]"
        filters["species"] = args.species
    return query, filters


def cmd_search(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    effective_query, filters = build_pubmed_query(args)
    es = parse_esearch(
        client.ncbi(
            "esearch",
            {
                "db": "pubmed",
                "term": effective_query,
                "retmax": args.max_results,
                "retstart": args.offset,
                "sort": args.sort,
                "usehistory": "y" if args.summary_count else None,
                "retmode": "xml",
            },
        )
    )
    summaries: list[dict[str, Any]] = []
    if args.summary_count and es["idList"]:
        params: dict[str, Any] = {"db": "pubmed", "version": "2.0", "retmode": "xml"}
        if es.get("webEnv") and es.get("queryKey"):
            params.update(
                {
                    "WebEnv": es["webEnv"],
                    "query_key": es["queryKey"],
                    "retmax": min(args.summary_count, len(es["idList"])),
                    "retstart": args.offset,
                }
            )
        else:
            params["id"] = ",".join(es["idList"][: args.summary_count])
        summaries = parse_esummary(client.ncbi("esummary", params))
    notice = None
    if es["count"] == 0:
        notice = "No results matched. Try spell, relaxing filters, or broadening the query."
    elif not es["idList"] and args.offset >= es["count"]:
        notice = f"Offset {args.offset} exceeds totalFound {es['count']}."
    return {
        "query": args.query,
        "effectiveQuery": effective_query,
        "totalFound": es["count"],
        "offset": args.offset,
        "pmids": es["idList"],
        "summaries": summaries,
        "appliedFilters": filters,
        "searchUrl": f"https://pubmed.ncbi.nlm.nih.gov/?term={urllib.parse.quote(effective_query)}",
        "notice": notice,
    }


def parse_date(node: ET.Element | None) -> dict[str, str] | None:
    if node is None:
        return None
    value = {
        "year": text_of(child(node, "Year")) or None,
        "month": text_of(child(node, "Month")) or None,
        "day": text_of(child(node, "Day")) or None,
        "medlineDate": text_of(child(node, "MedlineDate")) or None,
    }
    return {k: v for k, v in value.items() if v}


def parse_authors(article: ET.Element) -> tuple[list[dict[str, Any]], list[str]]:
    authors: list[dict[str, Any]] = []
    affiliations: list[str] = []
    aff_index: dict[str, int] = {}
    for author in find_desc_all(find_desc(article, "AuthorList"), "Author"):
        entry: dict[str, Any] = {}
        for xml_name, out_name in [
            ("LastName", "lastName"),
            ("ForeName", "firstName"),
            ("Initials", "initials"),
            ("CollectiveName", "collectiveName"),
        ]:
            value = text_of(child(author, xml_name))
            if value:
                entry[out_name] = value
        indices: list[int] = []
        for aff in find_desc_all(author, "Affiliation"):
            value = text_of(aff)
            if value:
                if value not in aff_index:
                    aff_index[value] = len(affiliations)
                    affiliations.append(value)
                indices.append(aff_index[value])
        if indices:
            entry["affiliationIndices"] = indices
        if entry:
            authors.append(entry)
    return authors, affiliations


def parse_pubmed_article(article: ET.Element, include_mesh: bool = True, include_grants: bool = False) -> dict[str, Any]:
    medline = find_desc(article, "MedlineCitation")
    art = find_desc(medline, "Article")
    pmid = text_of(find_desc(medline, "PMID"))
    journal = child(art, "Journal")
    journal_issue = find_desc(journal, "JournalIssue")
    authors, affiliations = parse_authors(article)
    abstract_parts = [text_of(item) for item in find_desc_all(art, "AbstractText")]
    ids = {}
    article_id_list = find_desc(find_desc(article, "PubmedData"), "ArticleIdList")
    for aid in children(article_id_list, "ArticleId"):
        typ = (aid.attrib.get("IdType") or "").lower()
        if typ:
            ids[typ] = text_of(aid)
    result: dict[str, Any] = {
        "pmid": pmid or None,
        "title": text_of(child(art, "ArticleTitle")) or None,
        "abstractText": "\n".join([p for p in abstract_parts if p]) or None,
        "authors": authors or None,
        "affiliations": affiliations or None,
        "journalInfo": {
            "title": text_of(child(journal, "Title")) or None,
            "isoAbbreviation": text_of(child(journal, "ISOAbbreviation")) or None,
            "issn": text_of(child(journal, "ISSN")) or None,
            "volume": text_of(child(journal_issue, "Volume")) or None,
            "issue": text_of(child(journal_issue, "Issue")) or None,
            "pages": text_of(find_desc(art, "MedlinePgn")) or None,
            "publicationDate": parse_date(child(journal_issue, "PubDate")),
        },
        "doi": ids.get("doi"),
        "pmcId": ids.get("pmc") or ids.get("pmcid"),
        "publicationTypes": [text_of(p) for p in find_desc_all(article, "PublicationType") if text_of(p)],
        "keywords": [text_of(k) for k in find_desc_all(article, "Keyword") if text_of(k)],
        "pubmedUrl": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
    }
    if result.get("pmcId"):
        result["pmcUrl"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{result['pmcId']}/"
    if include_mesh:
        terms = []
        for heading in find_desc_all(article, "MeshHeading"):
            desc = child(heading, "DescriptorName")
            if desc is None:
                continue
            terms.append(
                {
                    "descriptorName": text_of(desc),
                    "descriptorUi": desc.attrib.get("UI"),
                    "isMajorTopic": desc.attrib.get("MajorTopicYN") == "Y",
                    "qualifiers": [
                        {
                            "qualifierName": text_of(q),
                            "qualifierUi": q.attrib.get("UI"),
                            "isMajorTopic": q.attrib.get("MajorTopicYN") == "Y",
                        }
                        for q in children(heading, "QualifierName")
                    ],
                }
            )
        result["meshTerms"] = terms or None
    if include_grants:
        grants = []
        for grant in find_desc_all(article, "Grant"):
            grants.append(
                {
                    "grantId": text_of(child(grant, "GrantID")) or None,
                    "acronym": text_of(child(grant, "Acronym")) or None,
                    "agency": text_of(child(grant, "Agency")) or None,
                    "country": text_of(child(grant, "Country")) or None,
                }
            )
        result["grantList"] = grants or None
    return prune_none(result)


def prune_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: prune_none(v) for k, v in value.items() if v is not None and v != {} and v != []}
    if isinstance(value, list):
        return [prune_none(v) for v in value]
    return value


def cmd_fetch(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    xml = client.ncbi(
        "efetch",
        {"db": "pubmed", "id": ",".join(args.pmids), "retmode": "xml"},
        post=len(args.pmids) >= 100,
    )
    root = xml_root(xml)
    articles = [
        parse_pubmed_article(a, include_mesh=args.include_mesh, include_grants=args.include_grants)
        for a in find_desc_all(root, "PubmedArticle")
    ]
    returned = {a.get("pmid") for a in articles}
    unavailable = [pmid for pmid in args.pmids if pmid not in returned]
    return {
        "articles": articles,
        "totalReturned": len(articles),
        "unavailablePmids": unavailable or None,
    }


def cmd_convert(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    raw = client.external_ncbi(
        PMC_ID_CONVERTER,
        {"ids": ",".join(args.ids), "idtype": args.id_type, "format": "json"},
    )
    data = json.loads(raw)
    records = []
    for rec in data.get("records", []):
        out = {
            "requestedId": str(rec.get("requested-id", "")),
            "pmid": str(rec["pmid"]) if rec.get("pmid") is not None else None,
            "pmcid": rec.get("pmcid"),
            "doi": rec.get("doi"),
            "errmsg": rec.get("errmsg"),
        }
        if out["errmsg"] and str(out["errmsg"]).lower() == "identifier not found in pmc":
            out["errmsg"] = "Not in PMC ID Converter. Article may still exist in PubMed; try fetch or search."
        records.append(prune_none(out))
    return {
        "records": records,
        "totalConverted": len([r for r in records if not r.get("errmsg")]),
        "totalSubmitted": len(args.ids),
    }


def normalize_pmcid(value: str) -> str:
    return value if value.upper().startswith("PMC") else f"PMC{value}"


def parse_pmc_article(root: ET.Element, via_source: str = "pmc", epmc_id: str | None = None) -> dict[str, Any]:
    front = find_desc(root, "front")
    article_meta = find_desc(front, "article-meta")
    ids: dict[str, str] = {}
    for aid in children(article_meta, "article-id"):
        typ = aid.attrib.get("pub-id-type", "")
        ids[typ] = text_of(aid)
    title_group = find_desc(article_meta, "title-group")
    authors = []
    affiliations = [text_of(aff) for aff in find_desc_all(article_meta, "aff") if text_of(aff)]
    for contrib in find_desc_all(article_meta, "contrib"):
        if contrib.attrib.get("contrib-type") != "author":
            continue
        name = child(contrib, "name")
        collab = child(contrib, "collab")
        entry = {
            "givenNames": text_of(child(name, "given-names")) or None,
            "lastName": text_of(child(name, "surname")) or None,
            "collectiveName": text_of(collab) or None,
        }
        if any(entry.values()):
            authors.append(prune_none(entry))
    sections = []
    for sec in find_desc_all(find_desc(root, "body"), "sec"):
        if any(strip_ns(parent.tag) == "sec" for parent in []):
            pass
        title = text_of(child(sec, "title")) or None
        paras = [text_of(p) for p in children(sec, "p") if text_of(p)]
        if title or paras:
            sections.append({"title": title, "text": "\n\n".join(paras)})
    refs = []
    for ref in find_desc_all(root, "ref"):
        citation = text_of(ref)
        if citation:
            refs.append({"id": ref.attrib.get("id"), "citation": citation})
    pmcid = ids.get("pmc") or ids.get("pmcid")
    pmid = ids.get("pmid")
    doi = ids.get("doi")
    result = {
        "source": "pmc",
        "viaSource": via_source,
        "pmcId": normalize_pmcid(pmcid) if pmcid else None,
        "pmid": pmid,
        "doi": doi,
        "title": text_of(find_desc(title_group, "article-title")) or None,
        "abstract": text_of(find_desc(article_meta, "abstract")) or None,
        "authors": authors or None,
        "affiliations": affiliations or None,
        "sections": sections,
        "references": refs or None,
        "epmcId": epmc_id,
    }
    if pmid:
        result["pubmedUrl"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    if pmcid:
        result["pmcUrl"] = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{normalize_pmcid(pmcid)}/"
    return prune_none(result)


def fetch_pmc_xml(client: HttpClient, pmcid: str) -> str | None:
    try:
        return client.ncbi("efetch", {"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"})
    except RuntimeError:
        return None


def europepmc_fulltext(client: HttpClient, epmc_id: str) -> str | None:
    if not client.cfg.europepmc_enabled:
        return None
    try:
        return client.request_text(
            f"{EUROPEPMC_BASE}/{urllib.parse.quote(epmc_id)}/fullTextXML",
            headers={"Accept": "application/xml, text/xml, */*"},
            throttle="epmc",
            retries=client.cfg.europepmc_max_retries,
            timeout_ms=client.cfg.europepmc_timeout_ms,
        )
    except RuntimeError:
        return None


def search_europepmc_for_doi(client: HttpClient, doi: str) -> dict[str, Any] | None:
    if not client.cfg.europepmc_enabled:
        return None
    data = europepmc_search_raw(client, f'DOI:"{doi}"', 1, "*", ["MED", "PMC", "PPR"], "core", None)
    hits = data.get("resultList", {}).get("result", [])
    return hits[0] if hits else None


def unpaywall(client: HttpClient, doi: str) -> dict[str, Any] | None:
    if not client.cfg.unpaywall_email:
        return None
    try:
        data = client.request_text(
            f"{UNPAYWALL_BASE}/{urllib.parse.quote(doi, safe='')}",
            {"email": client.cfg.unpaywall_email},
            timeout_ms=client.cfg.unpaywall_timeout_ms,
            retries=1,
        )
        parsed = json.loads(data)
    except RuntimeError:
        return None
    best = parsed.get("best_oa_location") or {}
    url = best.get("url_for_landing_page") or best.get("url")
    return prune_none(
        {
            "source": "unpaywall",
            "doi": doi,
            "title": parsed.get("title"),
            "journal": parsed.get("journal_name"),
            "isOa": parsed.get("is_oa"),
            "license": best.get("license"),
            "url": url,
            "pdfUrl": best.get("url_for_pdf"),
            "note": "Unpaywall metadata only; this standard-library client does not parse publisher PDFs/HTML.",
        }
    )


def cmd_fulltext(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    identifiers = args.pmcids or args.pmids or args.dois
    id_type = "pmcid" if args.pmcids else "pmid" if args.pmids else "doi"
    if not identifiers:
        raise SystemExit("Provide exactly one of --pmcids, --pmids, or --dois.")
    articles = []
    unavailable = []
    pmcid_by_id: dict[str, str] = {}
    doi_by_id: dict[str, str] = {}
    if id_type in {"pmid", "doi"}:
        conv = cmd_convert(client, argparse.Namespace(ids=identifiers, id_type=id_type))
        for rec in conv["records"]:
            if rec.get("pmcid"):
                pmcid_by_id[rec["requestedId"]] = rec["pmcid"]
            if rec.get("doi"):
                doi_by_id[rec["requestedId"]] = rec["doi"]
    for item in identifiers:
        pmcid = normalize_pmcid(item) if id_type == "pmcid" else pmcid_by_id.get(item)
        doi = item if id_type == "doi" else doi_by_id.get(item)
        xml_text = fetch_pmc_xml(client, pmcid) if pmcid else None
        if xml_text:
            article = parse_pmc_article(xml_root(xml_text), "pmc")
            articles.append(filter_fulltext(article, args))
            continue
        epmc_hit = search_europepmc_for_doi(client, doi) if doi else None
        epmc_xml = None
        if epmc_hit:
            epmc_xml = europepmc_fulltext(client, epmc_hit.get("id") or epmc_hit.get("pmid") or item)
        if epmc_xml:
            article = parse_pmc_article(xml_root(epmc_xml), "europepmc", epmc_hit.get("id") if epmc_hit else None)
            articles.append(filter_fulltext(article, args))
            continue
        uw = unpaywall(client, doi) if doi else None
        if uw:
            articles.append(uw)
        else:
            unavailable.append({"id": item, "idType": id_type, "reason": "not-found-or-no-fulltext"})
    return {"articles": articles, "unavailable": unavailable or None}


def filter_fulltext(article: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.sections:
        wanted = [s.lower() for s in args.sections]
        article["sections"] = [
            sec
            for sec in article.get("sections", [])
            if any(w in (sec.get("title") or "").lower() for w in wanted)
        ]
    if args.max_sections is not None:
        article["sections"] = article.get("sections", [])[: args.max_sections]
    if not args.include_references:
        article.pop("references", None)
    return article


def cmd_spell(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    root = xml_root(client.ncbi("espell", {"db": "pubmed", "term": args.query, "retmode": "xml"}))
    corrected = text_of(find_desc(root, "CorrectedQuery")) or args.query
    return {"original": args.query, "corrected": corrected, "hasSuggestion": corrected != args.query}


def cmd_related(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    link_name = {
        "similar": "pubmed_pubmed",
        "cited_by": "pubmed_pubmed_citedin",
        "references": "pubmed_pubmed_refs",
    }[args.relationship]
    root = xml_root(
        client.ncbi(
            "elink",
            {
                "dbfrom": "pubmed",
                "db": "pubmed",
                "id": args.pmid,
                "cmd": "neighbor",
                "linkname": link_name,
                "retmode": "xml",
            },
        )
    )
    pmids = []
    for linksetdb in find_desc_all(root, "LinkSetDb"):
        if text_of(child(linksetdb, "LinkName")) == link_name or not pmids:
            pmids = [text_of(find_desc(link, "Id")) for link in children(linksetdb, "Link")]
    pmids = [p for p in pmids if p and p != args.pmid and p != "0"]
    summaries = []
    if pmids:
        summaries = parse_esummary(client.ncbi("esummary", {"db": "pubmed", "id": ",".join(pmids[: args.max_results]), "version": "2.0", "retmode": "xml"}))
    return {
        "sourcePmid": args.pmid,
        "relationship": args.relationship,
        "totalFound": len(pmids),
        "articles": summaries,
    }


def cmd_mesh(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    root = xml_root(client.ncbi("esearch", {"db": "mesh", "term": args.query, "retmax": args.max_results, "retmode": "xml"}))
    ids = [text_of(i) for i in find_desc_all(find_desc(root, "IdList"), "Id")]
    results = []
    if ids:
        summary = xml_root(client.ncbi("esummary", {"db": "mesh", "id": ",".join(ids), "retmode": "xml"}))
        for doc in find_desc_all(summary, "DocSum"):
            fields = {item.attrib.get("Name", ""): text_of(item) for item in children(doc, "Item")}
            results.append(
                prune_none(
                    {
                        "id": text_of(child(doc, "Id")),
                        "name": fields.get("DS_MeshTerms") or fields.get("Title") or fields.get("Name"),
                        "scopeNote": fields.get("DS_ScopeNote"),
                        "annotation": fields.get("DS_Annotation"),
                    }
                )
            )
    return {"query": args.query, "results": results}


def cmd_lookup_citation(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    citations = [json.loads(c) for c in args.citation]
    lines = []
    for i, c in enumerate(citations, 1):
        key = c.get("key") or str(i)
        fields = [c.get("journal", ""), c.get("year", ""), c.get("volume", ""), c.get("firstPage", ""), c.get("authorName", ""), key]
        lines.append("|".join(fields) + "|")
    raw = client.ncbi("ecitmatch", {"db": "pubmed", "retmode": "xml", "bdata": "\r".join(lines)}, post=True)
    results = []
    for line in raw.strip().splitlines():
        parts = line.split("|")
        key = parts[5] if len(parts) > 5 else str(len(results) + 1)
        pmid = parts[6] if len(parts) > 6 else ""
        status = "matched" if pmid and pmid != "NOT_FOUND" else "not_found"
        results.append({"key": key, "pmid": pmid or None, "matched": status == "matched", "status": status})
    return {
        "results": [prune_none(r) for r in results],
        "totalMatched": len([r for r in results if r["matched"]]),
        "totalSubmitted": len(citations),
    }


def europepmc_search_raw(client: HttpClient, query: str, page_size: int, cursor: str, sources: list[str] | None, result_type: str, sort: str | None) -> dict[str, Any]:
    if sources:
        source_clause = " OR ".join(f'SRC:"{s}"' for s in sources)
        query = f"({query}) AND ({source_clause})"
    params = {
        "query": query,
        "format": "json",
        "resultType": result_type,
        "pageSize": page_size,
        "cursorMark": cursor,
        "sort": sort,
        "email": client.cfg.europepmc_email,
    }
    raw = client.request_text(
        f"{EUROPEPMC_BASE}/search",
        params,
        throttle="epmc",
        retries=client.cfg.europepmc_max_retries,
        timeout_ms=client.cfg.europepmc_timeout_ms,
        headers={"Accept": "application/json"},
    )
    return json.loads(raw)


def cmd_europepmc_search(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    if not client.cfg.europepmc_enabled:
        raise SystemExit("Europe PMC is disabled. Set europepmc_enabled: true or EUROPEPMC_ENABLED=true.")
    data = europepmc_search_raw(client, args.query, args.page_size, args.cursor_mark, args.sources, args.result_type, args.sort)
    hits = []
    for item in data.get("resultList", {}).get("result", []):
        hits.append(
            prune_none(
                {
                    "source": item.get("source"),
                    "epmcId": item.get("id"),
                    "title": item.get("title"),
                    "authors": item.get("authorString"),
                    "journal": item.get("journalTitle"),
                    "pubYear": item.get("pubYear"),
                    "firstPublicationDate": item.get("firstPublicationDate"),
                    "pmid": item.get("pmid"),
                    "pmcId": item.get("pmcid"),
                    "doi": item.get("doi"),
                    "isOpenAccess": item.get("isOpenAccess") == "Y",
                    "hasFullTextXml": item.get("inPMC") == "Y",
                    "abstractSnippet": collapse_ws(item.get("abstractText", ""))[:700] or None,
                    "citedByCount": int(item["citedByCount"]) if str(item.get("citedByCount", "")).isdigit() else None,
                    "epmcUrl": f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}",
                }
            )
        )
    return {
        "hits": hits,
        "cursorMark": args.cursor_mark,
        "nextCursorMark": data.get("nextCursorMark"),
        "hitCount": int(data.get("hitCount", 0)),
        "searchUrl": f"https://europepmc.org/search?query={urllib.parse.quote(args.query)}",
    }


def author_label(article: dict[str, Any]) -> str:
    authors = article.get("authors") or []
    if isinstance(authors, str):
        return authors
    names = []
    for a in authors:
        if a.get("collectiveName"):
            names.append(a["collectiveName"])
        elif a.get("lastName"):
            initials = a.get("initials") or "".join(p[0] for p in (a.get("firstName") or "").split())
            names.append(f"{a['lastName']} {initials}".strip())
    return ", ".join(names[:3]) + (", et al." if len(names) > 3 else "")


def citation(article: dict[str, Any], style: str) -> str:
    title = str(article.get("title", "")).rstrip(".")
    journal = article.get("journalInfo", {}).get("isoAbbreviation") or article.get("journalInfo", {}).get("title", "")
    year = article.get("journalInfo", {}).get("publicationDate", {}).get("year", "n.d.")
    doi = article.get("doi")
    authors = author_label(article)
    pmid = article.get("pmid", "")
    if style == "apa":
        out = f"{authors}. ({year}). {title}. {journal}."
        return out + (f" https://doi.org/{doi}" if doi else "")
    if style == "mla":
        out = f'{authors}. "{title}." {journal}, {year}.'
        return out + (f" doi:{doi}." if doi else "")
    if style == "bibtex":
        key = re.sub(r"\W+", "", (authors.split(",")[0] if authors else "pubmed") + year + pmid)
        lines = [f"@article{{{key},", f"  title = {{{title}}},", f"  journal = {{{journal}}},", f"  year = {{{year}}},", f"  pmid = {{{pmid}}},"]
        if authors:
            lines.append(f"  author = {{{authors}}},")
        if doi:
            lines.append(f"  doi = {{{doi}}},")
        lines.append("}")
        return "\n".join(lines)
    if style == "ris":
        lines = ["TY  - JOUR", f"TI  - {title}", f"JO  - {journal}", f"PY  - {year}", f"ID  - {pmid}"]
        if doi:
            lines.append(f"DO  - {doi}")
        lines.append("ER  -")
        return "\n".join(lines)
    raise ValueError(f"Unknown citation style: {style}")


def cmd_cite(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    fetched = cmd_fetch(client, argparse.Namespace(pmids=args.pmids, include_mesh=False, include_grants=False))
    cites = []
    for article in fetched["articles"]:
        cites.append(
            {
                "pmid": article.get("pmid"),
                "title": article.get("title"),
                "citations": {style: citation(article, style) for style in args.style},
            }
        )
    return {
        "citations": cites,
        "totalSubmitted": len(args.pmids),
        "totalFormatted": len(cites),
        "unavailablePmids": fetched.get("unavailablePmids"),
    }


def cmd_database_info(client: HttpClient, args: argparse.Namespace) -> dict[str, Any]:
    root = xml_root(client.ncbi("einfo", {"db": "pubmed", "retmode": "xml"}))
    dbinfo = find_desc(root, "DbInfo")
    fields = []
    for field in find_desc_all(dbinfo, "Field"):
        fields.append(
            {
                "name": text_of(child(field, "Name")),
                "fullName": text_of(child(field, "FullName")),
                "description": text_of(child(field, "Description")),
            }
        )
    return {
        "dbName": text_of(child(dbinfo, "DbName")),
        "menuName": text_of(child(dbinfo, "MenuName")),
        "description": text_of(child(dbinfo, "Description")),
        "count": text_of(child(dbinfo, "Count")),
        "lastUpdate": text_of(child(dbinfo, "LastUpdate")),
        "fields": fields,
    }


def markdown_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|")


def as_markdown(result: dict[str, Any], command: str) -> str:
    if command == "search":
        lines = [
            "## PubMed Search Results",
            f"**Returned:** {len(result.get('pmids', []))} of {result.get('totalFound')} | **Offset:** {result.get('offset')}",
            f"**Search URL:** {result.get('searchUrl')}",
        ]
        if result.get("notice"):
            lines.append(f"\n> {result['notice']}")
        if result.get("pmids"):
            lines.append("\n**PMIDs:** " + ", ".join(result["pmids"]))
        if result.get("summaries"):
            lines.append("\n| PMID | Title | Authors | Source | Date | DOI |")
            lines.append("|:---|:---|:---|:---|:---|:---|")
            for s in result["summaries"]:
                lines.append(
                    f"| {s.get('pmid')} | {markdown_escape(s.get('title'))} | {markdown_escape(s.get('authors'))} | {markdown_escape(s.get('source'))} | {markdown_escape(s.get('pubDate'))} | {markdown_escape(s.get('doi'))} |"
                )
        return "\n".join(lines)
    if command in {"fetch", "fulltext"}:
        lines = [f"## PubMed {command.title()} Results"]
        for article in result.get("articles", []):
            lines.append(f"\n### {article.get('title') or article.get('pmid') or article.get('doi')}")
            for key in ["pmid", "pmcId", "doi", "pubmedUrl", "pmcUrl", "source", "viaSource", "url", "pdfUrl"]:
                if article.get(key):
                    lines.append(f"**{key}:** {article[key]}")
            if article.get("abstractText") or article.get("abstract"):
                lines.append("\n" + (article.get("abstractText") or article.get("abstract")))
            for sec in article.get("sections", [])[:10]:
                lines.append(f"\n#### {sec.get('title') or 'Section'}\n{sec.get('text', '')[:4000]}")
        if result.get("unavailable"):
            lines.append("\n### Unavailable\n```json\n" + json.dumps(result["unavailable"], indent=2) + "\n```")
        return "\n".join(lines)
    if command == "cite":
        lines = ["## PubMed Citations"]
        for entry in result.get("citations", []):
            lines.append(f"\n### PMID {entry.get('pmid')}")
            for style, value in entry.get("citations", {}).items():
                fence = style if style in {"bibtex", "ris"} else ""
                if fence:
                    lines.append(f"\n#### {style.upper()}\n```{fence}\n{value}\n```")
                else:
                    lines.append(f"\n#### {style.upper()}\n{value}")
        return "\n".join(lines)
    return "```json\n" + json.dumps(result, indent=2, ensure_ascii=False) + "\n```"


def emit(result: dict[str, Any], args: argparse.Namespace, command: str) -> None:
    if args.format == "markdown":
        print(as_markdown(prune_none(result), command))
    else:
        print(json.dumps(prune_none(result), indent=2, ensure_ascii=False))


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="Path to config YAML with placeholder-backed credentials.")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PubMed research helper.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search")
    add_common(p)
    p.add_argument("--query", required=True)
    p.add_argument("--max-results", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--sort", choices=["relevance", "pub_date", "author", "journal"], default="relevance")
    p.add_argument("--summary-count", type=int, default=0)
    p.add_argument("--min-date")
    p.add_argument("--max-date")
    p.add_argument("--date-type", choices=["pdat", "mdat", "edat"], default="pdat")
    p.add_argument("--publication-type", action="append")
    p.add_argument("--author")
    p.add_argument("--journal")
    p.add_argument("--mesh", action="append")
    p.add_argument("--language")
    p.add_argument("--has-abstract", action="store_true")
    p.add_argument("--free-full-text", action="store_true")
    p.add_argument("--species", choices=["humans", "animals"])

    p = sub.add_parser("fetch")
    add_common(p)
    p.add_argument("--pmids", nargs="+", required=True)
    p.add_argument("--include-mesh", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--include-grants", action="store_true")

    p = sub.add_parser("fulltext")
    add_common(p)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--pmcids", nargs="+")
    group.add_argument("--pmids", nargs="+")
    group.add_argument("--dois", nargs="+")
    p.add_argument("--sections", nargs="+")
    p.add_argument("--max-sections", type=int)
    p.add_argument("--include-references", action="store_true")

    p = sub.add_parser("cite")
    add_common(p)
    p.add_argument("--pmids", nargs="+", required=True)
    p.add_argument("--style", nargs="+", choices=["apa", "mla", "bibtex", "ris"], default=["apa"])

    p = sub.add_parser("related")
    add_common(p)
    p.add_argument("--pmid", required=True)
    p.add_argument("--relationship", choices=["similar", "cited_by", "references"], default="similar")
    p.add_argument("--max-results", type=int, default=10)

    p = sub.add_parser("spell")
    add_common(p)
    p.add_argument("--query", required=True)

    p = sub.add_parser("mesh")
    add_common(p)
    p.add_argument("--query", required=True)
    p.add_argument("--max-results", type=int, default=10)

    p = sub.add_parser("lookup-citation")
    add_common(p)
    p.add_argument("--citation", action="append", required=True, help="JSON object with journal/year/volume/firstPage/authorName/key.")

    p = sub.add_parser("convert")
    add_common(p)
    p.add_argument("--id-type", choices=["pmcid", "pmid", "doi"], required=True)
    p.add_argument("--ids", nargs="+", required=True)

    p = sub.add_parser("europepmc-search")
    add_common(p)
    p.add_argument("--query", required=True)
    p.add_argument("--page-size", type=int, default=25)
    p.add_argument("--cursor-mark", default="*")
    p.add_argument("--sources", nargs="+", choices=["MED", "PMC", "PPR", "PAT", "AGR"], default=["MED", "PMC", "PPR"])
    p.add_argument("--result-type", choices=["core", "lite"], default="core")
    p.add_argument("--sort")

    p = sub.add_parser("database-info")
    add_common(p)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = Config.load(args.config)
    client = HttpClient(cfg)
    handlers = {
        "search": cmd_search,
        "fetch": cmd_fetch,
        "fulltext": cmd_fulltext,
        "cite": cmd_cite,
        "related": cmd_related,
        "spell": cmd_spell,
        "mesh": cmd_mesh,
        "lookup-citation": cmd_lookup_citation,
        "convert": cmd_convert,
        "europepmc-search": cmd_europepmc_search,
        "database-info": cmd_database_info,
    }
    try:
        result = handlers[args.command](client, args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    emit(result, args, args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
