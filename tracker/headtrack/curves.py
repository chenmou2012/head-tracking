"""
每个轴独立的映射曲线：死区 → 归一化 → 指数曲线 → 输出范围（可反向）。

    |x| ≤ deadzone              → 0
    s = (|x| − deadzone) / (in_max − deadzone)，并限制在 [0, 1]
    y = sign(x) · s^expo · out_max · (−1 if invert)

expo > 1 时中心附近更平缓（小幅晃动不会让视角乱跑），越往外越陡，
头转到 in_max 时视角正好到 out_max，再往外不再增加。
死区之外从 0 开始连续增长，不会在死区边缘跳变。
"""
from dataclasses import dataclass


@dataclass
class AxisCurve:
    in_max: float          # 头部输入的满量程（deg 或 cm）
    out_max: float         # 对应的视角输出（deg 或 cm）
    expo: float = 1.0
    deadzone: float = 0.0
    invert: bool = False
    enabled: bool = True

    def __call__(self, x):
        if not self.enabled:
            return 0.0
        mag = abs(x) - self.deadzone
        if mag <= 0.0:
            return 0.0
        span = max(self.in_max - self.deadzone, 1e-6)
        s = min(mag / span, 1.0)
        y = (s ** self.expo) * self.out_max
        y = y if x > 0 else -y
        return -y if self.invert else y
