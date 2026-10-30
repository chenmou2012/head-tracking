"""
坐标系与正负方向的唯一定义处（追踪程序侧）。

MediaPipe 的 facial transformation matrix（4×4）把“标准人脸模型”变换到摄像头的度量空间：
  - 右手系，单位 cm；摄像头位于原点，朝 -Z 方向看；
  - +X 指向图像右侧，+Y 指向图像上方，+Z 从人脸指向摄像头（人脸位于 z < 0 处）。
  - 正对摄像头时，标准人脸模型的“正前方”是 +Z（朝向摄像头）。
  注意图像未做镜像：图像右侧是用户的左侧。

协议约定（与插件一致，见 plugin/src/coords.h）：
  x 右 / y 上 / z 后 为正（cm）；yaw 右 / pitch 抬头 / roll 右歪头 为正（deg）。
  这里的“右”指用户自己的右手边。

按上面的定义推导出摄像头空间 → 协议的符号（CAM_TO_PROTOCOL_SIGN）：
  yaw   绕 +Y 正转：人脸正前方 (+Z) 转向 +X（图像右 = 用户左）→ 用户向左转  → 取负
  pitch 绕 +X 正转：+Z 转向 -Y → 人脸朝下 → 低头                         → 取负
  roll  绕 +Z 正转：从摄像头看逆时针，头顶移向图像左侧 = 用户右侧 → 右歪头 → 取正
  x     +X = 图像右 = 用户左                                           → 取负
  y     +Y = 上                                                        → 取正
  z     +Z = 朝向摄像头 = 向前；协议 z 向后为正                          → 取负
如果实测某个轴方向反了，优先用配置里每个轴的 invert 开关修正；
若确认是这里的推导错了，再改 CAM_TO_PROTOCOL_SIGN。
"""
import math

import numpy as np

AXES = ("x", "y", "z", "yaw", "pitch", "roll")

CAM_TO_PROTOCOL_SIGN = {
    "x": -1.0,
    "y": +1.0,
    "z": -1.0,
    "yaw": -1.0,
    "pitch": -1.0,
    "roll": +1.0,
}


def euler_yxz(R):
    """
    把旋转矩阵拆成欧拉角（度），约定 R = Ry(yaw) · Rx(pitch) · Rz(roll)。

    这个顺序符合头部运动的直觉：先绕竖直轴转头，再在转过的方向上点头，最后歪头；
    在常见转头范围内（pitch 远离 ±90°）不会出现万向节锁。

    展开 Ry·Rx·Rz 可得：
        R[1,2] = -sin(pitch)
        R[0,2] =  sin(yaw)·cos(pitch),   R[2,2] = cos(yaw)·cos(pitch)
        R[1,0] =  cos(pitch)·sin(roll),  R[1,1] = cos(pitch)·cos(roll)
    所以：
        pitch = asin(-R[1,2])
        yaw   = atan2(R[0,2], R[2,2])
        roll  = atan2(R[1,0], R[1,1])
    """
    pitch = math.asin(max(-1.0, min(1.0, -R[1, 2])))
    yaw = math.atan2(R[0, 2], R[2, 2])
    roll = math.atan2(R[1, 0], R[1, 1])
    return math.degrees(yaw), math.degrees(pitch), math.degrees(roll)


def split_matrix(M):
    """4×4 变换矩阵 → (3×3 旋转, 3 维平移 cm)。去掉可能存在的缩放，保证 R 正交。"""
    M = np.asarray(M, dtype=np.float64)
    R = M[:3, :3]
    # 用 SVD 求最接近的正交矩阵（去掉缩放和数值误差）
    U, _, Vt = np.linalg.svd(R)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    return R, M[:3, 3].copy()


def relative_pose(R, t, R0, t0):
    """
    相对回中姿态的头部运动，结果已换算到协议坐标（未经滤波和曲线）。

    旋转：R = R0 · R_rel  →  R_rel = R0ᵀ · R
      R_rel 表达在“回中时的头部坐标系”里，所以即使摄像头不在正前方
      （比如笔记本摄像头在屏幕上方、人略微低头对着它），yaw 仍是绕头部自身竖直轴的转动。
    平移：直接在摄像头坐标系下相减（cm）。
    """
    R_rel = R0.T @ R
    yaw_c, pitch_c, roll_c = euler_yxz(R_rel)
    dt = t - t0
    cam = {"x": dt[0], "y": dt[1], "z": dt[2], "yaw": yaw_c, "pitch": pitch_c, "roll": roll_c}
    return {ax: CAM_TO_PROTOCOL_SIGN[ax] * cam[ax] for ax in AXES}
