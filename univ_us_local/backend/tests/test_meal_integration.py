"""CA1 화면을 앱 세션 안에서 열고, 시간표와 입력 초안을 올바른 저장소에 연결한다."""
import importlib
import io
import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def desktop_app(tmp_path_factory):
    root = tmp_path_factory.mktemp("meal-app")
    sys.path.insert(0, str(REPO / "C0_Platform_agent"))
    sys.path.insert(0, str(REPO / "univ_us_local" / "backend"))
    from osenv import appdata

    patch = pytest.MonkeyPatch()
    env = {}
    appdata.apply(root, env)
    for key, value in env.items():
        patch.setenv(key, value)
    for key in ("F1_TASKS", "F6_TASKS", "F11_AUTOSYNC", "F12_AUTOSYNC"):
        patch.setenv(key, "off")
    patch.setenv("UNIVUS_SESSION_TOKEN", "meal-integration-test")
    patch.setenv("MEAL_PUBLIC", "0")
    patch.delenv("CA1_ENTRIES_DIR", raising=False)
    main = importlib.import_module("app.main")
    yield main, root
    patch.undo()


@pytest.fixture
def client(desktop_app):
    main, _ = desktop_app
    # No lifespan: these endpoints must not create any academic DB to render the meal UI.
    client = TestClient(main.app, base_url="http://127.0.0.1:8020")
    client.cookies.set(main.C.SESSION_COOKIE, "meal-integration-test")
    yield client
    client.close()


class _Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.base = None
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "base":
            self.base = attrs["href"]
        if tag == "script" and attrs.get("src"):
            self.resources.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.resources.append(attrs["href"])


def test_meal_ui_and_assets_share_the_desktop_session(desktop_app, client):
    main, _ = desktop_app
    guest = TestClient(main.app, base_url="http://127.0.0.1:8020")
    try:
        for path in ("/api/meal/", "/api/meal/assets/meal-form.js", "/api/meal/v1/meal/places",
                     "/api/meal/v1/meal/timeline?date=2026-11-16", "/api/meal/v1/meal/basemap"):
            assert guest.get(path).status_code == 403
    finally:
        guest.close()
    response = client.get("/api/meal/")
    assert response.status_code == 200
    assert "공강 식사 추천" in response.text and "식당 둘러보기" in response.text
    page = _Resources()
    page.feed(response.text)
    assert page.base == "/api/meal/"
    assert len(page.resources) >= 8
    for resource in page.resources:
        path = urljoin(page.base, resource)
        assert path.startswith("/api/meal/assets/")
        assert client.get(path).status_code == 200
    assert client.get("/api/meal/assets/PretendardVariable.woff2").status_code == 200
    assert client.get("/api/meal/v1/buildings").json()["buildings"]
    assert response.headers["cache-control"] == "no-store"


def test_recommendation_uses_the_f3_exporter_without_a_cli(desktop_app, client, monkeypatch):
    from attendance import meal_context
    from meal import academic, service

    def no_cli(*args):
        pytest.fail("embedded meal must not start desktop/cli.py")

    def exported(day):
        return {"version": 1, "date": day.isoformat(),
                "semester": {"id": "2026-2", "start": "2026-09-01", "end": "2026-12-20"},
                "blocks": [{"kind": "class", "start": "11:00", "end": "11:50", "building": "공7-223", "state": "scheduled"},
                           {"kind": "class", "start": "13:30", "end": "14:45", "building": "경영1-101", "state": "scheduled"}],
                "status": "ready", "warnings": [], "updatedAt": None,
                "courseCount": 2, "missingTimetableCount": 0}

    monkeypatch.setattr(academic, "_run_cli", no_cli)
    monkeypatch.setattr(meal_context, "export_context", exported)
    monkeypatch.setattr(service, "load_places", lambda **kwargs: [])
    response = client.get("/api/meal/v1/meal/timetable?date=2026-11-11")
    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert response.json()["dataMode"] == "app"
    assert "blocks" not in response.json()
    timeline = client.get("/api/meal/v1/meal/timeline?date=2026-11-11")
    assert timeline.status_code == 200
    assert timeline.json()["slots"][0]["start"] == "11:50"
    assert timeline.json()["slots"][0]["end"] == "13:30"
    response = client.post("/api/meal/v1/meal/recommend", headers={"Origin": "http://127.0.0.1:3000"},
                           json={"at": "2026-11-11T11:50:00+09:00", "routeMode": "estimate"})
    assert response.status_code == 200, response.text
    assert response.json()["context"]["origin"]["code"] == "공7"
    assert response.json()["context"]["destination"]["code"] == "경영1"
    assert client.post("/api/meal/v1/meal/recommend", headers={"Origin": "https://example.com"}, json={}).status_code == 403


def test_survey_drafts_stay_in_app_data_and_public_seeds_do_not_change(desktop_app, client):
    _, root = desktop_app
    from meal import survey

    seed = REPO / "campus/CA1_Meal_agent/data/places/p_0013.json"
    original = seed.read_bytes()
    entries = root / "CA1_Meal_agent/data/survey/user_entries"
    response = client.post("/api/meal/v1/meal/survey/p_0013", json={"expectedRevision": 0, "notes": "synthetic integration draft"})
    assert response.status_code == 200, response.text
    assert (entries / "p_0013.json").is_file()
    assert seed.read_bytes() == original
    assert survey.ENTRIES_DIR != entries
    assert client.get("/api/meal/v1/meal/survey/p_0013").json()["draft"]["revision"] == 1
    assert client.post("/api/meal/v1/meal/survey/p_0013", json={"expectedRevision": 0}).status_code == 409
    page = client.get("/api/meal/survey")
    assert '<base href="/api/meal/">' in page.text


def test_responses_schedule_and_assignments_through_desktop_api(desktop_app, client, monkeypatch):
    """가짜 Responses 응답으로 실제 일정·과제 도구와 성적 제외를 검증한다."""
    from app import assistant as bridge
    from assistant import llm, settings as S

    monkeypatch.setattr(S, "slot", lambda: S.Slot("https://api.openai.com/v1", "fake-key", "gpt-6.1-sol", "test"))
    lookups = []

    def academic_api(path, **params):
        lookups.append(path)
        if path == "/api/events":
            return [{"title": "중간 발표", "start": "2026-11-19T11:00:00", "grade": "private-grade"}]
        assert path == "/api/priority"
        return {"items": [{"title": "보고서 제출", "due": "2026-11-20T23:59:00", "score": "private-score"}],
                "counts": {"pending": 1}}

    monkeypatch.setattr(bridge, "_api", academic_api)
    calls = [{"type": "function_call", "id": "fc_" + name, "call_id": "call_" + name,
              "name": name, "arguments": "{}", "status": "completed"}
             for name in ("get_schedule", "get_assignments")]
    answer = "11월 19일 11시에 중간 발표, 20일 23:59까지 보고서 제출입니다."
    requests = []

    def fake_post(url, key, body, timeout):
        assert url == "https://api.openai.com/v1/responses" and key == "fake-key"
        requests.append(body)
        if len(requests) == 1:
            events = [{"type": "response.completed", "response": {"status": "completed", "output": calls}}]
        else:
            assert len(requests) == 2
            events = [{"type": "response.output_text.delta", "delta": answer},
                      {"type": "response.completed", "response": {"status": "completed", "output": [
                          {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": answer}]}]}}]
        return io.BytesIO("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())

    monkeypatch.setattr(llm, "_post", fake_post)
    response = client.post("/api/assistant/v1/agent/chat", json={"messages": [
        {"role": "user", "content": "다가오는 일정과 과제 알려줘"}]})
    assert response.status_code == 200 and "event: error" not in response.text, response.text
    assert answer in response.text and "event: done" in response.text
    assert lookups == ["/api/events", "/api/priority"]
    outputs = [i for i in requests[1]["input"] if i.get("type") == "function_call_output"]
    assert [i["call_id"] for i in outputs] == ["call_get_schedule", "call_get_assignments"]
    assert "중간 발표" in outputs[0]["output"] and "보고서 제출" in outputs[1]["output"]
    assert "private-grade" not in json.dumps(requests) and "private-score" not in json.dumps(requests)
    assert '"href": "/assignments"' in response.text
