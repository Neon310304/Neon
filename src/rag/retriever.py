import math
import re
from collections import Counter
from pathlib import Path
from threading import RLock
from typing import Any

from src.services.procedure_knowledge import contains_phrase, fold_text

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SEARCH_STOPWORDS = {
    "toi", "ban", "co", "duoc", "khong", "la", "ve", "cho", "cua", "va",
    "nhung", "nhu", "the", "nao", "gi", "hay", "can", "biet", "hoi", "xin",
    "trong", "khu", "dan", "cu", "quy", "dinh", "noi", "quy",
    "thu", "tuc", "dang", "ky", "huong", "dan", "cac", "lien", "quan", "tu",
    "cung", "cap", "thong", "tin", "bao", "nhieu", "theo", "luat", "nghi",
}

# A coherent subject must occur in the passage, not just loose syllables
# scattered through an unrelated legal document or its title.
TOPIC_ANCHORS = (
    (("xe may", "xe mo to"), ("xe may", "xe mo to")),
    (("o to", "xe hoi"), ("o to", "xe hoi")),
    (("dang ky xe", "gui xe", "phuong tien"), ("xe may", "xe mo to", "o to", "xe hoi", "gui xe", "phuong tien", "bai do xe", "bien so")),
    (("cho thue", "thue nha", "nguoi thue"), ("cho thue", "thue nha", "ben thue", "nguoi thue")),
    (("the cu dan", "resident card"), ("the cu dan", "resident card")),
    (("tam tru",), ("tam tru",)),
    (("thuong tru",), ("thuong tru",)),
    (("luu tru",), ("luu tru",)),
    (("cu tru",), ("tam tru", "thuong tru", "cu tru", "luu tru")),
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
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    output: list[str] = []
    start = 0
    while start < len(body):
        end = min(start + max_chars, len(body))
        if end < len(body):
            window = body[start:end]
            for pattern in (r"\n\s*\n", r"[.!?;](?:\s+)", r"\s+"):
                boundaries = [match.end() for match in re.finditer(pattern, window)
                              if match.end() >= max_chars // 2]
                if boundaries:
                    end = start + boundaries[-1]
                    break
        output.append(body[start:end])
        start = end
    return output


class RegulationRetriever:
    def __init__(self, knowledge_dir: str, persist_dir: str,
                 knowledge_store: Any = None, include_demo: bool = True):
        self.knowledge_dir = Path(knowledge_dir)
        self.persist_dir = persist_dir
        self.knowledge_store = knowledge_store
        self.include_demo = include_demo
        self._records: list[dict[str, Any]] = []
        self._lock = RLock()
        self._initialized = False
        self._database_revision: tuple = ()
        self._lexical_key: tuple = ()
        self._lexical: list[tuple] = []

    def initialize(self) -> None:
        with self._lock:
            try:
                self._initialize()
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            self._records = []
            self._initialized = False
            self._lexical_key = ()
            self._lexical = []

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

    def search(self, query: str, site_id: str, category: str | None = None, limit: int = 8, *,
               source_type: str | None = None, legal_status: str | None = None,
               site_scope: str = "all") -> list[dict[str, Any]]:
        if limit <= 0 or site_scope not in {"all", "site", "national"}:
            return []
        with self._lock:
            revision = self.knowledge_store.revision() if self.knowledge_store else ()
            if (not self._records and not self._initialized) or revision != self._database_revision:
                self.initialize()
            records = [dict(record) for record in self._records]
            lexical_key = tuple((record["text"], record["title"], record["section"]) for record in records)
            if lexical_key != self._lexical_key:
                self._lexical = []
                for text, title, section in lexical_key:
                    body = normalize(text)
                    body_counts = Counter(TOKEN_RE.findall(body))
                    title_counts = Counter(TOKEN_RE.findall(normalize(f"{title} {section}")))
                    self._lexical.append((body, body_counts, title_counts))
                self._lexical_key = lexical_key
            lexical = tuple(self._lexical)
        allowed_sites = {site_id, "national"} if site_scope == "all" else {site_id} if site_scope == "site" else {"national"}
        candidates = [(record, features) for record, features in zip(records, lexical, strict=True)
                      if record.get("site_id") in allowed_sites
                      and record.get("active") is not False
                      and (not category or record.get("category") == category)
                      and (source_type is None or record.get("source_type") == source_type)
                      and (legal_status is None or record.get("legal_status") == legal_status)]
        if not candidates:
            return []
        normalized_query = re.sub(r"\b(?:phap luat|the law|by law)\b", "luat", normalize(query))
        if any(contains_phrase(normalized_query, phrase) for phrase in ("gio yen tinh", "quiet hours")):
            normalized_query = re.sub(r"\bquiet hours\b", "gio yen tinh", normalized_query)
            normalized_query = re.sub(
                r"\b(?:bat dau (?:luc nao|khi nao|tu may gio)|start at(?: what time)?|what time do|when do)\b",
                "", normalized_query,
            )
        normalized_query = re.sub(r"\bmay gio(?= yen tinh\b)", "gio", normalized_query)
        normalized_query = re.sub(r"\b(?:o dau|luc nao|khi nao|(?:tu )?may gio)\b", "", normalized_query)
        terms = {term for term in TOKEN_RE.findall(normalized_query) if len(term) > 1 and term not in SEARCH_STOPWORDS}
        broad_rules = not terms and any(phrase in normalized_query for phrase in ("noi quy", "quy dinh"))
        anchors = []
        residual_terms = set(terms)
        expanded_terms = set(terms)
        for triggers, synonyms in TOPIC_ANCHORS:
            matched_triggers = [phrase for phrase in triggers if contains_phrase(normalized_query, phrase)]
            if matched_triggers:
                anchors.append(synonyms)
                residual_terms.difference_update(word for phrase in matched_triggers for word in phrase.split())
                expanded_terms.update(word for phrase in synonyms for word in phrase.split()
                                      if len(word) > 1 and word not in SEARCH_STOPWORDS)
        query_tokens = TOKEN_RE.findall(normalized_query)
        phrases = {" ".join(query_tokens[index:index + 2]) for index in range(len(query_tokens) - 1)
                   if any(word not in SEARCH_STOPWORDS for word in query_tokens[index:index + 2])}
        document_frequency = Counter()
        lengths = []
        for _, (_, body_counts, title_counts) in candidates:
            document_frequency.update(body_counts.keys() | title_counts.keys())
            lengths.append(sum(body_counts.values()))
        average_length = sum(lengths) / len(lengths) or 1.0
        rows = []
        for (record, (body, body_counts, title_counts)), length in zip(candidates, lengths, strict=True):
            body_terms = body_counts.keys()
            title_terms = title_counts.keys()
            matched_body = terms & body_terms
            matched_title = terms & title_terms
            coherent_subject = bool(anchors) and all(any(contains_phrase(body, phrase) for phrase in group) for group in anchors)
            if "muc luc" in body and "muc luc" not in normalized_query:
                continue
            if anchors and not coherent_subject:
                continue
            required_terms = residual_terms if coherent_subject else terms
            if required_terms and not broad_rules and (len(required_terms & body_terms) < min(2, len(required_terms))
                                    or len(required_terms & (body_terms | title_terms)) / len(required_terms) < 0.4):
                continue
            if not (matched_body or matched_title or coherent_subject or (broad_rules and record["category"] == "regulations")):
                continue
            score = 0.0
            for term in sorted(expanded_terms):
                frequency = body_counts[term] + 0.35 * title_counts[term]
                if not frequency:
                    continue
                inverse_frequency = math.log1p((len(candidates) - document_frequency[term] + 0.5)
                                               / (document_frequency[term] + 0.5))
                score += inverse_frequency * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * length / average_length)) * (1.0 if term in terms else 0.35)
            score += 0.5 * sum(contains_phrase(body, phrase) for phrase in sorted(phrases))
            if coherent_subject:
                score += 1
            if broad_rules and record["category"] == "regulations":
                score += 1
            rows.append({**record, "score": round(score, 4)})
        return sorted(rows, key=lambda row: (-row["score"], str(row.get("source_id", "")),
                                            str(row.get("id", "")), str(row.get("section", "")),
                                            row["text"]))[:limit if broad_rules else min(limit, 4)]
