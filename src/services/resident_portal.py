import hashlib
import json


def document_revision(document: dict) -> str:
    content = {key: document.get(key, "") for key in ("source_id", "title", "version", "body", "source_url", "content_sha256")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def reading_documents(store, resident_id: str, documents: list[dict]) -> list[dict]:
    receipts = {(item["source_id"], item["revision"]): item for item in store.document_acknowledgements(resident_id)}
    result = []
    for document in documents:
        revision = document_revision(document)
        receipt = receipts.get((document["source_id"], revision))
        result.append({**document, "revision": revision, "acknowledged_at": receipt["acknowledged_at"] if receipt else None})
    return result
