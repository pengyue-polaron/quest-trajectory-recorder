import subprocess

from quest_trajectory_recorder import quest_ports
from quest_trajectory_recorder.quest_ports import (
    QUEST_BLOCKING_PACKAGES,
    QUEST_PACKAGE,
    QUEST_VR_CATEGORY,
    adb_reverse_ports,
)


def test_adb_reverse_ports_parses_quest_mappings(monkeypatch) -> None:
    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "UsbFfs tcp:8125 tcp:8125\n"
                "UsbFfs tcp:8100 tcp:8100\n"
                "ignored localabstract:name localabstract:name\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert adb_reverse_ports() == {8100, 8125}


def test_focus_frankabot_clears_launch_dialog_and_starts_vr_activity(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(quest_ports, "adb_connected", lambda: True)
    monkeypatch.setattr(subprocess, "run", run)

    quest_ports.focus_frankabot()

    assert [
        call[-1] for call in calls[:-1] if call[:4] == ["adb", "shell", "am", "force-stop"]
    ] == list(QUEST_BLOCKING_PACKAGES)
    assert calls[-1] == [
        "adb",
        "shell",
        "am",
        "start",
        "-S",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        QUEST_VR_CATEGORY,
        "-n",
        f"{QUEST_PACKAGE}/{quest_ports.QUEST_ACTIVITY}",
        "--es",
        "unity",
        "-force-gles",
    ]
