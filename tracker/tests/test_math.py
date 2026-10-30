"""数学部分的单元测试：python -m unittest discover -s tests"""
import math
import unittest

import numpy as np

from headtrack.coords import euler_yxz, relative_pose, split_matrix
from headtrack.curves import AxisCurve
from headtrack.filters import OneEuro


def Rx(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def Ry(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def Rz(a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class TestEuler(unittest.TestCase):
    def test_roundtrip(self):
        for y, p, r in [(0, 0, 0), (30, 0, 0), (-25, 15, 0), (40, -20, 10), (-60, 30, -15)]:
            got = euler_yxz(Ry(y) @ Rx(p) @ Rz(r))
            for g, e in zip(got, (y, p, r)):
                self.assertAlmostEqual(g, e, places=6)

    def test_split_removes_scale(self):
        M = np.eye(4)
        M[:3, :3] = 1.3 * (Ry(20) @ Rx(-10))
        M[:3, 3] = [1, 2, -40]
        R, t = split_matrix(M)
        self.assertAlmostEqual(np.linalg.det(R), 1.0, places=9)
        np.testing.assert_allclose(euler_yxz(R)[:2], (20, -10), atol=1e-6)
        np.testing.assert_allclose(t, [1, 2, -40])

    def test_relative_to_center(self):
        # 回中时略微低头（摄像头在上方）；之后绕头部自身竖直轴转 20°
        R0 = Rx(-12)
        R = R0 @ Ry(20)
        p = relative_pose(R, np.zeros(3), R0, np.zeros(3))
        self.assertAlmostEqual(abs(p["yaw"]), 20, places=6)
        self.assertAlmostEqual(p["pitch"], 0, places=6)

    def test_signs(self):
        # 摄像头空间绕 +Y 正转 = 用户向左转 → 协议 yaw 为负
        p = relative_pose(Ry(10), np.zeros(3), np.eye(3), np.zeros(3))
        self.assertLess(p["yaw"], 0)
        # 绕 +X 正转 = 低头 → 协议 pitch 为负
        p = relative_pose(Rx(10), np.zeros(3), np.eye(3), np.zeros(3))
        self.assertLess(p["pitch"], 0)


class TestCurve(unittest.TestCase):
    def test_yaw_defaults(self):
        c = AxisCurve(in_max=30, out_max=150, expo=1.4, deadzone=1.5)
        self.assertEqual(c(1.0), 0.0)
        self.assertEqual(c(-1.5), 0.0)
        self.assertAlmostEqual(c(30), 150)
        self.assertAlmostEqual(c(-30), -150)
        self.assertAlmostEqual(c(45), 150)          # 超出量程后饱和
        self.assertLess(c(1.6), 0.5)                # 死区边缘连续，无跳变
        self.assertAlmostEqual(c(10), -c(-10))      # 对称
        mid = c(15.75)                              # 归一化 0.5 → 0.5^1.4 ≈ 0.379
        self.assertAlmostEqual(mid, 150 * 0.5 ** 1.4, places=6)

    def test_invert_and_disable(self):
        self.assertAlmostEqual(AxisCurve(30, 150, invert=True)(30), -150)
        self.assertEqual(AxisCurve(30, 150, enabled=False)(30), 0.0)


class TestOneEuro(unittest.TestCase):
    def test_static_noise_suppressed(self):
        rng = np.random.default_rng(0)
        f = OneEuro(min_cutoff=0.8, beta=0.02)
        out = [f(0.5 * rng.standard_normal(), i / 30) for i in range(300)]
        self.assertLess(np.std(out[60:]), 0.5 / 3)   # 抖动降到 1/3 以下（理论值约 0.27）

    def test_fast_motion_low_lag(self):
        f = OneEuro(min_cutoff=0.8, beta=0.02)
        for i in range(60):                 # 0.5 秒内转到 30°（60°/s）
            t = i / 120
            y = f(min(60 * t, 30.0), t)
        for i in range(60, 90):
            y = f(30.0, i / 120)
        self.assertGreater(y, 28.0)          # 停下 0.25 秒后基本到位


if __name__ == "__main__":
    unittest.main()
