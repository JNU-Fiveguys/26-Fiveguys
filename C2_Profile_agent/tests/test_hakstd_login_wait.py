"""로그인 완료를 알리는 브라우저 이벤트와 새 탭을 놓치지 않아야 한다."""
from types import SimpleNamespace

import pytest

from student import hakstd


@pytest.fixture
def browser_case(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(hakstd, "time", SimpleNamespace(monotonic=lambda: clock.now))
    context = SimpleNamespace(pages=[])
    browser = SimpleNamespace(is_connected=lambda: True)

    class Page:
        def __init__(self, url, on_wait=None):
            self.url = url
            self.on_wait = on_wait

        def is_closed(self):
            return self not in context.pages

        def wait_for_timeout(self, ms):
            assert 0 < ms <= 250
            clock.now += ms / 1000
            if self.on_wait:
                callback, self.on_wait = self.on_wait, None
                callback()

    return context, browser, Page, clock


def test_detects_same_tab_navigation_only_after_events_are_pumped(browser_case):
    context, browser, Page, _ = browser_case
    page = Page("https://sso.jnu.ac.kr/login")
    page.on_wait = lambda: setattr(page, "url", "https://hakstd.jnu.ac.kr/Home/DashBoard")
    context.pages.append(page)
    assert hakstd._wait_hakstd_page(context, browser, timeout_s=1) is page


def test_uses_authenticated_popup_instead_of_original_login_tab(browser_case):
    context, browser, Page, _ = browser_case
    popup = Page("https://hakstd.jnu.ac.kr/web/Sung/Sung010")
    page = Page("https://hakstd.jnu.ac.kr/Main/Login.aspx", lambda: context.pages.append(popup))
    context.pages.append(page)
    assert hakstd._wait_hakstd_page(context, browser, timeout_s=1) is popup


def test_returns_when_all_tabs_are_closed_during_wait(browser_case):
    context, browser, Page, _ = browser_case
    context.pages.append(Page("https://sso.jnu.ac.kr/login", context.pages.clear))
    assert hakstd._wait_hakstd_page(context, browser, timeout_s=1) is None


@pytest.mark.parametrize("url", [
    "https://hakstd.jnu.ac.kr/Main/Login.aspx",
    "https://sso.jnu.ac.kr/login",
    "https://example.test/?next=https://hakstd.jnu.ac.kr/Home/DashBoard",
])
def test_times_out_without_treating_login_or_other_hosts_as_success(browser_case, url):
    context, browser, Page, clock = browser_case
    context.pages.append(Page(url))
    assert hakstd._wait_hakstd_page(context, browser, timeout_s=1) is None
    assert clock.now == 1
