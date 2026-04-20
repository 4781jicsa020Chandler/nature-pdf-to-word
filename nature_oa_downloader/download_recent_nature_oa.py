#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


CROSSREF_WORKS_URL = "https://api.crossref.org/works"
NATURE_ISSN = "1476-4687"
NATURE_RESEARCH_DOI_PREFIX = "10.1038/s41586-"
OA_LICENSE_MARKER = "creativecommons.org/licenses/"
DEFAULT_LIMIT = 100
DEFAULT_ROWS = 100
DEFAULT_DELAY_SECONDS = 1.2
DEFAULT_TIMEOUT_SECONDS = 120


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


@dataclass
class PaperRecord:
    index: int
    doi: str
    title: str
    published_date: str
    article_url: str
    pdf_url: str
    license_urls: list[str]
    file_path: str
    download_status: str


@dataclass
class CurlSession:
    cookie_jar: Path
    user_agent: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the most recent open-access Nature research PDFs into a separate folder."
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Number of papers to download.")
    parser.add_argument(
        "--rows",
        type=int,
        default=DEFAULT_ROWS,
        help="Crossref page size. Crossref currently allows up to 1000.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Directory for metadata and downloaded PDFs.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help="Delay in seconds between network requests.",
    )
    parser.add_argument(
        "--email",
        default="",
        help="Email address for Crossref polite-pool identification.",
    )
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Only save metadata and download URLs. Do not fetch PDFs.",
    )
    return parser.parse_args()


def build_session(email: str, cookie_jar: Path) -> CurlSession:
    user_agent = "NatureOADownloader/1.0"
    if email:
        user_agent = f"{user_agent} ({email})"
    cookie_jar.parent.mkdir(parents=True, exist_ok=True)
    cookie_jar.touch(exist_ok=True)
    return CurlSession(cookie_jar=cookie_jar, user_agent=user_agent)


def run_curl(command: list[str]) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    return subprocess.run(
        ["/bin/zsh", "-lc", shlex.join(command)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def request_json(
    session: CurlSession,
    url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    retries: int = 3,
) -> dict[str, Any]:
    backoff = 2.0
    for attempt in range(1, retries + 1):
        try:
            command = [
                "curl",
                "--max-time",
                str(timeout),
                "--location",
                "--silent",
                "--show-error",
                "--fail",
                "--user-agent",
                session.user_agent,
                "--header",
                "Accept: application/json, text/html;q=0.9, */*;q=0.8",
                "--header",
                "Accept-Language: en-US,en;q=0.8",
                url,
            ]
            result = run_curl(command)
            return json.loads(result.stdout)
        except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Request failed for {url}: {exc}") from exc
            log(f"Request failed ({exc}). Retrying in {backoff:.1f}s...")
            time.sleep(backoff)
            backoff *= 2
    raise AssertionError("Unreachable")


def format_date(date_parts: dict[str, Any]) -> str:
    parts = (date_parts.get("date-parts") or [[]])[0]
    if not parts:
        return ""
    return "-".join(f"{value:02d}" if index else str(value) for index, value in enumerate(parts))


def first_title(item: dict[str, Any]) -> str:
    titles = item.get("title") or []
    if not titles:
        return item.get("DOI", "")
    return " ".join(str(title).strip() for title in titles if str(title).strip())


def find_link(item: dict[str, Any], content_type: str) -> str:
    for link in item.get("link") or []:
        if str(link.get("content-type", "")).lower() == content_type:
            return str(link.get("URL", ""))
    return ""


def is_open_access(item: dict[str, Any]) -> bool:
    for license_entry in item.get("license") or []:
        license_url = str(license_entry.get("URL", "")).lower()
        if OA_LICENSE_MARKER in license_url:
            return True
    return False


def is_nature_research_paper(item: dict[str, Any]) -> bool:
    doi = str(item.get("DOI", "")).lower()
    return doi.startswith(NATURE_RESEARCH_DOI_PREFIX)


def normalize_record(index: int, item: dict[str, Any]) -> PaperRecord:
    license_urls = [
        str(license_entry.get("URL", ""))
        for license_entry in item.get("license") or []
        if str(license_entry.get("URL", "")).strip()
    ]
    article_url = find_link(item, "text/html")
    if not article_url:
        article_url = str((((item.get("resource") or {}).get("primary") or {}).get("URL")) or item.get("URL") or "")
    return PaperRecord(
        index=index,
        doi=str(item.get("DOI", "")),
        title=first_title(item),
        published_date=format_date(item.get("issued") or item.get("published-online") or item.get("published") or {}),
        article_url=article_url,
        pdf_url=find_link(item, "application/pdf"),
        license_urls=license_urls,
        file_path="",
        download_status="pending",
    )


def collect_latest_papers(
    session: CurlSession,
    *,
    limit: int,
    rows: int,
    delay: float,
    email: str,
) -> list[PaperRecord]:
    if limit <= 0:
        return []

    papers: list[PaperRecord] = []
    seen_dois: set[str] = set()
    cursor = "*"

    while len(papers) < limit:
        params = {
            "filter": f"issn:{NATURE_ISSN},has-full-text:1,full-text.type:application/pdf",
            "sort": "published",
            "order": "desc",
            "rows": str(rows),
            "cursor": cursor,
        }
        if email:
            params["mailto"] = email
        url = f"{CROSSREF_WORKS_URL}?{urllib.parse.urlencode(params)}"
        payload = request_json(session, url)
        message = payload["message"]
        items = message.get("items") or []
        if not items:
            break

        for item in items:
            doi = str(item.get("DOI", ""))
            if not doi or doi in seen_dois:
                continue
            if not is_nature_research_paper(item):
                continue
            if not is_open_access(item):
                continue
            record = normalize_record(len(papers) + 1, item)
            if not record.pdf_url:
                continue
            papers.append(record)
            seen_dois.add(doi)
            if len(papers) >= limit:
                break

        if len(items) < rows:
            break

        cursor = str(message.get("next-cursor", ""))
        if not cursor:
            break
        time.sleep(delay)

    return papers


def sanitize_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in ("-", "_", ".") else "_" for character in value)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("._") or "paper"


def download_pdf(
    session: CurlSession,
    paper: PaperRecord,
    destination: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    tmp_path = destination.with_suffix(destination.suffix + ".part")
    command = [
        "curl",
        "--max-time",
        str(timeout),
        "--location",
        "--silent",
        "--show-error",
        "--fail",
        "--cookie",
        str(session.cookie_jar),
        "--cookie-jar",
        str(session.cookie_jar),
        "--user-agent",
        session.user_agent,
        "--header",
        f"Referer: {paper.article_url or 'https://www.nature.com/'}",
        "--header",
        "Accept: application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        "--output",
        str(tmp_path),
        paper.pdf_url,
    ]
    run_curl(command)

    first_chunk = tmp_path.read_bytes()[:8192]
    if not first_chunk.startswith(b"%PDF-"):
        preview = first_chunk[:200].decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"Expected a PDF from {paper.pdf_url}, but the saved response did not look like one. "
            f"Response preview: {preview!r}"
        )
    tmp_path.replace(destination)


def prime_article_session(session: CurlSession, article_url: str) -> None:
    if not article_url:
        return
    command = [
        "curl",
        "--max-time",
        str(DEFAULT_TIMEOUT_SECONDS),
        "--location",
        "--silent",
        "--show-error",
        "--fail",
        "--cookie",
        str(session.cookie_jar),
        "--cookie-jar",
        str(session.cookie_jar),
        "--user-agent",
        session.user_agent,
        "--header",
        "Accept: text/html,application/xhtml+xml,*/*;q=0.8",
        "--output",
        "/dev/null",
        article_url,
    ]
    run_curl(command)


def write_outputs(output_dir: Path, papers: list[PaperRecord]) -> None:
    json_path = output_dir / "papers.json"
    csv_path = output_dir / "papers.csv"
    urls_path = output_dir / "download_urls.txt"

    json_path.write_text(json.dumps([asdict(paper) for paper in papers], indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "doi",
                "title",
                "published_date",
                "article_url",
                "pdf_url",
                "license_urls",
                "file_path",
                "download_status",
            ],
        )
        writer.writeheader()
        for paper in papers:
            row = asdict(paper)
            row["license_urls"] = "; ".join(paper.license_urls)
            writer.writerow(row)

    urls_path.write_text("\n".join(paper.pdf_url for paper in papers) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    pdf_dir = output_dir / "pdfs"
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    session = build_session(args.email, output_dir / ".cookies.txt")
    papers = collect_latest_papers(
        session,
        limit=args.limit,
        rows=max(1, min(args.rows, 1000)),
        delay=max(args.delay, 0.0),
        email=args.email,
    )

    if not papers:
        log("No matching Nature OA papers were found.")
        write_outputs(output_dir, papers)
        return 1

    log(f"Found {len(papers)} recent open-access Nature research papers.")

    for paper in papers:
        filename = f"{paper.index:03d}_{sanitize_filename(paper.doi.replace('/', '_'))}.pdf"
        destination = pdf_dir / filename
        paper.file_path = str(destination)

        if args.metadata_only:
            paper.download_status = "metadata-only"
            continue

        if destination.exists() and destination.stat().st_size > 0:
            paper.download_status = "skipped-existing"
            continue

        try:
            download_pdf(session, paper, destination)
            paper.download_status = "downloaded"
        except Exception as first_error:
            log(f"Initial PDF fetch failed for {paper.doi}: {first_error}")
            try:
                prime_article_session(session, paper.article_url)
                time.sleep(max(args.delay, 0.0))
                download_pdf(session, paper, destination)
                paper.download_status = "downloaded"
            except Exception as second_error:
                paper.download_status = f"failed: {second_error}"
                if destination.exists():
                    destination.unlink(missing_ok=True)
                part_path = destination.with_suffix(destination.suffix + ".part")
                if part_path.exists():
                    part_path.unlink(missing_ok=True)
                log(f"Download failed for {paper.doi}: {second_error}")

        time.sleep(max(args.delay, 0.0))

    write_outputs(output_dir, papers)

    downloaded = sum(1 for paper in papers if paper.download_status == "downloaded")
    skipped = sum(1 for paper in papers if paper.download_status == "skipped-existing")
    failed = sum(1 for paper in papers if paper.download_status.startswith("failed:"))
    log(
        f"Finished. Downloaded={downloaded}, skipped-existing={skipped}, failed={failed}. "
        f"Output directory: {output_dir}"
    )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
