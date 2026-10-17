import argparse
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ALLOWED_HOSTS = {"congbao.chinhphu.vn", "congbaocdn.chinhphu.vn", "g7.cdnchinhphu.vn"}
USER_AGENT = "MoveInMatePublicKnowledge/1.0"
MAX_BYTES = 25 * 1024 * 1024


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError(f"Source URL is not an approved public HTTPS source: {url}")


class PublicFetcher:
    def __init__(self, delay: float = 1.0):
        self.client = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        self.delay = max(1.0, delay)
        self.last_request = 0.0
        self.robots: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self.client.close()

    def request(self, url: str) -> tuple[int, dict, bytes]:
        validate_url(url)
        time.sleep(max(0.0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        with self.client.stream("GET", url) as response:
            body = bytearray()
            for portion in response.iter_bytes():
                body.extend(portion)
                if len(body) > MAX_BYTES:
                    raise ValueError("Download exceeds the 25 MiB safety limit")
            return response.status_code, dict(response.headers), bytes(body)

    def check_robots(self, url: str) -> None:
        host = urlsplit(url).netloc
        if host not in self.robots:
            status, _, payload = self.request(f"https://{host}/robots.txt")
            parser = RobotFileParser()
            if status == 404:
                parser.parse(["User-agent: *", "Allow: /"])
            elif status == 200:
                parser.parse(payload.decode("utf-8-sig").splitlines())
            else:
                raise ValueError(f"Cannot confirm crawling permission: robots.txt returned {status} for {host}")
            self.robots[host] = parser
        parser = self.robots[host]
        if not parser.can_fetch(USER_AGENT, url):
            raise ValueError(f"robots.txt disallows fetching {url}")
        self.delay = max(self.delay, float(parser.crawl_delay(USER_AGENT) or 0))

    def get(self, url: str) -> tuple[bytes, str]:
        for _ in range(5):
            validate_url(url)
            self.check_robots(url)
            status, headers, payload = self.request(url)
            if status in (301, 302, 303, 307, 308):
                url = urljoin(url, headers["location"])
                continue
            if status != 200:
                raise ValueError(f"Public source returned HTTP {status}: {url}")
            return payload, url
        raise ValueError("Too many source redirects")


class SourcePageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.pdf_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self.in_title = True
        attributes = dict(attrs)
        url = attributes.get("data-href")
        if url and urlsplit(url).path.lower().endswith(".pdf") and url not in self.pdf_urls:
            self.pdf_urls.append(url)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())


def split_page(text: str, max_chars: int = 1200) -> list[str]:
    text = unicodedata.normalize("NFC", text).replace("\x00", "")
    words = text.split()
    output: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + len(word) + 1 > max_chars:
            output.append(current)
            current = ""
        current = f"{current} {word}".strip()
    if current:
        output.append(current)
    return output


def archive_payload(directory: Path, payload: bytes, suffix: str) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.{suffix}"
    if not path.exists():
        path.write_bytes(payload)
    return {"sha256": digest, "bytes": len(payload), "archive_file": str(path)}


def crawl_source(source: dict, fetcher: PublicFetcher, knowledge: Any, archive: Path) -> dict:
    source_id = source["source_id"]
    if not re.fullmatch(r"[A-Z0-9-]{1,80}", source_id):
        raise ValueError("Invalid source identifier")
    payload, source_url = fetcher.get(source["source_url"])
    parser = SourcePageParser()
    parser.feed(payload.decode("utf-8-sig"))
    if source["document_number"] not in parser.title:
        raise ValueError("Official page title does not match the expected document number")
    if not 1 <= len(parser.pdf_urls) <= 10:
        raise ValueError("Expected between one and ten public PDF parts")
    directory = archive / source_id
    provenance = {"page": {**archive_payload(directory, payload, "html"), "url": source_url},
                  "original_title": parser.title, "files": []}
    chunks: list[dict] = []
    pages: list[str] = []
    for pdf_part, pdf_url in enumerate(parser.pdf_urls, start=1):
        pdf_payload, download_url = fetcher.get(pdf_url)
        if not pdf_payload.startswith(b"%PDF-"):
            raise ValueError("Official attachment is not a PDF")
        reader = PdfReader(io.BytesIO(pdf_payload))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 1000:
            raise ValueError("Encrypted or unexpectedly large PDF")
        extracted = [unicodedata.normalize("NFC", page.extract_text() or "").replace("\x00", "").strip()
                     for page in reader.pages]
        if sum(len(text) >= 30 for text in extracted) < 0.9 * len(extracted):
            raise ValueError("PDF is scanned or extraction is incomplete; OCR is required, not fabricated text")
        provenance["files"].append({
            **archive_payload(directory, pdf_payload, "pdf"), "url": download_url,
            "pdf_part": pdf_part, "page_count": len(extracted),
        })
        for page_number, text in enumerate(extracted, start=1):
            if len(text) < 30:
                continue
            pages.append(f"[PDF {pdf_part}, trang {page_number}]\n{text}")
            chunks.extend({"pdf_url": download_url, "pdf_part": pdf_part, "page_number": page_number,
                           "text": chunk} for chunk in split_page(text))
    content = "\n\n".join(pages)
    changed = knowledge.upsert_document({
        "id": source_id, "source_url": source_url, "title": parser.title[:499] + "…" if len(parser.title) > 500 else parser.title,
        "document_number": source["document_number"], "category": source["category"],
        "publisher": "Công báo điện tử Chính phủ", "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "provenance_json": json.dumps(provenance, ensure_ascii=False),
    }, chunks)
    return {"source_id": source_id, "title": parser.title, "source_url": source_url,
            "pages": sum(part["page_count"] for part in provenance["files"]),
            "chunks": len(chunks), "changed": changed, "legal_status": "not_verified"}


def main() -> int:
    from src.config import Settings
    from src.repositories import KnowledgeStore, Store

    parser = argparse.ArgumentParser(description="Import official public legal PDFs into PostgreSQL; never seed residents.")
    parser.add_argument("--sources", type=Path, default=ROOT / "scripts" / "public_sources.json")
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "data" / "crawled")
    parser.add_argument("--limit", type=int)
    arguments = parser.parse_args()
    sources = json.loads(arguments.sources.read_text(encoding="utf-8"))
    if arguments.limit is not None:
        if arguments.limit < 1:
            parser.error("--limit must be positive")
        sources = sources[:arguments.limit]
    settings = Settings()
    store = Store(settings.require_database_url())
    if store.engine.dialect.name != "postgresql":
        parser.error("Run inside Docker: docker compose exec backend python scripts/crawl_data.py")
    knowledge = KnowledgeStore(store)
    knowledge.create_tables()
    report = {"started_at": datetime.now(UTC).isoformat(), "database": "postgresql", "results": [], "errors": []}
    fetcher = PublicFetcher()
    try:
        for source in sources:
            try:
                result = crawl_source(source, fetcher, knowledge, arguments.archive_dir)
                report["results"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
            except Exception as exc:
                error = {"source_id": source.get("source_id"), "error": str(exc)}
                report["errors"].append(error)
                print(json.dumps(error, ensure_ascii=False), file=sys.stderr, flush=True)
    finally:
        fetcher.close()
        store.engine.dispose()
    report["finished_at"] = datetime.now(UTC).isoformat()
    report_dir = ROOT / "data" / "crawl_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {report_path}", flush=True)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
