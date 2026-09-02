import subprocess

from quest_trajectory_recorder.quest_ports import adb_reverse_ports


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
