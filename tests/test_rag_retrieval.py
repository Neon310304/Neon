import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from src.rag.retriever import RegulationRetriever, chunks


def record(source_id, text="Nội quy hồ bơi dành cho cư dân.", **metadata):
    return {
        "id": f"{source_id}:0", "source_id": source_id, "text": text,
        "title": "Tài liệu tổng hợp giả lập", "section": "Mục 1", "site_id": "site-a",
        "category": "regulations", "source_type": "building_policy", "legal_status": "verified",
        **metadata,
    }


def retriever_with(tmp_path, records):
    retriever = RegulationRetriever(str(tmp_path), str(tmp_path), include_demo=False)
    retriever._records = records
    retriever._initialized = True
    return retriever


@pytest.mark.parametrize("filters,expected", [
    ({}, ["national", "site"]),
    ({"site_scope": "site"}, ["site"]),
    ({"site_scope": "national"}, ["national"]),
    ({"source_type": "building_policy"}, ["site"]),
    ({"legal_status": "verified"}, ["site"]),
    ({"site_scope": "national", "legal_status": "verified"}, []),
    ({"source_type": "missing"}, []),
    ({"site_scope": "invalid"}, []),
    ({"site_scope": "SITE"}, []),
])
def test_scope_and_metadata_filters(tmp_path, filters, expected):
    retriever = retriever_with(tmp_path, [
        record("site"),
        record("national", site_id="national", source_type="official_public_document", legal_status="not_verified"),
        record("other", site_id="site-b"),
        record("inactive", active=False),
        record("category", category="fire_safety"),
    ])
    results = retriever.search("hồ bơi", "site-a", "regulations", **filters)
    assert [row["source_id"] for row in results] == expected
    assert all(row["legal_status"] == ("not_verified" if row["source_id"] == "national" else "verified") for row in results)


@pytest.mark.parametrize("filters,noise", [
    ({"site_scope": "site"}, {"site_id": "national"}),
    ({"site_scope": "national"}, {"site_id": "site-a"}),
    ({"source_type": "building_policy"}, {"source_type": "demo_bql"}),
    ({"legal_status": "verified"}, {"legal_status": "not_verified"}),
    ({}, {"active": False}),
])
def test_disallowed_sources_cannot_crowd_topk_or_change_scores(tmp_path, filters, noise):
    target = record("target", site_id="national" if filters.get("site_scope") == "national" else "site-a")
    retriever = retriever_with(tmp_path, [target])
    baseline = retriever.search("hồ bơi", "site-a", limit=1, **filters)
    retriever._records = [record(f"noise-{index}", "Hồ bơi hồ bơi hồ bơi.", title="Hồ bơi", **noise) for index in range(8)] + [target]
    assert retriever.search("hồ bơi", "site-a", limit=1, **filters) == baseline
    assert baseline[0]["source_id"] == "target"


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_does_not_initialize(tmp_path, limit):
    retriever = RegulationRetriever(str(tmp_path), str(tmp_path))
    retriever.initialize = lambda: pytest.fail("An empty search must not initialize Chroma")
    assert retriever.search("hồ bơi", "site-a", limit=limit) == []


def test_phrase_relevance_beats_scattered_words_and_title(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("scattered", "Bảo trì hệ thống. Kiểm tra định kỳ.", title="Bảo trì định kỳ"),
        record("phrase", "Bảo trì định kỳ hệ thống. Kiểm tra."),
    ])
    assert retriever.search("bảo trì định kỳ", "site-a")[0]["source_id"] == "phrase"


@pytest.mark.parametrize("query,text", [
    ("pháp luật về cho thuê nhà", "Quyền và nghĩa vụ của bên cho thuê nhà và bên thuê nhà."),
    ("phap luat ve cho thue nha", "Quyền và nghĩa vụ của bên cho thuê nhà và bên thuê nhà."),
    ("Theo luật nhà ở, cho thuê nhà được quy định thế nào?", "Quyền và nghĩa vụ của bên cho thuê nhà và bên thuê nhà."),
    ("the law: rental housing", "Rental housing requirements."),
    ("by law: rental housing", "Rental housing requirements."),
])
def test_legal_lead_in_does_not_require_legal_words_in_passage(tmp_path, query, text):
    retriever = retriever_with(tmp_path, [
        record("law", text, site_id="national", category="housing",
               source_type="official_public_document", legal_status="not_verified"),
        record("irrelevant", "Nội quy hồ bơi.", site_id="national", category="housing"),
        record("other-site", text, site_id="site-b", category="housing"),
    ])
    results = retriever.search(query, "site-a", "housing", site_scope="national")
    assert [row["source_id"] for row in results] == ["law"]
    assert results[0]["legal_status"] == "not_verified"


@pytest.mark.parametrize("lead_in", ["pháp luật về", "the law:", "by law:"])
def test_legal_lead_in_preserves_phrase_ranking(tmp_path, lead_in):
    retriever = retriever_with(tmp_path, [
        record("scattered", "Bảo trì hệ thống. Kiểm tra định kỳ.", title="Bảo trì định kỳ"),
        record("phrase", "Bảo trì định kỳ hệ thống. Kiểm tra."),
    ])
    assert retriever.search(f"{lead_in} bảo trì định kỳ", "site-a")[0]["source_id"] == "phrase"


def test_legal_lead_in_does_not_remove_unknown_subject_constraints(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("housing", "Quyền và nghĩa vụ của bên cho thuê nhà.", site_id="national", category="housing"),
    ])
    assert retriever.search("pháp luật về cho thuê nhà lượng tử", "site-a", "housing") == []


@pytest.mark.parametrize("query", [
    "Giờ yên tĩnh bắt đầu lúc nào?",
    "Gio yen tinh bat dau luc nao?",
    "Giờ yên tĩnh bắt đầu khi nào?",
    "Giờ yên tĩnh bắt đầu từ mấy giờ?",
    "Mấy giờ yên tĩnh?",
    "may gio yen tinh",
    "quiet hours start at",
    "What time do quiet hours start at?",
    "When do quiet hours start at?",
])
def test_quiet_hours_inquiries_find_same_synthetic_source(tmp_path, query):
    source = record("quiet", "Giờ yên tĩnh của khu là 22:00–06:00.")
    retriever = retriever_with(tmp_path, [source, record("pool", "Hồ bơi mở cửa từ 07:00.")])
    results = retriever.search(query, "site-a", site_scope="site")
    assert [row["source_id"] for row in results] == ["quiet"]
    assert results[0]["text"] == source["text"]


@pytest.mark.parametrize("query", [
    "Giờ yên tĩnh lượng tử bắt đầu lúc nào?",
    "Gio yen tinh luong tu bat dau luc nao?",
    "Giờ yên tĩnh hồ bơi bắt đầu lúc nào?",
    "quiet hours quantum start at",
    "Giờ yên tĩnh bắt đầu lúc 23:00?",
])
def test_quiet_hours_inquiry_preserves_subject_and_numeric_constraints(tmp_path, query):
    retriever = retriever_with(tmp_path, [record("quiet", "Giờ yên tĩnh của khu là 22:00–06:00.")])
    assert retriever.search(query, "site-a") == []


def test_quiet_hours_tool_uses_synthetic_metadata_without_database(tmp_path):
    from src.tools.agent_tools import ToolService

    source = record("quiet", "Giờ yên tĩnh của khu là 22:00–06:00.",
                    version="synthetic-1", site="Khu giả lập", effective_date="",
                    source_type="demo_bql", legal_status="")
    retriever = retriever_with(tmp_path, [source])
    service = ToolService(SimpleNamespace(get_resident=lambda _: {"site_id": "site-a"}), retriever)
    result = service.search_regulations("synthetic-resident", "Giờ yên tĩnh bắt đầu lúc nào?")
    assert result["answer_status"] == "supported"
    assert [item["source_id"] for item in result["sources"]] == ["quiet"]
    assert result["sources"][0]["excerpt"] == source["text"]
    assert result["data_partition"] == "demo_bql"


def test_bm25_prefers_focused_passage_over_long_distractor(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("long", "Máy bơm bảo trì. " + "Nội dung khác không liên quan. " * 100),
        record("focused", "Máy bơm bảo trì."),
    ])
    assert retriever.search("máy bơm bảo trì", "site-a")[0]["source_id"] == "focused"


def test_bm25_rare_term_distinguishes_otherwise_equal_passages(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("rare", "Máy bơm áp suất."),
        record("common", "Máy bơm vận hành."),
        *[record(f"background-{index}", "Máy bơm vận hành.") for index in range(8)],
    ])
    assert retriever.search("máy bơm áp suất vận hành", "site-a")[0]["source_id"] == "rare"


@pytest.mark.parametrize("query,text", [
    ("ho boi", "Nội quy bể bơi."),
    ("thu cung", "Quy định vật nuôi."),
    ("lam on", "Hướng dẫn xử lý tiếng ồn."),
    ("wifi", "Thông tin viễn thông."),
    ("thủ tục đăng kí xe máy", "Đăng ký xe mô tô và biển số."),
    ("resident card", "Thủ tục thẻ cư dân."),
])
def test_curated_aliases_match_only_coherent_body_topics(tmp_path, query, text):
    retriever = retriever_with(tmp_path, [record("match", text), record("wrong", "Máy chữa cháy được kiểm tra.")])
    assert [row["source_id"] for row in retriever.search(query, "site-a")] == ["match"]


@pytest.mark.parametrize("query,positive,negative", [
    ("đăng ký ô tô", "Đăng ký xe hơi.", "Đăng ký xe máy."),
    ("dang ky o to", "Đăng ký ô tô.", "Đăng ký xe máy."),
    ("đăng ký xe máy", "Đăng ký xe mô tô.", "Đăng ký ô tô."),
    ("xe hoi", "Ô tô.", "Xe máy."),
    ("tạm trú", "Đăng ký tạm trú.", "Thông báo lưu trú."),
    ("tam tru", "Đăng ký tạm trú.", "Đăng ký thường trú."),
    ("lưu trú", "Thông báo lưu trú.", "Đăng ký tạm trú."),
    ("thường trú", "Đăng ký thường trú.", "Thông báo lưu trú."),
])
def test_specific_subtype_requires_its_own_body_evidence(tmp_path, query, positive, negative):
    retriever = retriever_with(tmp_path, [
        record("positive", positive), record("negative", negative, title=query),
        record("generic", "Đăng ký phương tiện và thông tin cư trú.", title=query),
    ])
    assert [row["source_id"] for row in retriever.search(query, "site-a")] == ["positive"]


@pytest.mark.parametrize("query,passages", [
    ("phương tiện", ["Đăng ký ô tô.", "Đăng ký xe máy."]),
    ("đăng ký xe", ["Đăng ký xe hơi.", "Đăng ký xe mô tô."]),
    ("cư trú", ["Đăng ký tạm trú.", "Thông báo lưu trú.", "Đăng ký thường trú."]),
])
def test_broad_topics_can_retrieve_distinct_subtypes(tmp_path, query, passages):
    retriever = retriever_with(tmp_path, [record(str(index), text) for index, text in enumerate(passages)])
    assert {row["source_id"] for row in retriever.search(query, "site-a")} == {str(index) for index in range(len(passages))}


@pytest.mark.parametrize("query", ["zzqxv unknownquery", "thiên văn lượng tử", "", "tôi muốn biết"])
def test_unknown_queries_abstain(tmp_path, query):
    retriever = retriever_with(tmp_path, [record("pool")])
    assert retriever.search(query, "site-a") == []


def test_anchor_toc_and_category_safeguards(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("loose", "Đăng ký máy chữa cháy. Xe chữa cháy kiểm tra máy.", title="Xe máy"),
        record("toc", "MỤC LỤC. Xe máy .......... 12"),
        record("wrong-category", "Đăng ký xe máy.", category="fire_safety"),
    ])
    assert retriever.search("thủ tục đăng kí xe máy", "site-a", "regulations") == []


def test_multiple_topics_require_each_anchor(tmp_path):
    retriever = retriever_with(tmp_path, [
        record("partial", "Thông tin hồ bơi."),
        record("both", "Thông tin hồ bơi và vật nuôi."),
    ])
    assert [row["source_id"] for row in retriever.search("hồ bơi và thú cưng", "site-a")] == ["both"]


def test_ties_are_stable_across_record_order_and_searches(tmp_path):
    records = [record(source_id) for source_id in ("z", "a", "m")]
    retriever = retriever_with(tmp_path, records)
    expected = retriever.search("hồ bơi", "site-a")
    retriever._records = list(reversed(records))
    assert retriever.search("hồ bơi", "site-a") == expected
    assert [row["source_id"] for row in expected] == ["a", "m", "z"]


def test_cached_lexical_features_follow_text_and_metadata_changes(tmp_path):
    source = record("source")
    retriever = retriever_with(tmp_path, [source])
    assert retriever.search("hồ bơi", "site-a")
    cached = retriever._lexical
    assert retriever.search("hồ bơi", "site-a")
    assert retriever._lexical is cached
    source["active"] = False
    assert retriever.search("hồ bơi", "site-a") == []
    source.update(active=True, text="Quy định vật nuôi.")
    assert retriever.search("hồ bơi", "site-a") == []
    assert retriever.search("thú cưng", "site-a")


@pytest.mark.parametrize("text", [
    "", "Nội quy ngắn.", "Đoạn dài có dấu tiếng Việt. " * 150,
    "x" * 2501, "  Đoạn một.\n\nĐoạn hai!\n" * 150,
])
@pytest.mark.parametrize("max_chars", [1, 73, 900])
def test_chunks_are_hard_bounded_and_preserve_exact_text(text, max_chars):
    result = chunks(text, max_chars)
    assert "".join(result) == text
    assert all(0 < len(chunk) <= max_chars for chunk in result)


@pytest.mark.parametrize("max_chars", [0, -1])
def test_chunk_size_must_be_positive(max_chars):
    with pytest.raises(ValueError):
        chunks("Nội quy", max_chars)


def test_revision_refresh_and_concurrent_search_keep_database_snapshot(tmp_path, monkeypatch):
    def unexpected_client(*args, **kwargs):
        raise AssertionError("Unused vector index must not be opened")

    monkeypatch.setitem(sys.modules, "chromadb", SimpleNamespace(PersistentClient=unexpected_client))
    entered = Event()
    release = Event()

    class FakeStore:
        version = 1
        rows = [record("old")]

        def revision(self):
            return (self.version,)

        def list_chunks(self):
            if self.version == 2:
                entered.set()
                assert release.wait(5)
            return self.rows

    store = FakeStore()
    retriever = RegulationRetriever(str(tmp_path), str(tmp_path), store, include_demo=False)
    try:
        assert retriever.search("hồ bơi", "site-a")[0]["source_id"] == "old"
        store.version = 2
        store.rows = [record("new")]
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(retriever.search, "hồ bơi", "site-a")
            assert entered.wait(5)
            second = executor.submit(retriever.search, "hồ bơi", "site-a")
            release.set()
            assert first.result()[0]["source_id"] == "new"
            assert second.result()[0]["source_id"] == "new"
        assert {record["id"] for record in retriever._records} == {"new:0"}
        assert retriever._database_revision == (2,)
    finally:
        release.set()
        retriever.close()
    retriever.close()
    assert retriever._records == []
    assert retriever._initialized is False
