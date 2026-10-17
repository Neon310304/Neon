import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, delete, func, inspect, select
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .store import Base, Store, now_utc


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(String(500))
    document_number: Mapped[str] = mapped_column(String(80))
    category: Mapped[str] = mapped_column(String(50), index=True)
    publisher: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(40), default="official_public_document")
    legal_status: Mapped[str] = mapped_column(String(40), default="not_verified")
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    provenance_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    pdf_url: Mapped[str] = mapped_column(Text)
    pdf_part: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    text_sha256: Mapped[str] = mapped_column(String(64))


class KnowledgeStore:
    def __init__(self, store: Store):
        self.store = store

    def create_tables(self) -> None:
        existing = set(inspect(self.store.engine).get_table_names())
        if not {"knowledge_documents", "knowledge_chunks"}.issubset(existing):
            raise RuntimeError("public_knowledge_migration_required")

    def upsert_document(self, values: dict[str, Any], chunks: list[dict[str, Any]]) -> bool:
        if not chunks or not values["content"].strip():
            raise ValueError("Refusing to save a document without extracted content")
        with self.store.session() as session:
            document = session.get(KnowledgeDocument, values["id"])
            count = session.scalar(
                select(func.count()).select_from(KnowledgeChunk).where(
                    KnowledgeChunk.document_id == values["id"]
                )
            )
            changed = (document is None or document.content_sha256 != values["content_sha256"]
                       or count != len(chunks)
                       or json.loads(document.provenance_json)["files"] !=
                       json.loads(values["provenance_json"])["files"])
            if document is None:
                document = KnowledgeDocument(id=values["id"])
                session.add(document)
            for field in ("source_url", "title", "document_number", "category", "publisher",
                          "content", "content_sha256", "provenance_json"):
                setattr(document, field, values[field])
            document.fetched_at = now_utc()
            document.active = True
            session.flush()
            if changed:
                session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
                for ordinal, chunk in enumerate(chunks):
                    session.add(KnowledgeChunk(
                        id=f"{document.id}:{ordinal}", document_id=document.id, ordinal=ordinal,
                        pdf_url=chunk["pdf_url"], pdf_part=chunk["pdf_part"],
                        page_number=chunk["page_number"], text=chunk["text"],
                        text_sha256=hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
                    ))
            return changed

    @staticmethod
    def metadata(document: KnowledgeDocument) -> dict[str, Any]:
        provenance = json.loads(document.provenance_json)
        return {
            "source_id": document.id, "title": document.title, "version": document.document_number,
            "document_number": document.document_number, "source_url": document.source_url,
            "publisher": document.publisher, "source_type": document.source_type,
            "legal_status": document.legal_status, "content_sha256": document.content_sha256,
            "fetched_at": document.fetched_at.isoformat(), "category": document.category,
            "site_id": "national", "site": "Pháp luật Việt Nam — không phải nội quy riêng của khu",
            "effective_date": "", "section": document.document_number, "active": document.active,
            "page_count": sum(part["page_count"] for part in provenance["files"]),
            "provenance": provenance,
        }

    def list_documents(self, include_body: bool = False) -> list[dict[str, Any]]:
        with self.store.session() as session:
            documents = session.scalars(select(KnowledgeDocument).where(
                KnowledgeDocument.active.is_(True)
            ).order_by(KnowledgeDocument.id))
            return [{**self.metadata(document), **({"body": document.content} if include_body else {})}
                    for document in documents]

    def list_chunks(self) -> list[dict[str, Any]]:
        with self.store.session() as session:
            metadata = {document.id: self.metadata(document) for document in session.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))
            )}
            rows = session.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.document_id.in_(metadata)
            ).order_by(
                KnowledgeChunk.document_id, KnowledgeChunk.ordinal
            ))
            return [{
                **metadata[chunk.document_id], "id": chunk.id, "text": chunk.text,
                "download_url": chunk.pdf_url, "pdf_part": chunk.pdf_part,
                "page_number": chunk.page_number,
                "section": f"{metadata[chunk.document_id]['document_number']} — PDF {chunk.pdf_part}, trang {chunk.page_number}",
            } for chunk in rows]

    def revision(self) -> tuple:
        with self.store.session() as session:
            return tuple(session.execute(select(
                KnowledgeDocument.id, KnowledgeDocument.content_sha256, KnowledgeDocument.fetched_at
            ).where(KnowledgeDocument.active.is_(True)).order_by(KnowledgeDocument.id)).all())
