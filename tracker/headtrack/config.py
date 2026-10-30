"""
配置：默认值 + 从 YAML 文件加载 + 校验 + 热重载检测。

YAML 中缺省的字段使用这里的默认值；未知字段会给出警告（多半是拼写错误）。
"""
import os
from dataclasses import dataclass, field, fields, is_dataclass

import yaml

from .coords import AXES
from .curves import AxisCurve


@dataclass
class CameraParams:
    index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass
class UdpParams:
    host: str = "127.0.0.1"
    port: int = 4242


@dataclass
class FilterParams:
    min_cutoff: float = 0.8   # Hz，越小静止越稳
    beta: float = 0.02        # 越大快速转头越跟手
    d_cutoff: float = 1.0


@dataclass
class FilterGroups:
    rotation: FilterParams = field(default_factory=FilterParams)
    translation: FilterParams = field(default_factory=lambda: FilterParams(0.5, 0.05, 1.0))


@dataclass
class Attenuation:
    enabled: bool = True
    yaw_start: float = 5.0
    yaw_full: float = 25.0
    pitch_start: float = 5.0
    pitch_full: float = 15.0
    min_gain: float = 0.2


def _default_axes():
    return {
        "yaw": AxisCurve(in_max=40.0, out_max=150.0, expo=1.4, deadzone=1.5),
        "pitch": AxisCurve(in_max=25.0, out_max=50.0, expo=1.4, deadzone=1.5),
        "roll": AxisCurve(in_max=20.0, out_max=20.0, expo=1.0, deadzone=2.0, enabled=False),
        "x": AxisCurve(in_max=15.0, out_max=15.0, deadzone=0.3),
        "y": AxisCurve(in_max=10.0, out_max=10.0, deadzone=0.3),
        "z": AxisCurve(in_max=15.0, out_max=15.0, deadzone=0.3),
    }


@dataclass
class Config:
    camera: CameraParams = field(default_factory=CameraParams)
    model_path: str = "models/face_landmarker.task"   # 相对 tracker/ 目录
    udp: UdpParams = field(default_factory=UdpParams)
    auto_center_sec: float = 1.0
    resume_blend_sec: float = 0.4
    filter: FilterGroups = field(default_factory=FilterGroups)
    axes: dict = field(default_factory=_default_axes)
    translation_attenuation: Attenuation = field(default_factory=Attenuation)


class ConfigError(ValueError):
    pass


def _merge(obj, data, path, warnings):
    """把 dict 合并进 dataclass 实例（递归），类型按默认值的类型转换。"""
    if not isinstance(data, dict):
        raise ConfigError("%s 应为映射（key: value）" % path)
    known = {f.name for f in fields(obj)}
    for k, v in data.items():
        if k not in known:
            warnings.append("未知字段 %s.%s（已忽略）" % (path, k))
            continue
        cur = getattr(obj, k)
        sub = "%s.%s" % (path, k)
        if is_dataclass(cur):
            _merge(cur, v, sub, warnings)
        elif k == "axes":
            _merge_axes(cur, v, sub, warnings)
        else:
            setattr(obj, k, _coerce(cur, v, sub))


def _merge_axes(axes, data, path, warnings):
    if not isinstance(data, dict):
        raise ConfigError("%s 应为映射" % path)
    for ax, v in data.items():
        if ax not in AXES:
            warnings.append("未知轴 %s.%s（已忽略）" % (path, ax))
            continue
        _merge(axes[ax], v, "%s.%s" % (path, ax), warnings)


def _coerce(default, v, path):
    try:
        if isinstance(default, bool):
            if not isinstance(v, bool):
                raise TypeError
            return v
        if isinstance(default, int):
            return int(v)
        if isinstance(default, float):
            return float(v)
        if isinstance(default, str):
            return str(v)
    except (TypeError, ValueError):
        pass
    raise ConfigError("%s 的值 %r 类型不对（应为 %s）" % (path, v, type(default).__name__))


def _validate(cfg):
    for ax, c in cfg.axes.items():
        if c.in_max <= c.deadzone:
            raise ConfigError("axes.%s: in_max 必须大于 deadzone" % ax)
        if c.expo <= 0:
            raise ConfigError("axes.%s: expo 必须大于 0" % ax)
    for name in ("rotation", "translation"):
        f = getattr(cfg.filter, name)
        if f.min_cutoff <= 0 or f.d_cutoff <= 0 or f.beta < 0:
            raise ConfigError("filter.%s: min_cutoff/d_cutoff 须 > 0，beta 须 ≥ 0" % name)
    a = cfg.translation_attenuation
    if a.yaw_full <= a.yaw_start or a.pitch_full <= a.pitch_start:
        raise ConfigError("translation_attenuation: *_full 必须大于 *_start")
    if not 0.0 <= a.min_gain <= 1.0:
        raise ConfigError("translation_attenuation.min_gain 须在 0~1 之间")


def load(path):
    """返回 (Config, warnings)。文件不存在时返回默认值。出错抛 ConfigError。"""
    cfg = Config()
    warnings = []
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f) or {}
            except yaml.YAMLError as e:
                raise ConfigError("YAML 语法错误：%s" % e)
        _merge(cfg, data, "config", warnings)
    _validate(cfg)
    return cfg, warnings


class ConfigWatcher:
    """轮询配置文件的修改时间，变化时重新加载。"""

    def __init__(self, path, interval=0.5):
        self.path = path
        self.interval = interval
        self._mtime = self._stat()
        self._next = 0.0

    def _stat(self):
        try:
            return os.stat(self.path).st_mtime_ns
        except OSError:
            return None

    def poll(self, now):
        """有变化且加载成功时返回新的 (Config, warnings)；否则返回 None。加载失败时打印错误、保留旧配置。"""
        if now < self._next:
            return None
        self._next = now + self.interval
        m = self._stat()
        if m == self._mtime:
            return None
        self._mtime = m
        try:
            return load(self.path)
        except ConfigError as e:
            print("\n[config] 重载失败，继续使用旧配置：%s" % e)
            return None
