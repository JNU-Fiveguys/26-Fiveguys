"""Run with the desktop Python environment (Playwright/Chromium required).

Uses a synthetic F3 context, public restaurants and a temporary survey directory.
No installed app settings, API keys or personal timetable are loaded.
"""
import argparse
import json
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright, expect

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from meal.academic import AcademicSnapshot
from meal.api import create_embedded_app


def reader(day):
    return AcademicSnapshot({"version": 1, "date": day.isoformat(), "blocks": [
        {"kind": "class", "start": "09:00", "end": "11:50", "building": "공7", "state": "scheduled"},
        {"kind": "class", "start": "13:30", "end": "14:45", "building": "경영1", "state": "scheduled"},
    ]}, {"status": "ready", "date": day.isoformat(), "warnings": [], "dataMode": "test"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pr6-ui-") as temporary:
        app = FastAPI()
        app.mount("/api/meal", create_embedded_app(timetable_reader=reader, entries_dir=Path(temporary)))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level="error"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        checks, errors = {}, []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1050}, timezone_id="Asia/Seoul")
                page.set_default_timeout(20000)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/api/meal/", wait_until="networkidle")
                expect(page.locator(".timeline-gap")).to_have_count(2)
                with page.expect_response(lambda r: r.url.endswith("/v1/meal/recommend")) as submitted:
                    page.locator(".timeline-gap").first.click()
                assert submitted.value.status == 200
                payload = submitted.value.request.post_data_json
                assert "T11:50:00+09:00" in payload["at"] and payload["autoTimetable"] is True
                expect(page.locator("#cards .card")).to_have_count(3)
                expect(page.locator("#cards .card:visible")).to_have_count(1)
                expect(page.locator("#deck-count")).to_have_text("1 / 3")
                page.locator("#deck-next").click()
                expect(page.locator("#deck-count")).to_have_text("2 / 3")
                page.locator("#cards").focus()
                page.keyboard.press("ArrowLeft")
                expect(page.locator("#deck-count")).to_have_text("1 / 3")
                page.locator("#cards").dispatch_event("pointerdown", {"clientX": 500, "clientY": 300})
                page.locator("#cards").dispatch_event("pointerup", {"clientX": 300, "clientY": 310})
                expect(page.locator("#deck-count")).to_have_text("2 / 3")
                checks["timeline_submit_and_deck_buttons_keyboard_swipe"] = True

                # Team-supplied PR #6 geometry renders without online map tiles.
                tiles = []
                page.on("request", lambda request: tiles.append(request.url) if "tile.openstreetmap.org/" in request.url else None)
                page.route("https://tile.openstreetmap.org/**", lambda route: route.abort())
                expect(page.locator("#recommendation-map-panel")).to_have_attribute("data-state", "map")
                expect(page.locator("#recommendation-map .meal-star")).to_have_count(3)
                page.locator("#recommendation-map .meal-star").nth(2).click()
                expect(page.locator("#deck-count")).to_have_text("3 / 3")
                page.locator("#cards .card:visible").get_by_role("button", name="이동 경로 보기").click()
                expect(page.locator("#map-details")).to_be_visible()
                expect(page.locator("#map-details")).to_have_attribute("data-state", "map")
                page.screenshot(path=str(args.output / "map.png"))
                assert not tiles
                page.locator("#route-close").click()
                page.locator("#cards .card:visible").get_by_role("button", name="메뉴·영업시간", exact=True).click()
                expect(page.locator("#place-dialog")).to_be_visible()
                page.locator("#detail-close").click()
                checks["pr6_map_without_tiles_star_selection_and_place_details"] = True
                page.locator("#browse-tab").click()
                expect(page.locator("#meal-timeline")).not_to_be_visible()
                expect(page.locator("#catalog-items")).to_be_visible()
                page.locator("#recommend-tab").click()
                page.screenshot(path=str(args.output / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(page.locator("#cards .card:visible")).to_have_count(1)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
                page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
                checks["desktop_mobile_no_horizontal_overflow_and_browse"] = True

                # An older slow request must not overwrite a newly selected day.
                async_requests = {"first": None}
                def delay_old(route):
                    if "date=2026-11-16" in route.request.url:
                        async_requests["first"] = route
                    else:
                        route.fulfill(json={"status": "ready", "date": "2026-11-17", "classes": [], "slots": []})
                page.route("**/v1/meal/timeline?**", delay_old)
                page.evaluate("document.dispatchEvent(new CustomEvent('meal:datechange',{detail:{date:'2026-11-16',automatic:true}}))")
                for _ in range(100):
                    if async_requests["first"]:
                        break
                    page.wait_for_timeout(20)
                assert async_requests["first"]
                page.evaluate("document.dispatchEvent(new CustomEvent('meal:datechange',{detail:{date:'2026-11-17',automatic:true}}))")
                expect(page.locator("#timeline-status")).to_contain_text("2026-11-17")
                async_requests["first"].fulfill(json={"status": "ready", "date": "2026-11-16", "classes": [], "slots": []})
                page.wait_for_timeout(150)
                expect(page.locator("#timeline-status")).to_contain_text("2026-11-17")
                page.unroute("**/v1/meal/timeline?**", delay_old)
                page.route("**/v1/meal/timeline?**", lambda route: route.fulfill(status=500, json={"detail": "test error"}))
                page.evaluate("document.dispatchEvent(new CustomEvent('meal:datechange',{detail:{date:'2026-11-18',automatic:true}}))")
                expect(page.locator("#timeline-status")).to_contain_text("읽지 못했습니다")
                expect(page.locator(".timeline-slot")).to_have_count(0)
                checks["stale_timeline_and_failure_clear_old_slots"] = True
                failure = browser.new_page(viewport={"width": 1280, "height": 900}, timezone_id="Asia/Seoul")
                failure.on("pageerror", lambda error: errors.append(str(error)))
                failure.route("**/v1/meal/basemap", lambda route: route.abort())
                failure.goto(f"http://127.0.0.1:{port}/api/meal/", wait_until="networkidle")
                failure.locator(".timeline-gap").first.click()
                expect(failure.locator("#recommendation-map-panel")).to_have_attribute("data-state", "fallback")
                failure.locator("#cards .card:visible").get_by_role("button", name="이동 경로 보기").click()
                expect(failure.locator("#map-details")).to_have_attribute("data-state", "fallback")
                checks["map_failure_keeps_cards_and_route_diagram"] = True
                failure.close()
                assert not errors, errors
                checks["javascript_errors"] = errors
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=8)
            sock.close()
        (args.output / "result.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(checks, ensure_ascii=False))


if __name__ == "__main__":
    main()
