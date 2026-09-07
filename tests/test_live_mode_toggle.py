from app import execution_mode_status


def test_paper_mode_is_default_when_credentials_missing():
    status, reason = execution_mode_status(enabled=False, mt5_module=None, account_id=None, password=None)
    assert status == "PAPER"
    assert "safe" in reason.lower()


def test_live_mode_is_ready_only_when_credentials_exist():
    status, reason = execution_mode_status(enabled=True, mt5_module=object(), account_id=12345, password="secret")
    assert status == "LIVE"
    assert "ready" in reason.lower()
