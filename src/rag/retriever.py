import hashlib
import math
import re
from pathlib import Path
from threading import RLock
from typing import Any

from src.services.procedure_knowledge import contains_phrase, fold_text

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SEARCH_STOPWORDS = {
    "toi", "ban", "co", "duoc", "khong", "la", "ve", "cho", "cua", "va",
    "nhung", "nhu", "the", "nao", "gi", "hay", "can", "biet", "hoi", "xin",
    "trong", "khu", "dan", "cu", "quy", "dinh", "noi", "quy",
    "thu", "tuc", "dang", "ky", "huong", "dan", "cac", "lien", "quan",
    "cung", "cap", "thong", "tin", "bao", "nhieu", "theo", "luat", "nghi",
}

# A coherent subject must occur in the passage, not just loose syllables
# scattered through an unrelated legal document or its title.
TOPIC_ANCHORS = (
    (("xe may", "o to", "dang ky xe", "gui xe", "phuong tien"), ("xe may", "o to", "gui xe", "phuong tien", "bai do xe", "bien so")),
    (("cho thue", "thue nha", "nguoi thue"), ("cho thue", "thue nha", "ben thue", "nguoi thue")),
    (("the cu dan", "resident card"), ("the cu dan", "resident card")),
    (("tam tru", "cu tru"), ("tam tru", "cu tru", "luu tru")),
    (("khach", "qua dem"), ("khach", "qua dem", "luu tru")),
    (("cho canh", "cho meo", "thu cung", "con cho", "con meo", "vat nuoi"), ("vat nuoi", "thu cung", "cho meo")),
    (("gio yen tinh", "on ao", "lam on", "tieng on"), ("yen tinh", "tieng on", "on ao")),
    (("hanh lang", "giay dep"), ("hanh lang", "giay dep")),
    (("internet", "wifi"), ("internet", "wifi", "vien thong")),
    (("chuyen do", "thang hang"), ("chuyen do", "thang hang", "van chuyen")),
    (("ho boi", "be boi"), ("ho boi", "be boi")),
)


def normalize(text: str) -> str:
    return fold_text(text)


def embed(text: str, dimensions: int = 384) -> list[float]:
    """Create a deterministic local feature-hashing embedding for Chroma."""
    vector = [0.0] * dimensions
    for token in TOKEN_RE.findall(normalize(text)):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def parse_document(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n"):
        raise ValueError(f"Missing front matter: {path}")
    front, body = raw[4:].split("\n---\n", 1)
    metadata: dict[str, Any] = {}
    for line in front.splitlines():
        key, value = line.split(":", 1)
        parsed = value.strip().strip('"')
        metadata[key.strip()] = parsed.lower() == "true" if parsed.lower() in {"true", "false"} else parsed
    return metadata, body.strip()


def chunks(body: str, max_chars: int = 900) -> list[str]:
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    output: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 2 > max_chars:
            output.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        output.append(current)
    return output


class RegulationRetriever:
    def __init__(self, knowledge_dir: str, persist_dir: str,
                 knowledge_store: Any = None, include_demo: bool = True):
        self.knowledge_dir = Path(knowledge_dir)
        self.persist_dir = persist_dir
        self.knowledge_store = knowledge_store
        self.include_demo = include_demo
        self._records: list[dict[str, Any]] = []
        self._collection = None
        self._client = None
        self._lock = RLock()
        self._initialized = False
        self._database_revision: tuple = ()

    def initialize(self) -> None:
        with self._lock:
            try:
                self._initialize()
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            self._collection = None
            self._records = []
            self._initialized = False
            client, self._client = self._client, None
            if client is not None:
                client.close()

    def _initialize(self) -> None:
        self._records = []
        self._database_revision = self.knowledge_store.revision() if self.knowledge_store else ()
        local_paths = sorted(self.knowledge_dir.glob("*.md")) if self.include_demo else []
        for path in local_paths:
            metadata, body = parse_document(path)
            if not metadata.get("active", False):
                continue
            metadata.setdefault("source_type", "demo_bql")
            for index, chunk in enumerate(chunks(body)):
                self._records.append({"id": f"{metadata['source_id']}:{index}", "text": chunk, **metadata})
        if self.knowledge_store:
            self._records.extend(self.knowledge_store.list_chunks())
        ids = [record["id"] for record in self._records]
        documents = [record["text"] for record in self._records]
        metadatas = [{key: value for key, value in record.items()
                      if key not in {"text", "id"} and isinstance(value, (str, int, float, bool))}
                     for record in self._records]
        embeddings = [embed(record["text"]) for record in self._records]
        try:
            import chromadb

            if self._client is None:
                self._client = chromadb.PersistentClient(path=self.persist_dir)
            self._collection = self._client.get_or_create_collection(
                "moveinmate_regulations" if self.include_demo else "moveinmate_public_regulations",
                metadata={"hnsw:space": "cosine"},
            )
            stale_ids = set(self._collection.get(include=[])["ids"]) - set(ids)
            if stale_ids:
                self._collection.delete(ids=sorted(stale_ids))
            if ids:
                self._collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
        except ImportError:
            self._collection = None
        self._initialized = True

    def list_documents(self, site_id: str) -> list[dict[str, Any]]:
        documents = self.knowledge_store.list_documents(include_body=True) if self.knowledge_store else []
        local_paths = sorted(self.knowledge_dir.glob("*.md")) if self.include_demo else []
        for path in local_paths:
            metadata, body = parse_document(path)
            if metadata.get("active") and metadata.get("site_id") == site_id:
                metadata.setdefault("source_type", "demo_bql")
                documents.append({**metadata, "body": body})
        return documents

    def search(self, query: str, site_id: str, category: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        revision = self.knowledge_store.revision() if self.knowledge_store else ()
        if (not self._records and not self._initialized) or revision != self._database_revision:
            self.initialize()
        normalized_query = normalize(query)
        terms = {term for term in TOKEN_RE.findall(normalized_query) if len(term) > 1 and term not in SEARCH_STOPWORDS}
        if any(word in normalized_query for word in ("cho canh", "cho meo", "thu cung", "con cho", "con meo")):
            terms.update({"vat", "nuoi"})
        if "on ao" in normalized_query or "lam on" in normalized_query:
            terms.update({"tieng", "yen", "tinh"})
        broad_rules = not terms and any(phrase in normalized_query for phrase in ("noi quy", "quy dinh"))
        anchors = [synonyms for triggers, synonyms in TOPIC_ANCHORS
                   if any(contains_phrase(normalized_query, phrase) for phrase in triggers)]
        rows = []
        for record in self._records:
            if record["site_id"] not in {site_id, "national"} or (category and record["category"] != category):
                continue
            body = normalize(record["text"])
            body_terms = set(TOKEN_RE.findall(body))
            title_terms = set(TOKEN_RE.findall(normalize(f"{record['title']} {record['section']}")))
            matched_body = terms & body_terms
            matched_title = terms & title_terms
            coherent_subject = any(contains_phrase(body, phrase) for group in anchors for phrase in group)
            if "muc luc" in body and "muc luc" not in normalized_query:
                continue
            if anchors and not coherent_subject:
                continue
            if terms and not broad_rules and (len(matched_body) < min(2, len(terms))
                                    or len(matched_body | matched_title) / max(1, len(terms)) < 0.4):
                continue
            if not (matched_body or matched_title or coherent_subject or (broad_rules and record["category"] == "regulations")):
                continue
            score = (len(matched_body) + 2 * len(matched_title)) / max(1, len(terms))
            if coherent_subject:
                score += 1
            if broad_rules and record["category"] == "regulations":
                score += 1
            rows.append({**record, "score": round(score, 4)})
        return sorted(rows, key=lambda row: row["score"], reverse=True)[:limit if broad_rules else min(limit, 4)]
