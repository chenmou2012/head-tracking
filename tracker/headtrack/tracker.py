"""
主循环：摄像头 → Face Landmarker → 相对姿态 → One Euro 滤波 → 串扰衰减 → 映射曲线 → UDP。

状态：
  WAIT_FACE   还没回中：检测到人脸并持续 auto_center_sec 后自动回中
  TRACKING    正常追踪，每帧发送一个包
  LOST        人脸丢失：停止发送（插件保持最后姿态）；恢复后从上次输出平滑过渡

回中来源：预览窗口按键、插件 headtrack/recenter 命令发来的 UDP 消息。
"""
import os
import socket
import struct
import time

import cv2
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision

from . import config as config_mod
from . import macos
from .coords import AXES, relative_pose, split_matrix
from .filters import OneEuro

TRACKER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROT_AXES = ("yaw", "pitch", "roll")
RECENTER_MSG = b"HEADTRACK_RECENTER"


def _smoothstep(a):
    a = min(max(a, 0.0), 1.0)
    return a * a * (3.0 - 2.0 * a)


def translation_gain(yaw, pitch, att):
    """
    串扰衰减增益。转头时人脸绕颈部转动，MediaPipe 会把它当成一部分位移；
    转头越大越不可信，所以按 yaw/pitch 的幅度把位移平滑压低：
        r = max(ramp(|yaw|; yaw_start→yaw_full), ramp(|pitch|; pitch_start→pitch_full))
        gain = 1 − (1 − min_gain) · smoothstep(r)
    """
    if not att.enabled:
        return 1.0
    ry = (abs(yaw) - att.yaw_start) / (att.yaw_full - att.yaw_start)
    rp = (abs(pitch) - att.pitch_start) / (att.pitch_full - att.pitch_start)
    r = _smoothstep(max(ry, rp))
    return 1.0 - (1.0 - att.min_gain) * r


class Udp:
    """
    发送：opentrack 兼容，6 个小端 double —— x, y, z (cm), yaw, pitch, roll (deg)
    接收：插件回发的回中消息（发往本 socket 的源地址，所以收发共用一个 socket）
    """

    def __init__(self, host, port):
        self.dest = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)

    def send(self, pose):
        try:
            self.sock.sendto(struct.pack("<6d", *(pose[ax] for ax in AXES)), self.dest)
        except OSError:
            pass   # 插件未运行时 macOS 可能返回 ECONNREFUSED，忽略即可

    def recenter_requested(self):
        got = False
        while True:
            try:
                data, _ = self.sock.recvfrom(256)
            except (BlockingIOError, OSError):
                return got
            if data.strip() == RECENTER_MSG:
                got = True

    def close(self):
        self.sock.close()


class HeadTracker:
    def __init__(self, cfg, config_path=None, preview=True, record=None):
        self.cfg = cfg
        self.preview = preview
        self.record = open(record, "w") if record else None   # 诊断用：逐帧记录到 CSV
        if self.record:
            self.record.write("wall,status,sent,raw_yaw,raw_pitch,out_yaw,out_pitch,gain,proc_ms,read_ms,ui_ms\n")
        self.watcher = config_mod.ConfigWatcher(config_path) if config_path else None

        model = cfg.model_path
        if not os.path.isabs(model):
            model = os.path.join(TRACKER_DIR, model)
        if not os.path.isfile(model):
            raise SystemExit(
                "找不到模型文件：%s\n请先运行 tracker/download_model.sh 下载 face_landmarker.task" % model)

        # 必须在创建 FaceLandmarker（及其推理线程）之前调用，线程才会继承高 QoS
        for note in macos.keep_awake():
            print("[tracker] %s" % note)

        options = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,
        )
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.udp = Udp(cfg.udp.host, cfg.udp.port)

        self.filters = {ax: OneEuro() for ax in AXES}
        self._apply_filter_params()

        self.R0 = None              # 回中姿态
        self.t0 = None
        self.face_since = None      # 连续检测到人脸的起始时间（用于自动回中）
        self.want_recenter = False
        self.was_lost = False

        self.last_out = {ax: 0.0 for ax in AXES}   # 上一次发送的值
        self.blend_from = None      # 平滑过渡的起点（dict），None 表示不在过渡中
        self.blend_start = 0.0

        self.raw = {ax: 0.0 for ax in AXES}
        self.out = {ax: 0.0 for ax in AXES}
        self.gain = 1.0
        self.status = "WAIT_FACE"

    # ---------- 配置 ----------

    def _apply_filter_params(self):
        for ax, f in self.filters.items():
            p = self.cfg.filter.rotation if ax in ROT_AXES else self.cfg.filter.translation
            f.min_cutoff, f.beta, f.d_cutoff = p.min_cutoff, p.beta, p.d_cutoff

    def _reload(self, new, warnings):
        old = self.cfg
        for w in warnings:
            print("\n[config] 警告：%s" % w)
        if new.camera != old.camera or new.model_path != old.model_path:
            print("\n[config] camera / model_path 的修改需重启程序才生效")
            new.camera, new.model_path = old.camera, old.model_path
        self.cfg = new
        self._apply_filter_params()   # 只改参数，不清空滤波器状态，避免跳变
        if new.udp != old.udp:
            self.udp.close()
            self.udp = Udp(new.udp.host, new.udp.port)
        print("\n[config] 已重新加载")

    # ---------- 回中与过渡 ----------

    def request_recenter(self):
        self.want_recenter = True

    def _recenter(self, R, t, now):
        self.R0, self.t0 = R.copy(), t.copy()
        for f in self.filters.values():
            f.reset()
        self._start_blend(now)
        self.want_recenter = False
        print("\n[tracker] 已回中")

    def _start_blend(self, now):
        self.blend_from = dict(self.last_out)
        self.blend_start = now

    def _apply_blend(self, target, now):
        if self.blend_from is None:
            return target
        a = (now - self.blend_start) / max(self.cfg.resume_blend_sec, 1e-6)
        if a >= 1.0:
            self.blend_from = None
            return target
        w = _smoothstep(a)
        return {ax: self.blend_from[ax] + w * (target[ax] - self.blend_from[ax]) for ax in AXES}

    # ---------- 每帧处理 ----------

    def process(self, frame_bgr, now):
        """返回 landmarks 或 None。更新 self.raw / self.out / self.status，并在需要时发送。"""
        if self.udp.recenter_requested():
            print("\n[tracker] 收到插件回中请求")
            self.request_recenter()

        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self.landmarker.detect_for_video(image, int(now * 1000))

        if not result.face_landmarks or not result.facial_transformation_matrixes:
            self.face_since = None
            if self.R0 is not None:
                self.status = "LOST"
                self.was_lost = True
            return None

        R, t = split_matrix(result.facial_transformation_matrixes[0])

        if self.R0 is None:
            # 自动回中：人脸稳定出现 auto_center_sec 秒
            if self.face_since is None:
                self.face_since = now
            if now - self.face_since < self.cfg.auto_center_sec and not self.want_recenter:
                self.status = "WAIT_FACE"
                return result.face_landmarks[0]
            self._recenter(R, t, now)
        elif self.want_recenter:
            self._recenter(R, t, now)

        if self.was_lost:
            # 人脸恢复：滤波器从新值重新开始，输出从上次发送的值平滑过渡
            for f in self.filters.values():
                f.reset()
            self._start_blend(now)
            self.was_lost = False

        self.raw = relative_pose(R, t, self.R0, self.t0)
        filtered = {ax: self.filters[ax](self.raw[ax], now) for ax in AXES}

        # 串扰衰减：只作用于位移，依据滤波后的头部转角（映射前）
        self.gain = translation_gain(filtered["yaw"], filtered["pitch"], self.cfg.translation_attenuation)
        for ax in ("x", "y", "z"):
            filtered[ax] *= self.gain

        mapped = {ax: self.cfg.axes[ax](filtered[ax]) for ax in AXES}
        self.out = self._apply_blend(mapped, now)

        self.udp.send(self.out)
        self.last_out = dict(self.out)
        self.status = "TRACKING"
        return result.face_landmarks[0]

    # ---------- 预览 ----------

    def draw(self, frame, landmarks, fps, proc_ms):
        h, w = frame.shape[:2]
        if landmarks is not None:
            for lm in landmarks:
                cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 1, (0, 220, 0), -1)
        frame = cv2.flip(frame, 1)   # 镜像显示，像照镜子一样更直观（不影响计算）

        color = {"TRACKING": (0, 220, 0), "WAIT_FACE": (0, 200, 255), "LOST": (0, 0, 255)}[self.status]
        r, o = self.raw, self.out
        lines = ["%s   %.0f fps   %.1f ms" % (self.status, fps, proc_ms)]
        if self.status == "WAIT_FACE" and self.face_since is not None:
            lines[0] += "   centering in %.1fs" % max(0.0, self.cfg.auto_center_sec - (time.monotonic() - self.face_since))
        lines.append("head  yaw %+6.1f pitch %+6.1f roll %+6.1f" % (r["yaw"], r["pitch"], r["roll"]))
        lines.append("view  yaw %+6.1f pitch %+6.1f roll %+6.1f" % (o["yaw"], o["pitch"], o["roll"]))
        lines.append("head  x %+5.1f y %+5.1f z %+5.1f cm" % (r["x"], r["y"], r["z"]))
        lines.append("view  x %+5.1f y %+5.1f z %+5.1f cm  gain %.2f" % (o["x"], o["y"], o["z"], self.gain))
        lines.append("[C/space] recenter   [Q/Esc] quit")
        for i, s in enumerate(lines):
            y = 22 + i * 22
            cv2.putText(frame, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, s, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color if i == 0 else (255, 255, 255), 1, cv2.LINE_AA)

        # 中心十字 + 当前视角指示点（按 ±out_max 缩放到画面）
        cx, cy = w // 2, h // 2
        cv2.drawMarker(frame, (cx, cy), (180, 180, 180), cv2.MARKER_CROSS, 20, 1)
        sy = self.cfg.axes["yaw"].out_max or 1.0
        sp = self.cfg.axes["pitch"].out_max or 1.0
        px = int(cx + o["yaw"] / sy * (w * 0.4))
        py = int(cy - o["pitch"] / sp * (h * 0.4))
        cv2.circle(frame, (px, py), 6, (0, 200, 255), -1)
        return frame

    # ---------- 主循环 ----------

    def run(self):
        cam = self.cfg.camera
        cap = cv2.VideoCapture(cam.index, cv2.CAP_AVFOUNDATION)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, cam.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cam.height)
        cap.set(cv2.CAP_PROP_FPS, cam.fps)
        if not cap.isOpened():
            raise SystemExit("无法打开摄像头。请在 系统设置 → 隐私与安全性 → 摄像头 中允许你的终端程序访问摄像头。")
        print("[tracker] 摄像头 %dx%d，发送到 %s:%d" % (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            self.cfg.udp.host, self.cfg.udp.port))
        if self.watcher:
            print("[tracker] 配置文件：%s（保存后自动重载）" % self.watcher.path)
        print("[tracker] 请正对屏幕保持约 %.0f 秒，将自动回中" % self.cfg.auto_center_sec)

        fps = 0.0
        last_frame_t = None
        last_print = 0.0
        ui_ms = 0.0
        try:
            while True:
                t_read = time.perf_counter()
                ok, frame = cap.read()
                read_ms = (time.perf_counter() - t_read) * 1000.0
                if not ok:
                    print("[tracker] 读取摄像头失败")
                    time.sleep(0.1)
                    continue
                now = time.monotonic()

                if self.watcher:
                    reloaded = self.watcher.poll(now)
                    if reloaded:
                        self._reload(*reloaded)

                t_start = time.perf_counter()
                landmarks = self.process(frame, now)
                proc_ms = (time.perf_counter() - t_start) * 1000.0
                if self.record:
                    r, o = self.raw, self.out
                    self.record.write("%.6f,%s,%d,%.3f,%.3f,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f\n" % (
                        time.time(), self.status, self.status == "TRACKING",
                        r["yaw"], r["pitch"], o["yaw"], o["pitch"], self.gain, proc_ms, read_ms, ui_ms))

                if last_frame_t is not None:
                    inst = 1.0 / max(now - last_frame_t, 1e-6)
                    fps = inst if fps == 0.0 else fps + 0.1 * (inst - fps)
                last_frame_t = now

                if self.preview:
                    t_ui = time.perf_counter()
                    cv2.imshow("HeadTrack", self.draw(frame, landmarks, fps, proc_ms))
                    key = cv2.waitKey(1) & 0xFF
                    ui_ms = (time.perf_counter() - t_ui) * 1000.0   # 记录在下一帧
                    if key in (ord("q"), 27):
                        break
                    if key in (ord("c"), ord(" ")):
                        self.request_recenter()
                elif now - last_print > 0.5:
                    o = self.out
                    print("\r%-9s %3.0f fps %4.1f ms | yaw %+6.1f pitch %+5.1f roll %+5.1f | x %+5.1f y %+5.1f z %+5.1f | gain %.2f" % (
                        self.status, fps, proc_ms, o["yaw"], o["pitch"], o["roll"],
                        o["x"], o["y"], o["z"], self.gain), end="", flush=True)
                    last_print = now
        except KeyboardInterrupt:
            pass
        finally:
            print()
            cap.release()
            if self.preview:
                cv2.destroyAllWindows()
            self.landmarker.close()
            self.udp.close()
            if self.record:
                self.record.close()
