"""CA1 화면을 앱 세션 안에서 열고, 시간표와 입력 초안을 올바른 저장소에 연결한다."""
import importlib
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
        for path in ("/api/meal/", "/api/meal/assets/meal-form.js", "/api/meal/v1/meal/places"):
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
