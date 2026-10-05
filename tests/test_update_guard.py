import pytest

from tools.apply_update import validate_worker


@pytest.mark.parametrize("state", ["open", "pending"])
def test_managed_trade_restart_requires_opt_in(state):
    snapshot = {"signals": [{"state": state}]}
    with pytest.raises(RuntimeError, match="active"):
        validate_worker(snapshot, "live")
    validate_worker(snapshot, "live", allow_active_restart=True)


@pytest.mark.parametrize("state", ["sending", "uncertain"])
def test_unresolved_requests_block_even_an_approved_restart(state):
    with pytest.raises(RuntimeError, match="uncertain"):
        validate_worker({"signals": [{"state": state}]}, "live", allow_active_restart=True)


def test_read_only_manual_and_simulation_updates_remain_allowed():
    snapshot = {"signals": [{"state": "open", "manual": True, "config": {"manage_manual_stops": False}}],
                "settings": {"manage_manual_stops": False}}
    validate_worker(snapshot, "live")
    snapshot["settings"]["manage_manual_stops"] = True
    with pytest.raises(RuntimeError):
        validate_worker(snapshot, "live")
    validate_worker(snapshot, "demo")
