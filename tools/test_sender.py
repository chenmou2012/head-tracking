#!/usr/bin/env python3
"""
测试发送脚本：不用摄像头，按 opentrack UDP 协议发送假姿态，用来单独验证插件的方向和幅度。

协议：每包 6 个小端 double —— x, y, z (cm), yaw, pitch, roll (deg)
约定：x 右 / y 上 / z 后 为正；yaw 右 / pitch 上 / roll 右 为正。

用法示例：
  python3 test_sender.py --axes yaw                 # yaw 正弦摆动 ±60°
  python3 test_sender.py --axes yaw,pitch --amp 30  # 两个轴同时摆
  python3 test_sender.py --hold yaw=45              # 固定向右 45°，检查方向
  python3 test_sender.py --hold x=10 --hold y=-5    # 固定位移（cm）
  python3 test_sender.py --axes yaw --duration 3    # 3 秒后停止发送（验证插件“保持最后姿态”）

同时监听插件回发的回中消息（XP 里执行 headtrack/recenter 命令时会打印出来）。
只依赖 Python 标准库。
"""
import argparse
import math
import socket
import struct
import sys
import time

AXES = ["x", "y", "z", "yaw", "pitch", "roll"]
DEFAULT_AMP = {"x": 10.0, "y": 5.0, "z": 10.0, "yaw": 60.0, "pitch": 30.0, "roll": 20.0}
UNIT = {"x": "cm", "y": "cm", "z": "cm", "yaw": "°", "pitch": "°", "roll": "°"}
RECENTER_MSG = b"HEADTRACK_RECENTER"


def parse_args():
    p = argparse.ArgumentParser(description="向 HeadTrack 插件发送假姿态数据")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4242)
    p.add_argument("--axes", default="", help="正弦摆动的轴，逗号分隔：" + ",".join(AXES))
    p.add_argument("--amp", type=float, help="所有摆动轴的统一幅度（默认按轴：%s）" % DEFAULT_AMP)
    p.add_argument("--period", type=float, default=6.0, help="正弦周期，秒")
    p.add_argument("--hold", action="append", default=[], metavar="AXIS=VALUE",
                   help="固定某轴的值，可重复")
    p.add_argument("--rate", type=float, default=60.0, help="发送频率，Hz")
    p.add_argument("--duration", type=float, default=0.0, help="发送多少秒后停止，0 表示一直发")
    a = p.parse_args()

    a.swing = [s.strip() for s in a.axes.split(",") if s.strip()]
    for ax in a.swing:
        if ax not in AXES:
            p.error("未知轴：%s" % ax)
    a.fixed = {}
    for h in a.hold:
        try:
            k, v = h.split("=")
            k = k.strip()
            if k not in AXES:
                raise ValueError
            a.fixed[k] = float(v)
        except ValueError:
            p.error("--hold 格式应为 AXIS=VALUE，例如 yaw=30")
    if not a.swing and not a.fixed:
        p.error("至少指定 --axes 或 --hold")
    return a


def main():
    a = parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    dest = (a.host, a.port)

    desc = ["%s 摆动 ±%g%s" % (ax, a.amp if a.amp is not None else DEFAULT_AMP[ax], UNIT[ax])
            for ax in a.swing]
    desc += ["%s 固定 %g%s" % (k, v, UNIT[k]) for k, v in a.fixed.items()]
    print("发送到 %s:%d，%g Hz：%s" % (a.host, a.port, a.rate, "，".join(desc)))
    print("Ctrl+C 退出")

    t0 = time.monotonic()
    dt = 1.0 / a.rate
    next_t = t0
    last_print = 0.0
    try:
        while True:
            now = time.monotonic()
            t = now - t0
            if a.duration and t > a.duration:
                print("\n已停止发送（%.1f 秒）。插件应保持最后的姿态。" % a.duration)
                break

            s = math.sin(2.0 * math.pi * t / a.period)
            pose = {ax: 0.0 for ax in AXES}
            for ax in a.swing:
                pose[ax] = (a.amp if a.amp is not None else DEFAULT_AMP[ax]) * s
            pose.update(a.fixed)

            sock.sendto(struct.pack("<6d", *(pose[ax] for ax in AXES)), dest)

            try:
                data, addr = sock.recvfrom(256)
                if data.strip() == RECENTER_MSG:
                    print("\n收到插件回中消息，来自 %s:%d" % addr)
            except (BlockingIOError, ConnectionRefusedError):
                pass

            if now - last_print > 0.2:
                sys.stdout.write("\r" + "  ".join("%s=%+7.2f" % (ax, pose[ax]) for ax in AXES))
                sys.stdout.flush()
                last_print = now

            next_t += dt
            time.sleep(max(0.0, next_t - time.monotonic()))
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    main()
