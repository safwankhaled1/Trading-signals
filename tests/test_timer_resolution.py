from types import SimpleNamespace

import pytest

from signaldesk import timing


@pytest.mark.parametrize("begin_result", [0, 97])
def test_timer_request_is_released_only_if_successful_even_on_failure(monkeypatch, begin_result):
    calls = []
    def begin(period):
        calls.append(("begin", period))
        return begin_result
    def end(period):
        calls.append(("end", period))
        return 0
    monkeypatch.setattr(timing.sys, "platform", "win32")
    monkeypatch.setattr(timing.ctypes, "WinDLL", lambda name: SimpleNamespace(timeBeginPeriod=begin, timeEndPeriod=end))
    with pytest.raises(RuntimeError):
        with timing.high_resolution_timer() as precise:
            assert precise == (begin_result == 0)
            raise RuntimeError("Worker exited")
    assert calls == [("begin", 1)] + ([("end", 1)] if begin_result == 0 else [])


def test_missing_timer_library_falls_back_to_normal_execution(monkeypatch):
    def missing(name):
        raise OSError("No timer library")
    monkeypatch.setattr(timing.sys, "platform", "win32")
    monkeypatch.setattr(timing.ctypes, "WinDLL", missing)
    with timing.high_resolution_timer() as precise:
        assert precise is False
