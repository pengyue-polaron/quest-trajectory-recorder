from quest_trajectory_recorder import device_cli


def test_status_payload_reports_missing_ports(monkeypatch) -> None:
    monkeypatch.setattr(
        device_cli,
        "quest_device_info",
        lambda: {
            "adb_connected": True,
            "app_resumed": True,
            "model": "Quest 3",
            "serial": "serial",
        },
    )
    monkeypatch.setattr(device_cli, "adb_reverse_ports", lambda: {8095, 8100, 8125})

    payload = device_cli.status_payload()

    assert payload["ready"] is False
    assert 8127 in payload["missing_ports"]


def test_prepare_restores_ports_without_restarting_active_app(monkeypatch, capsys) -> None:
    restored: list[list[int]] = []
    focus_calls: list[dict[str, bool]] = []
    wake_calls: list[bool] = []
    monkeypatch.setattr(device_cli, "adb_connected", lambda: True)
    monkeypatch.setattr(device_cli, "keep_quest_awake", lambda: wake_calls.append(True))
    monkeypatch.setattr(device_cli, "setup_adb_reverse", lambda ports: restored.append(ports))
    monkeypatch.setattr(device_cli, "quest_activity_resumed", lambda **kwargs: True)
    monkeypatch.setattr(device_cli, "focus_frankabot", lambda **kwargs: focus_calls.append(kwargs))

    assert device_cli.main(["prepare"]) == 0

    assert wake_calls == [True]
    assert restored == [list(device_cli.QUEST_REVERSE_PORTS)]
    assert focus_calls == [{}]
    assert "XR foreground are ready" in capsys.readouterr().out


def test_restart_is_explicit(monkeypatch) -> None:
    calls: list[dict[str, bool]] = []
    monkeypatch.setattr(device_cli, "adb_connected", lambda: True)
    monkeypatch.setattr(device_cli, "focus_frankabot", lambda **kwargs: calls.append(kwargs))

    assert device_cli.main(["restart"]) == 0

    assert calls == [{"restart": True}]
