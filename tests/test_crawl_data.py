import hashlib
import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import func, select

from scripts import crawl_data
from src.api.routes import router
from src.config import Settings
from src.rag.retriever import RegulationRetriever
from src.repositories import KnowledgeStore
from src.repositories.knowledge import KnowledgeChunk, KnowledgeDocument
from src.repositories.store import Resident, now_utc

SOURCE_URL = "https://congbao.chinhphu.vn/van-ban/example.htm"
PDF_URL = "https://congbaocdn.chinhphu.vn/example.pdf"
SOURCE = {"source_id": "UNIT-TEST-SOURCE", "document_number": "UNIT-TEST/2026", "category": "residency", "source_url": SOURCE_URL}
PDF_TEXT = "Public residency document text for an isolated unit test, never production data. " * 5


def pdf_bytes(text: str = PDF_TEXT) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({
        NameObject("/F1"): writer._add_object(font),
    })})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 30 750 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class FixtureFetcher:
    def __init__(self, pdf: bytes | None = None, title: str = "UNIT-TEST/2026"):
        self.payloads = {
            SOURCE_URL: f'<html><title>{title}</title><a data-href="{PDF_URL}">PDF</a></html>'.encode(),
            PDF_URL: pdf if pdf is not None else pdf_bytes(),
        }
        self.urls: list[str] = []

    def get(self, url: str) -> tuple[bytes, str]:
        self.urls.append(url)
        return self.payloads[url], url


@pytest.fixture
def knowledge(app_container):
    with app_container.store.session() as session:
        session.query(Resident).filter_by(id="R001").delete()
    return app_container.knowledge_store


def row_count(knowledge: KnowledgeStore, model) -> int:
    with knowledge.store.session() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_crawl_saves_original_text_and_provenance_without_resident_seed(knowledge, tmp_path):
    fetcher = FixtureFetcher()
    result = crawl_data.crawl_source(SOURCE, fetcher, knowledge, tmp_path)
    assert result["pages"] == 1
    assert result["chunks"] == 1
    assert result["changed"] is True
    assert row_count(knowledge, Resident) == 0
    document = knowledge.list_documents(include_body=True)[0]
    assert PDF_TEXT.strip() in document["body"]
    assert document["source_url"] == SOURCE_URL
    assert document["legal_status"] == "not_verified"
    assert document["effective_date"] == ""
    part = document["provenance"]["files"][0]
    assert hashlib.sha256(Path(part["archive_file"]).read_bytes()).hexdigest() == part["sha256"]
    assert document["content_sha256"] == hashlib.sha256(document["body"].encode()).hexdigest()
    assert knowledge.list_chunks()[0]["download_url"] == PDF_URL


def test_recrawl_does_not_duplicate_documents_or_chunks(knowledge, tmp_path):
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), knowledge, tmp_path)
    result = crawl_data.crawl_source(SOURCE, FixtureFetcher(), knowledge, tmp_path)
    assert result["changed"] is False
    assert row_count(knowledge, KnowledgeDocument) == 1
    assert row_count(knowledge, KnowledgeChunk) == 1


def test_changed_source_replaces_chunks_transactionally(knowledge, tmp_path):
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), knowledge, tmp_path)
    text = "Changed public residency document in unit tests only. " * 60
    result = crawl_data.crawl_source(SOURCE, FixtureFetcher(pdf_bytes(text)), knowledge, tmp_path)
    assert result["changed"] is True
    assert row_count(knowledge, KnowledgeDocument) == 1
    assert row_count(knowledge, KnowledgeChunk) == result["chunks"]
    assert all("Changed public residency" in chunk["text"] for chunk in knowledge.list_chunks())


@pytest.mark.parametrize("payload", [b"not a PDF", pdf_bytes("")])
def test_invalid_or_scanned_pdf_does_not_overwrite_good_data(knowledge, tmp_path, payload):
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), knowledge, tmp_path)
    digest = knowledge.list_documents()[0]["content_sha256"]
    with pytest.raises(ValueError):
        crawl_data.crawl_source(SOURCE, FixtureFetcher(payload), knowledge, tmp_path)
    assert knowledge.list_documents()[0]["content_sha256"] == digest


def test_mismatched_official_title_is_rejected(knowledge, tmp_path):
    with pytest.raises(ValueError, match="title"):
        crawl_data.crawl_source(SOURCE, FixtureFetcher(title="Another document"), knowledge, tmp_path)
    assert row_count(knowledge, KnowledgeDocument) == 0


def test_invalid_source_identifier_is_rejected(knowledge, tmp_path):
    with pytest.raises(ValueError, match="identifier"):
        crawl_data.crawl_source({**SOURCE, "source_id": "../escape"}, FixtureFetcher(), knowledge, tmp_path)


@pytest.mark.parametrize("url", [
    "http://congbao.chinhphu.vn/document", "https://example.com/document",
    "https://congbao.chinhphu.vn.evil.test/document", "https://localhost/private",
    "https://user:secret@congbao.chinhphu.vn/document", "https://congbao.chinhphu.vn:444/document",
])
def test_only_approved_https_sources_are_allowed(url):
    with pytest.raises(ValueError):
        crawl_data.validate_url(url)


@pytest.mark.parametrize("status,payload", [(403, b"Forbidden"), (503, b"Unavailable"), (200, b"User-agent: *\nDisallow: /")])
def test_crawler_respects_denied_or_unavailable_robots(monkeypatch, status, payload):
    fetcher = crawl_data.PublicFetcher()
    monkeypatch.setattr(fetcher, "request", lambda url: (status, {}, payload))
    try:
        with pytest.raises(ValueError):
            fetcher.check_robots(SOURCE_URL)
    finally:
        fetcher.close()


def test_crawler_allows_missing_robots_and_checks_redirect_targets(monkeypatch):
    fetcher = crawl_data.PublicFetcher()
    def response(url):
        if url.endswith("robots.txt"):
            return 404, {}, b""
        return 302, {"location": "https://localhost/private"}, b""
    monkeypatch.setattr(fetcher, "request", response)
    try:
        with pytest.raises(ValueError, match="approved"):
            fetcher.get(SOURCE_URL)
    finally:
        fetcher.close()


def test_split_page_is_bounded_and_preserves_unicode():
    text = "Cư trú tại chung cư. " * 200
    chunks = crawl_data.split_page(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert " ".join(chunks) == " ".join(text.split())


def test_public_retriever_uses_database_not_demo_files_and_refreshes(knowledge, tmp_path, request):
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), knowledge, tmp_path / "archive")
    (tmp_path / "demo.md").write_text("Invalid unverified demo file", encoding="utf-8")
    retriever = RegulationRetriever(str(tmp_path), str(tmp_path / "chroma"), knowledge, include_demo=False)
    request.addfinalizer(retriever.close)
    rows = retriever.search("residency", "any-building")
    assert rows[0]["source_id"] == SOURCE["source_id"]
    assert rows[0]["site_id"] == "national"
    assert rows[0]["source_url"] == SOURCE_URL
    assert retriever._collection.count() == 1
    crawl_data.crawl_source(SOURCE, FixtureFetcher(pdf_bytes("Updated public residency statement for unit tests. " * 40)), knowledge, tmp_path / "archive")
    rows = retriever.search("Updated public residency", "another-building")
    assert "Updated" in rows[0]["text"]
    assert retriever._collection.count() == row_count(knowledge, KnowledgeChunk)
    assert retriever.list_documents("any-building")[0]["source_url"] == SOURCE_URL


def test_demo_knowledge_is_disabled_by_default():
    assert Settings(_env_file=None).include_demo_knowledge is False


@pytest.mark.asyncio
async def test_source_api_exposes_original_url_and_content_without_secrets(app_container, tmp_path):
    with app_container.store.session() as session:
        session.get(Resident, "R001").ownership_confirmed_at = now_utc()
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), app_container.knowledge_store, tmp_path)
    app = FastAPI()
    app.state.container = app_container
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/knowledge/sources")
        assert response.status_code == 200
        assert response.json()["items"][0]["source_url"] == SOURCE_URL
        assert "body" not in response.json()["items"][0]
        detail = await client.get(f"/api/v1/knowledge/sources/{SOURCE['source_id']}")
        assert PDF_TEXT.strip() in detail.json()["body"]
        assert (await client.get("/api/v1/knowledge/sources/missing")).status_code == 404
        regulations = await client.get("/api/v1/residents/R001/regulations", headers={"X-Demo-Role": "resident"})
        assert SOURCE["source_id"] in {document["source_id"] for document in regulations.json()["items"]}


def test_public_tool_includes_page_and_legal_status(app_container, tmp_path):
    crawl_data.crawl_source(SOURCE, FixtureFetcher(), app_container.knowledge_store, tmp_path)
    result = app_container.tool_service.search_regulations("R001", "public residency", "residency")
    assert result["sources"][0]["source_url"] == SOURCE_URL
    assert "trang 1" in result["sources"][0]["section"]
    assert result["requires_human"] is True
    assert "không phải nội quy" in result["source_notice"]


def test_default_sources_are_official_and_unique():
    sources = json.loads((crawl_data.ROOT / "scripts" / "public_sources.json").read_text(encoding="utf-8"))
    assert len({source["source_id"] for source in sources}) == len(sources)
    for source in sources:
        crawl_data.validate_url(source["source_url"])
