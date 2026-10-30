"""
macOS 调度相关：防止追踪程序在后台被降速。

问题：预览窗口（cv2.imshow）会让 Python 成为一个 GUI 应用；当它被全屏的 X-Plane 挡住时，
macOS 的 App Nap 会合并定时器、把线程降到低 QoS（放到能效核心），
实测帧率从 30 fps 掉到约 4 fps，单帧处理从 8 ms 涨到 40–80 ms，视角一顿一顿。

对策：
  1. NSProcessInfo.beginActivityWithOptions：声明一个“用户发起、延迟敏感”的活动，
     活动期间系统不会对本进程启用 App Nap。返回的 token 必须一直持有。
  2. pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE)：把当前线程设为最高的交互 QoS。
     之后创建的线程（MediaPipe 的推理线程）会继承这个 QoS，所以要在创建 FaceLandmarker 之前调用。
"""
import ctypes
import sys

_activity = None

QOS_CLASS_USER_INTERACTIVE = 0x21

# NSActivityOptions（Foundation/NSProcessInfo.h）
NSActivityIdleSystemSleepDisabled = 1 << 20
NSActivityUserInitiated = 0x00FFFFFF | NSActivityIdleSystemSleepDisabled
NSActivityLatencyCritical = 0xFF00000000


def keep_awake():
    """返回说明文字列表（用于启动时打印）。非 macOS 上什么也不做。"""
    if sys.platform != "darwin":
        return []
    notes = []

    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        rc = libc.pthread_set_qos_class_self_np(ctypes.c_uint(QOS_CLASS_USER_INTERACTIVE), ctypes.c_int(0))
        notes.append("线程 QoS: user-interactive" if rc == 0 else "线程 QoS 设置失败 (%d)" % rc)
    except (OSError, AttributeError) as e:
        notes.append("线程 QoS 设置失败: %s" % e)

    global _activity
    try:
        from Foundation import NSProcessInfo
        _activity = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            NSActivityUserInitiated | NSActivityLatencyCritical, "Head tracking for X-Plane")
        notes.append("App Nap: 已禁用")
    except ImportError:
        notes.append("警告：未安装 pyobjc，无法禁用 App Nap，预览窗口被遮挡时可能掉帧"
                     "（conda install -n headtrack -c conda-forge pyobjc-framework-cocoa，或使用 --no-preview）")
    return notes
