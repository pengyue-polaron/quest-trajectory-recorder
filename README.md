# Quest Trajectory Recorder

鲁棒接收 Quest 控制器追踪应用发出的右手控制器轨迹，并输出 CSV、2D SVG/PNG、3D SVG/PNG 和校验报告。

这个项目是 Open-Teach 风格的轻量接收端：保留 Quest APK 的 ZMQ PUSH 输入，同时把 controller tracking 变体的 `8125` 文本协议整理成可分析的数据。

## 适用场景

- Quest 端应用：Open-Teach 风格的 controller-tracking APK。
- Quest 端 IP：推荐填 `127.0.0.1`，本地用 `adb reverse` 转发端口。
- 轨迹端口：`8125`。
- 状态端口：`8095` resolution，`8100` pause/continue。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

也可以直接运行：

```bash
scripts/setup.sh
source .venv/bin/activate
```

还需要本机能运行 `adb`，并且 Quest 已开启 USB debugging。

## 一键录制

```bash
scripts/record_once.sh
```

操作顺序：

1. Quest 应用里的 IP 设置为 `127.0.0.1`。
2. 确保应用先处于红色/暂停状态；如果已经绿色，先按一次右手控制器 `B` 变红。
3. 运行 `scripts/record_once.sh`。
4. 按 `B` 变绿开始录制，移动右手控制器。
5. 再按 `B` 变红停止；脚本会自动结束并生成结果。

输出位置：

- CSV / raw log：`captures/`
- 2D / 3D 图：`plots/`

## 单独运行 receiver

```bash
quest-receive \
  --host 0.0.0.0 \
  --out-dir captures \
  --session test \
  --trajectory-gate-pause High \
  --gate-requires-prior-pause Low \
  --stop-on-pause Low \
  --stop-pause-count 20 \
  --stop-no-data-sec 0.5 \
  --stop-idle-sec 2.0
```

如果没有安装 console scripts，也可以：

```bash
PYTHONPATH=src python -m quest_trajectory_recorder.receiver --help
```

## 分析和画图

```bash
quest-analyze --drop-leading-origin captures/test_remote.csv
quest-plot2d captures/test_remote.csv --out plots/test_remote.svg --png
quest-plot3d captures/test_remote.csv --out plots/test_remote_3d.svg --png
```

PNG 转换在 macOS 上使用 `sips`；Linux/Codespaces 没有 `sips` 时会保留 SVG。

## Open-Teach bridge

如果想尽量复用 Open-Teach 的 PUB/SUB 数据流，可以运行：

```bash
quest-openteach-bridge --conflate
```

它会把：

- APK `8125` controller pose 转成 `8089 / transformed_hand_frame`。
- APK `8095` resolution 转成 `8093 / button`。
- APK `8100` pause 转成 `8102 / pause`。

注意：这个 bridge 适合实时控制风格；完整轨迹录制请用 `scripts/record_once.sh` 或 `quest-receive`，不要用 `--conflate`。

## 协议摘要

controller-tracking Quest APK 的主要轨迹帧格式是：

```text
absolute|pos_x,pos_y,pos_z|quat_x,quat_y,quat_z,quat_w|flag|point0_x,point0_y,point0_z|point1_x,point1_y,point1_z|point2_x,point2_y,point2_z
```

字段含义：

- `pos_*`：右手控制器/跟踪器在 Quest/Unity world space 下的位置，单位约为米。
- `quat_*`：姿态四元数，顺序为 `x,y,z,w`。
- `point0..2`：辅助轴端点，实测长度约 `0.1 m`。

实测辅助轴关系：

- `point1 ~= +quat X`
- `point2 ~= -quat Y`
- `point0 ~= -quat Z`

## 鲁棒性策略

- **门控录制**：默认需要先看到 `pause=Low`，再看到 `pause=High`，才写入轨迹；避免旧的排队帧混入新录制。
- **原始日志保留**：所有帧都会写入 `*_raw.jsonl`，即使解析失败也能追溯。
- **开头原点过滤**：分析和绘图默认忽略开头精确 `0,0,0` 的占位帧。
- **时间戳说明**：CSV 的 `recv_unix` 是本机接收时间，不是 Quest 设备采样时间；可用于排序，不适合计算精确速度/加速度。

## 参考

- Open-Teach repo: https://github.com/aadhithya14/Open-Teach
- Open-Teach network config: https://raw.githubusercontent.com/aadhithya14/Open-Teach/main/configs/network.yaml
- Open-Teach ZMQ helpers: https://raw.githubusercontent.com/aadhithya14/Open-Teach/main/openteach/utils/network.py
- Open-Teach keypoint transform: https://raw.githubusercontent.com/aadhithya14/Open-Teach/main/openteach/components/detector/keypoint_transform.py
