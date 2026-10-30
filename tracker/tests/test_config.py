"""配置加载、热重载、串扰衰减、回中消息的测试"""
import os
import socket
import struct
import tempfile
import time
import unittest

from headtrack import config as C
from headtrack.tracker import Udp, translation_gain

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestConfig(unittest.TestCase):
    def test_shipped_yaml_matches_defaults(self):
        cfg, warnings = C.load(os.path.join(HERE, "config.yaml"))
        self.assertEqual(warnings, [])
        self.assertEqual(cfg, C.Config())

    def _load_text(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(text)
        try:
            return C.load(f.name)
        finally:
            os.unlink(f.name)

    def test_partial_override_and_unknown(self):
        cfg, w = self._load_text("axes:\n  yaw: {out_max: 120, invert: true}\n  roll: {enabled: true}\nfoo: 1\n")
        self.assertEqual(cfg.axes["yaw"].out_max, 120.0)
        self.assertTrue(cfg.axes["yaw"].invert)
        self.assertEqual(cfg.axes["yaw"].expo, 1.4)       # 未写的字段保持默认
        self.assertTrue(cfg.axes["roll"].enabled)
        self.assertEqual(len(w), 1)

    def test_errors(self):
        for bad in ("axes: {yaw: {in_max: 1, deadzone: 2}}\n",
                    "axes: {yaw: {invert: yes_please}}\n",
                    "filter: {rotation: {min_cutoff: 0}}\n",
                    "translation_attenuation: {min_gain: 2}\n",
                    "axes: [1, 2\n"):
            with self.assertRaises(C.ConfigError, msg=bad):
                self._load_text(bad)

    def test_watcher_reload_and_keep_on_error(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "c.yaml")
        open(p, "w").write("auto_center_sec: 1.0\n")
        w = C.ConfigWatcher(p, interval=0)
        self.assertIsNone(w.poll(0))
        time.sleep(0.01)
        open(p, "w").write("auto_center_sec: 2.5\n")
        os.utime(p, ns=(time.time_ns(), time.time_ns() + 10**9))
        cfg, _ = w.poll(1)
        self.assertEqual(cfg.auto_center_sec, 2.5)
        open(p, "w").write("auto_center_sec: [\n")
        os.utime(p, ns=(time.time_ns(), time.time_ns() + 2 * 10**9))
        self.assertIsNone(w.poll(2))                        # 语法错误 → 保留旧配置


class TestAttenuation(unittest.TestCase):
    def test_gain(self):
        a = C.Attenuation()
        self.assertEqual(translation_gain(0, 0, a), 1.0)
        self.assertEqual(translation_gain(5, 5, a), 1.0)
        self.assertAlmostEqual(translation_gain(25, 0, a), 0.2)
        self.assertAlmostEqual(translation_gain(-40, 0, a), 0.2)
        self.assertAlmostEqual(translation_gain(0, 15, a), 0.2)
        g = translation_gain(15, 0, a)
        self.assertTrue(0.2 < g < 1.0)
        a.enabled = False
        self.assertEqual(translation_gain(30, 30, a), 1.0)


class TestRecenterMessage(unittest.TestCase):
    def test_roundtrip_with_fake_plugin(self):
        plugin = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        plugin.bind(("127.0.0.1", 0))
        udp = Udp("127.0.0.1", plugin.getsockname()[1])
        udp.send({ax: 0.0 for ax in ("x", "y", "z", "yaw", "pitch", "roll")})
        data, addr = plugin.recvfrom(256)
        self.assertEqual(len(data), 48)
        self.assertFalse(udp.recenter_requested())
        plugin.sendto(b"HEADTRACK_RECENTER", addr)          # 插件回发到最近一次数据的源地址
        time.sleep(0.05)
        self.assertTrue(udp.recenter_requested())
        self.assertFalse(udp.recenter_requested())
        udp.close(); plugin.close()


if __name__ == "__main__":
    unittest.main()
