"""
One Euro 滤波器（Casiez et al., CHI 2012）。

本质是截止频率随速度自适应的一阶低通：
    cutoff = min_cutoff + beta · |dx/dt|
  - 头静止时速度≈0，截止频率低（min_cutoff）→ 强平滑，消除抖动；
  - 快速转头时速度大，截止频率升高 → 跟得紧，几乎不拖尾。
调参经验：先把 beta 设为 0，调 min_cutoff 直到静止不抖；再逐步加大 beta 直到快速转头不拖。
"""
import math


def _alpha(cutoff, dt):
    # 一阶低通的平滑系数：tau = 1/(2π·fc)，alpha = dt / (dt + tau)
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


class OneEuro:
    def __init__(self, min_cutoff=1.0, beta=0.0, d_cutoff=1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self.reset()

    def reset(self):
        self._x = None
        self._dx = 0.0
        self._t = None

    def __call__(self, x, t):
        """x: 新的测量值；t: 时间戳（秒）。返回滤波后的值。"""
        if self._x is None or t <= self._t:
            self._x, self._t, self._dx = x, t, 0.0
            return x
        dt = t - self._t
        # 先对导数做固定截止频率的低通，再用它决定主滤波器的截止频率
        dx = (x - self._x) / dt
        self._dx += _alpha(self.d_cutoff, dt) * (dx - self._dx)
        cutoff = self.min_cutoff + self.beta * abs(self._dx)
        self._x += _alpha(cutoff, dt) * (x - self._x)
        self._t = t
        return self._x
