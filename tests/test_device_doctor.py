from quest_trajectory_recorder.device_doctor import parse_reverse_ports


def test_parse_reverse_ports_accepts_adb_output() -> None:
    output = "UsbFfs tcp:8125 tcp:8125\nUsbFfs tcp:8100 tcp:8100\n"
    assert parse_reverse_ports(output) == {8100, 8125}
