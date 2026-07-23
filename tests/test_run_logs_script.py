from pathlib import Path


def test_print_latest_runs_script_exists() -> None:
    assert Path("scripts/print_latest_runs.py").exists()


def test_send_telegram_brief_script_exists() -> None:
    assert Path("scripts/send_telegram_brief.py").exists()


def test_policy_refresh_script_exists() -> None:
    assert Path("scripts/run_policy_refresh.py").exists()


def test_daily_warning_uses_supported_partial_status() -> None:
    from app.services import DAILY_WARNING_STATUS

    assert DAILY_WARNING_STATUS == 'partial'
