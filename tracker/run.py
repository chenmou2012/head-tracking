#!/usr/bin/env python3
"""启动头部追踪：python run.py [--config config.yaml] [--no-preview]"""
import argparse
import os

from headtrack import config as config_mod
from headtrack.tracker import HeadTracker

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    p = argparse.ArgumentParser(description="摄像头头部追踪 → X-Plane HeadTrack 插件")
    p.add_argument("--config", default=os.path.join(HERE, "config.yaml"), help="配置文件（默认 tracker/config.yaml）")
    p.add_argument("--no-preview", action="store_true", help="不显示预览窗口")
    p.add_argument("--record", metavar="CSV", help="诊断：逐帧记录时间和数值到 CSV")
    a = p.parse_args()

    if not os.path.isfile(a.config):
        print("[config] 找不到 %s，使用内置默认值" % a.config)
    try:
        cfg, warnings = config_mod.load(a.config)
    except config_mod.ConfigError as e:
        raise SystemExit("[config] 配置有误：%s" % e)
    for w in warnings:
        print("[config] 警告：%s" % w)

    HeadTracker(cfg, config_path=a.config, preview=not a.no_preview, record=a.record).run()


if __name__ == "__main__":
    main()
