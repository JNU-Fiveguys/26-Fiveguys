"""강의자료 PDF 쪽 검색 · 전송 동의 — 손으로 만든 작은 PDF 로 (외부 접속 없음)."""
import json

import pytest

from assistant import agent, pdfsearch
from assistant import config as C
from assistant import settings as S


def make_pdf(path, pages):
    """글자만 있는 아주 작은 PDF (쪽마다 한 줄, 영문) — pypdf 가 읽을 수 있는 최소 구조."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)
    return path


@pytest.fixture
def lecture(tmp_path):
    p = make_pdf(tmp_path / "os-week5.pdf", ["Process scheduling overview", "Deadlock needs four conditions: mutual exclusion and hold and wait",
                                             "Paging and segmentation"])
    return [{"id": "mt:1", "title": "os-week5.pdf", "path": str(p), "pages": 3}]


def test_finds_the_page(lecture):
    res = pdfsearch.search(lecture, "deadlock conditions")
    assert res["files"] == 1 and res["pages"] == 3 and res["unread"] == []
    assert [(h["title"], h["page"]) for h in res["hits"]] == [("os-week5.pdf", 2)] and "mutual exclusion" in res["hits"][0]["text"]
    assert pdfsearch.search(lecture, "quantum chromodynamics")["hits"] == []


def test_cache_and_unreadable(lecture, tmp_path):
    pdfsearch.search(lecture, "paging")
    assert len(list((C.DATA_DIR / "pdfcache").glob("*.json"))) == 1          # 읽은 글자는 이 PC 의 캐시에만
    bad = tmp_path / "scan.pdf"
    bad.write_bytes(b"not a pdf")
    res = pdfsearch.search([{"id": "mt:2", "title": "scan.pdf", "path": str(bad), "pages": 1}], "paging")
    assert res["hits"] == [] and res["unread"] == ["scan.pdf"]


def test_too_many_pages(lecture, monkeypatch):
    monkeypatch.setattr(C, "MATERIAL_MAX_PAGES", 2)
    with pytest.raises(pdfsearch.TooMany):
        pdfsearch.search(lecture, "deadlock")


def test_consent_roundtrip_survives_clear():
    assert not S.consent() and not S.materials_allowed()
    S.save("custom", "https://a.test/v1", "m", "k-12345678")
    assert S.set_consent(True) and S.view()["materialsConsent"] and S.slot().model == "m"
    S.clear()
    assert S.consent() and S.slot().source == "none"                         # 연결을 지워도 동의는 따로다
    S.set_consent(False)
    S.save("ollama", "http://localhost:11434/v1", "local")
    assert S.materials_allowed()                                             # AI 가 내 PC 안이면 밖으로 나가지 않는다


def test_materials_tool_waits_for_consent(fake_llm, monkeypatch):
    calls = []
    monkeypatch.setattr(S, "is_local", lambda url: False)                    # 가짜 LLM 서버가 127.0.0.1 이라 — 바깥 AI 인 것처럼
    monkeypatch.setitem(agent._TOOLS, "read_notes", {"fn": lambda a, c: calls.append(a) or {"content": {"results": ["x"]}},
                                                     "sends_materials": True, "enabled": None, "spec": {"type": "function", "function": {"name": "read_notes"}}})
    ask = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_notes", "arguments": "{}"}}]}}]}
    say = {"choices": [{"delta": {"content": "동의가 필요해요."}}]}
    fake_llm.replies = [[ask, "[DONE]"], [say, "[DONE]"]]
    ev = list(agent.run([{"role": "user", "content": "운영체제 교착 상태 설명해줘"}]))
    assert any(k == "consent" for k, _ in ev) and calls == []                # 동의 전에는 실행하지 않는다
    assert "동의" in json.loads(fake_llm.requests[1]["body"]["messages"][-1]["content"])["error"]
    S.set_consent(True)
    fake_llm.replies = [[ask, "[DONE]"], [say, "[DONE]"]]
    ev = list(agent.run([{"role": "user", "content": "운영체제 교착 상태 설명해줘"}]))
    assert not any(k == "consent" for k, _ in ev) and calls == [{}]


def test_disabled_tool_is_hidden(monkeypatch):
    monkeypatch.setattr(C, "KNOWLEDGE_DIRS", ())
    assert "search_campus_info" not in [t["function"]["name"] for t in agent.specs()]
    assert agent._call({"function": {"name": "search_campus_info", "arguments": "{}"}}, {})[0]["error"]


def test_role_and_today_in_system_prompt(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(agent, "_role", "내 데이터 도우미")
    text = agent.system_prompt(datetime(2026, 11, 14, 9, 5))
    assert text.startswith("내 데이터 도우미") and "2026-11-14 토요일" in text and "09:05" in text
