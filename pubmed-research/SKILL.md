---
name: pubmed-research
description: Search PubMed and Europe PMC, fetch biomedical article metadata or full text, resolve DOI/PMID/PMCID mappings, check biomedical spelling, explore MeSH, format citations, and find related papers using the bundled Python PubMed client. Use when a user asks for PubMed/PMC/Europe PMC literature lookup, PMID metadata retrieval, citation formatting, identifier conversion, or biomedical search strategy help.
metadata:
  short-description: PubMed literature search with Python
---

# PubMed Research

Use the bundled Python client for deterministic PubMed, PMC, Europe PMC, Unpaywall, MeSH, citation, and identifier tasks. It replaces the original TypeScript MCP server workflow with a skill-first workflow.

## Configuration

Start from `config.example.yaml`. Keep real secrets out of the skill folder unless the user explicitly wants a local config file.

Preferred runtime configuration order:

1. Pass `--config /path/to/config.yaml` to `scripts/pubmed.py`.
2. Or set environment variables: `NCBI_API_KEY`, `NCBI_ADMIN_EMAIL`, `UNPAYWALL_EMAIL`, `EUROPEPMC_EMAIL`.
3. If neither is present, NCBI still works anonymously at the lower rate limit.

The API key placeholder is `NCBI_API_KEY: "<YOUR_NCBI_API_KEY>"` in `config.example.yaml`. Do not replace it with a real key unless the user provides one and asks you to write it.

## Quick Commands

From this skill directory:

```bash
python3 scripts/pubmed.py search --query 'CRISPR cancer immunotherapy' --max-results 10 --summary-count 5 --format markdown
python3 scripts/pubmed.py fetch --pmids 31452104 38392781 --format json
python3 scripts/pubmed.py fulltext --pmcids PMC3531190 --sections methods results --format markdown
python3 scripts/pubmed.py cite --pmids 23193287 --style apa bibtex --format markdown
python3 scripts/pubmed.py related --pmid 23193287 --relationship similar --max-results 10
python3 scripts/pubmed.py spell --query 'brast canser'
python3 scripts/pubmed.py mesh --query 'myocardial infarction'
python3 scripts/pubmed.py lookup-citation --citation '{"journal":"proc natl acad sci u s a","year":"1991","volume":"88","firstPage":"3248","authorName":"mann bj"}'
python3 scripts/pubmed.py convert --id-type pmid --ids 23193287
python3 scripts/pubmed.py europepmc-search --query 'TITLE:"single cell" AND PUB_YEAR:2024' --page-size 10
python3 scripts/pubmed.py database-info
```

The default output is JSON. Use `--format markdown` when showing results to a user.

## Workflow

1. For discovery, run `search` with `--summary-count` so the user gets titles and journals, not just PMIDs.
2. For known PMIDs, run `fetch` to retrieve structured metadata, abstracts, authors, DOI, PMCID, MeSH, keywords, and publication types.
3. For full text, prefer `fulltext --pmcids` when a PMCID is known. Use `--pmids` or `--dois` to let the script resolve through PMC, Europe PMC, and optionally Unpaywall.
4. For precise query expansion, run `mesh` and then search with quoted MeSH terms.
5. For noisy references, use `lookup-citation` before broad search.
6. For DOI/PMID/PMCID crosswalks, use `convert`; remember the PMC ID Converter only resolves records indexed in PMC.

## Notes

- NCBI asks clients to send a tool name and contact email. Configure `NCBI_ADMIN_EMAIL` when possible.
- `NCBI_API_KEY` raises the NCBI rate ceiling. The script automatically uses a shorter delay when a key is present.
- `UNPAYWALL_EMAIL` enables legal open-access DOI fallback for full text when PMC/Europe PMC do not provide JATS XML.
- Europe PMC is enabled by default. Disable it in config with `europepmc_enabled: false` or env `EUROPEPMC_ENABLED=false`.
- Avoid logging or echoing full URLs that contain API keys. The script redacts `api_key` in error messages.
