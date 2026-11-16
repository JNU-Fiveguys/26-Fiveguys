"""테스트가 개인 학사 저장소를 실제로 읽지 않게 한다."""
import pytest


@pytest.fixture(autouse=True)
def isolate_academic_store(monkeypatch):
    def unexpected_cli(*args, **kwargs):
        pytest.fail("학사 연동 테스트는 합성 CLI 출력 또는 임시 프로세스를 명시해야 합니다.")
    monkeypatch.setattr("meal.academic._run_cli", unexpected_cli)
