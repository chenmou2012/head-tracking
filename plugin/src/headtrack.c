/*
 * HeadTrack —— X-Plane 12 摄像头头部追踪插件
 *
 * 从 127.0.0.1:4242 接收 opentrack 兼容的 UDP 姿态包（6 个小端 double，见 coords.h），
 * 每帧写入 sim/graphics/view/pilots_head_* 六个 dataref。
 * 不使用 XPLMControlCamera，因此不影响驾驶舱内的鼠标点击交互。
 *
 * 状态说明：
 *   g_enabled  用户开关（headtrack/toggle），默认开启
 *   g_active   当前是否正在接管头部 dataref
 *              = g_enabled && 处于 3D 驾驶舱视角 && 已记录基准值
 *   g_base[]   接管时记录的头部位置/角度，输出 = 基准 + 偏移；
 *              停止接管时把它写回去
 *
 * 插值（headtrack/toggle_interp，默认开启）：
 *   数据包频率（摄像头 30 Hz、测试脚本 60 Hz）和 X-Plane 渲染帧率不同步，
 *   每帧直接取最新包会让每帧的转动量忽大忽小，看起来一顿一顿。
 *   开启插值后，用内核记录的每包到达时间把数据当作一条连续曲线，
 *   在“当前时间 − 延迟”处线性插值取值，延迟约等于一个包间隔，
 *   这样每帧的转动量与真实时间成正比，代价是增加约一个包间隔的延迟。
 */

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/uio.h>
#include <unistd.h>

#include "XPLMDataAccess.h"
#include "XPLMPlugin.h"
#include "XPLMProcessing.h"
#include "XPLMUtilities.h"

#include "coords.h"

#define HT_PORT              4242
#define HT_VIEW_3D_COCKPIT   1026   /* sim/graphics/view/view_type 的 3D 驾驶舱取值 */
#define HT_STALE_SEC         0.5    /* 超过这个时间没收到数据，视为中断，保持最后姿态 */
#define HT_SETTLE_SEC        0.2    /* 进入驾驶舱视角后等待 X-Plane 摆好默认头位，再记录基准 */
#define HT_PACKET_SIZE       (6 * sizeof(double))
#define HT_RECENTER_MSG      "HEADTRACK_RECENTER"

#define HT_RING              16     /* 插值用的历史包数量 */
#define HT_INTERP_MARGIN     0.004  /* 插值延迟 = 包间隔估计 + 这个余量（秒），吸收到达时间抖动 */
#define HT_INTERVAL_MIN      0.005
#define HT_INTERVAL_MAX      0.100

static const char *kHeadRefNames[AX_COUNT] = {
    "sim/graphics/view/pilots_head_x",
    "sim/graphics/view/pilots_head_y",
    "sim/graphics/view/pilots_head_z",
    "sim/graphics/view/pilots_head_psi",
    "sim/graphics/view/pilots_head_the",
    "sim/graphics/view/pilots_head_phi",
};

static XPLMDataRef       g_head_ref[AX_COUNT];
static XPLMDataRef       g_view_type_ref;
static XPLMFlightLoopID  g_loop;
static XPLMCommandRef    g_cmd_toggle, g_cmd_recenter, g_cmd_interp;

static int    g_sock = -1;
static struct sockaddr_in g_peer;          /* 最近一次发来数据的地址，回中消息发往这里 */
static int    g_have_peer;

static int    g_enabled = 1;
static int    g_interp = 1;
static int    g_active;
static double g_base[AX_COUNT];
static double g_settle_start = -1.0;

/* 收到的包（按到达时间排列的环形缓冲），时间为墙钟秒 */
typedef struct { double t; double v[AX_COUNT]; } Sample;
static Sample g_ring[HT_RING];
static int    g_ring_count, g_ring_head;   /* head 指向最新一个 */
static double g_interval = 1.0 / 30.0;     /* 包间隔估计（EMA） */
static long   g_rx_total;
static int    g_rate_logged;

static double g_pose[AX_COUNT];            /* 本帧要输出的姿态（协议单位） */
static double g_last_rx_time = -1.0;       /* 墙钟秒 */
static int    g_stale_logged;
static int    g_last_view_type = -1;
static long   g_bad_packets;

/* ---------- 工具 ---------- */

static void ht_log(const char *fmt, ...)
{
    char buf[512];
    int n = snprintf(buf, sizeof(buf), "HeadTrack: ");
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf + n, sizeof(buf) - (size_t)n - 1, fmt, ap);
    va_end(ap);
    strncat(buf, "\n", sizeof(buf) - strlen(buf) - 1);
    XPLMDebugString(buf);
}

static double wall_now(void)
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (double)tv.tv_sec + tv.tv_usec * 1e-6;
}

/* ---------- UDP ---------- */

static int socket_open(void)
{
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    if (s < 0) {
        ht_log("ERROR socket(): %s", strerror(errno));
        return -1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons(HT_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(s, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ht_log("ERROR bind 127.0.0.1:%d 失败: %s（端口是否被 opentrack 等程序占用？）",
               HT_PORT, strerror(errno));
        close(s);
        return -1;
    }
    /* 让内核给每个包打上到达时间戳：我们每帧才读一次，自己取时间会被量化到帧 */
    int on = 1;
    if (setsockopt(s, SOL_SOCKET, SO_TIMESTAMP, &on, sizeof(on)) < 0)
        ht_log("WARNING SO_TIMESTAMP 不可用，插值精度会下降: %s", strerror(errno));
    int flags = fcntl(s, F_GETFL, 0);
    fcntl(s, F_SETFL, flags | O_NONBLOCK);
    ht_log("listening on UDP 127.0.0.1:%d", HT_PORT);
    return s;
}

static void socket_close(void)
{
    if (g_sock >= 0) {
        close(g_sock);
        g_sock = -1;
    }
}

static void ring_push(double t, const double v[AX_COUNT])
{
    if (g_ring_count > 0) {
        double dt = t - g_ring[g_ring_head].t;
        /* 只用正常连续的间隔更新估计，忽略中断和同批到达的包 */
        if (dt > 0.001 && dt < 0.25) {
            g_interval += 0.05 * (dt - g_interval);
            if (g_interval < HT_INTERVAL_MIN) g_interval = HT_INTERVAL_MIN;
            if (g_interval > HT_INTERVAL_MAX) g_interval = HT_INTERVAL_MAX;
        }
        if (dt < 0.0) t = g_ring[g_ring_head].t;   /* 保证时间单调 */
    }
    g_ring_head = (g_ring_head + 1) % HT_RING;
    g_ring[g_ring_head].t = t;
    memcpy(g_ring[g_ring_head].v, v, sizeof(g_ring[0].v));
    if (g_ring_count < HT_RING) g_ring_count++;
}

/* 读空接收缓冲区：每个有效包都带时间戳进入环形缓冲 */
static void socket_poll(void)
{
    if (g_sock < 0) return;
    for (;;) {
        unsigned char buf[256];
        struct sockaddr_in from;
        struct iovec iov = { .iov_base = buf, .iov_len = sizeof(buf) };
        unsigned char ctrl[CMSG_SPACE(sizeof(struct timeval))];
        struct msghdr msg = {
            .msg_name = &from, .msg_namelen = sizeof(from),
            .msg_iov = &iov, .msg_iovlen = 1,
            .msg_control = ctrl, .msg_controllen = sizeof(ctrl),
        };
        ssize_t n = recvmsg(g_sock, &msg, 0);
        if (n < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
                ht_log("ERROR recvmsg(): %s", strerror(errno));
            break;
        }
        if ((size_t)n != HT_PACKET_SIZE) {
            g_bad_packets++;
            continue;
        }
        /* macOS 的 arm64 和 x86_64 都是小端，直接拷贝即可 */
        double v[AX_COUNT];
        memcpy(v, buf, sizeof(v));
        int ok = 1;
        for (int i = 0; i < AX_COUNT; i++)
            if (!isfinite(v[i]) || fabs(v[i]) > kAxisSanityLimit[i]) ok = 0;
        if (!ok) {
            g_bad_packets++;
            continue;
        }

        double t = -1.0;
        for (struct cmsghdr *c = CMSG_FIRSTHDR(&msg); c; c = CMSG_NXTHDR(&msg, c)) {
            if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_TIMESTAMP) {
                struct timeval tv;
                memcpy(&tv, CMSG_DATA(c), sizeof(tv));
                t = (double)tv.tv_sec + tv.tv_usec * 1e-6;
            }
        }
        if (t < 0.0) t = wall_now();

        if (g_rx_total == 0)
            ht_log("first packet from %s:%d", inet_ntoa(from.sin_addr), ntohs(from.sin_port));
        g_rx_total++;
        ring_push(t, v);
        g_peer = from;
        g_have_peer = 1;
        if (g_stale_logged) {
            ht_log("data resumed");
            g_stale_logged = 0;
        }
        g_last_rx_time = t;
    }
}

/*
 * 取时间 t 处的姿态：在相邻两包之间线性插值。
 * t 晚于最新包 → 保持最新包（不外推，避免过冲）；
 * t 早于缓冲里最旧的包 → 用最旧的包。
 * 两包间隔超过 HT_STALE_SEC（中断后恢复）时不跨越插值，直接取较新的包。
 */
static void sample_at(double t, double out[AX_COUNT])
{
    const Sample *newer = &g_ring[g_ring_head];
    if (t >= newer->t) {
        memcpy(out, newer->v, sizeof(newer->v));
        return;
    }
    for (int k = 1; k < g_ring_count; k++) {
        const Sample *older = &g_ring[(g_ring_head - k + HT_RING) % HT_RING];
        if (t >= older->t) {
            double span = newer->t - older->t;
            if (span <= 1e-6 || span > HT_STALE_SEC) {
                memcpy(out, newer->v, sizeof(newer->v));
                return;
            }
            double a = (t - older->t) / span;   /* 0 → older，1 → newer */
            for (int i = 0; i < AX_COUNT; i++)
                out[i] = older->v[i] + a * (newer->v[i] - older->v[i]);
            return;
        }
        newer = older;
    }
    memcpy(out, newer->v, sizeof(newer->v));   /* 比缓冲里最旧的包还早 */
}

static void update_pose(double now)
{
    if (g_interp)
        sample_at(now - (g_interval + HT_INTERP_MARGIN), g_pose);
    else
        memcpy(g_pose, g_ring[g_ring_head].v, sizeof(g_pose));
}

/* ---------- 头部 dataref ---------- */

static void capture_base(void)
{
    for (int i = 0; i < AX_COUNT; i++)
        g_base[i] = XPLMGetDataf(g_head_ref[i]);
}

static void restore_base(void)
{
    for (int i = 0; i < AX_COUNT; i++)
        XPLMSetDataf(g_head_ref[i], (float)g_base[i]);
}

/* 输出 = 基准 + 符号 × 单位换算 × 偏移；坐标约定见 coords.h */
static void write_pose(void)
{
    for (int i = 0; i < AX_COUNT; i++) {
        double out = g_base[i] + kAxisSign[i] * kAxisScale[i] * g_pose[i];
        XPLMSetDataf(g_head_ref[i], (float)out);
    }
}

static void deactivate(int restore)
{
    if (g_active && restore) restore_base();
    g_active = 0;
    g_settle_start = -1.0;
}

/* ---------- 每帧回调 ---------- */

static float flight_loop_cb(float since_last_call, float since_last_loop, int counter, void *refcon)
{
    (void)since_last_call; (void)since_last_loop; (void)counter; (void)refcon;
    double sim_now = XPLMGetElapsedTime();
    double now = wall_now();

    socket_poll();

    if (!g_rate_logged && g_rx_total >= 120) {
        ht_log("packet rate ~%.0f Hz, interp %s (delay %.0f ms)",
               1.0 / g_interval, g_interp ? "ON" : "OFF", (g_interval + HT_INTERP_MARGIN) * 1000.0);
        g_rate_logged = 1;
    }

    int vt = XPLMGetDatai(g_view_type_ref);
    if (vt != g_last_view_type) {
        ht_log("view_type = %d%s", vt, vt == HT_VIEW_3D_COCKPIT ? " (3D cockpit)" : "");
        g_last_view_type = vt;
    }

    int want = g_enabled && vt == HT_VIEW_3D_COCKPIT;
    if (!want) {
        /* 离开驾驶舱视角或被关闭：写回基准值，下次进入时重新记录 */
        deactivate(1);
        return -1.0f;
    }

    if (!g_active) {
        if (g_settle_start < 0.0) g_settle_start = sim_now;
        if (sim_now - g_settle_start < HT_SETTLE_SEC) return -1.0f;
        capture_base();
        g_active = 1;
        ht_log("active, base x=%.3f y=%.3f z=%.3f psi=%.1f the=%.1f phi=%.1f",
               g_base[0], g_base[1], g_base[2], g_base[3], g_base[4], g_base[5]);
    }

    if (g_ring_count == 0) return -1.0f;   /* 还没收到过数据：不动 X-Plane 的视角 */

    if (now - g_last_rx_time > HT_STALE_SEC && !g_stale_logged) {
        ht_log("no data for %.1fs, holding last pose", HT_STALE_SEC);
        g_stale_logged = 1;
    }
    /* 中断时 sample_at 返回最新包，即保持最后姿态 */
    update_pose(now);
    write_pose();
    return -1.0f;
}

/* ---------- 命令 ---------- */

static int cmd_toggle_cb(XPLMCommandRef cmd, XPLMCommandPhase phase, void *refcon)
{
    (void)cmd; (void)refcon;
    if (phase != xplm_CommandBegin) return 0;
    g_enabled = !g_enabled;
    if (!g_enabled) deactivate(1);
    ht_log("tracking %s", g_enabled ? "ON" : "OFF");
    return 0;
}

static int cmd_interp_cb(XPLMCommandRef cmd, XPLMCommandPhase phase, void *refcon)
{
    (void)cmd; (void)refcon;
    if (phase != xplm_CommandBegin) return 0;
    g_interp = !g_interp;
    ht_log("interp %s", g_interp ? "ON" : "OFF");
    return 0;
}

static int cmd_recenter_cb(XPLMCommandRef cmd, XPLMCommandPhase phase, void *refcon)
{
    (void)cmd; (void)refcon;
    if (phase != xplm_CommandBegin) return 0;
    if (g_sock < 0 || !g_have_peer) {
        ht_log("recenter: no tracker seen yet");
        return 0;
    }
    ssize_t n = sendto(g_sock, HT_RECENTER_MSG, strlen(HT_RECENTER_MSG), 0,
                       (struct sockaddr *)&g_peer, sizeof(g_peer));
    if (n < 0)
        ht_log("ERROR recenter sendto(): %s", strerror(errno));
    else
        ht_log("recenter sent to %s:%d", inet_ntoa(g_peer.sin_addr), ntohs(g_peer.sin_port));
    return 0;
}

/* ---------- 插件入口 ---------- */

PLUGIN_API int XPluginStart(char *outName, char *outSig, char *outDesc)
{
    strcpy(outName, "HeadTrack");
    strcpy(outSig, "local.headtrack");
    strcpy(outDesc, "Webcam head tracking via UDP (opentrack protocol)");

    for (int i = 0; i < AX_COUNT; i++) {
        g_head_ref[i] = XPLMFindDataRef(kHeadRefNames[i]);
        if (!g_head_ref[i] || !XPLMCanWriteDataRef(g_head_ref[i])) {
            ht_log("ERROR dataref missing or not writable: %s", kHeadRefNames[i]);
            return 0;
        }
    }
    g_view_type_ref = XPLMFindDataRef("sim/graphics/view/view_type");
    if (!g_view_type_ref) {
        ht_log("ERROR dataref missing: sim/graphics/view/view_type");
        return 0;
    }

    g_cmd_toggle = XPLMCreateCommand("headtrack/toggle", "HeadTrack: toggle head tracking");
    g_cmd_recenter = XPLMCreateCommand("headtrack/recenter", "HeadTrack: recenter tracker");
    g_cmd_interp = XPLMCreateCommand("headtrack/toggle_interp", "HeadTrack: toggle frame interpolation");
    XPLMRegisterCommandHandler(g_cmd_toggle, cmd_toggle_cb, 1, NULL);
    XPLMRegisterCommandHandler(g_cmd_recenter, cmd_recenter_cb, 1, NULL);
    XPLMRegisterCommandHandler(g_cmd_interp, cmd_interp_cb, 1, NULL);

    XPLMCreateFlightLoop_t params = {
        .structSize = sizeof(XPLMCreateFlightLoop_t),
        .phase = xplm_FlightLoop_Phase_AfterFlightModel,
        .callbackFunc = flight_loop_cb,
        .refcon = NULL,
    };
    g_loop = XPLMCreateFlightLoop(&params);

    ht_log("started");
    return 1;
}

PLUGIN_API void XPluginStop(void)
{
    XPLMUnregisterCommandHandler(g_cmd_toggle, cmd_toggle_cb, 1, NULL);
    XPLMUnregisterCommandHandler(g_cmd_recenter, cmd_recenter_cb, 1, NULL);
    XPLMUnregisterCommandHandler(g_cmd_interp, cmd_interp_cb, 1, NULL);
    if (g_loop) XPLMDestroyFlightLoop(g_loop);
    g_loop = NULL;
    ht_log("stopped");
}

PLUGIN_API int XPluginEnable(void)
{
    g_sock = socket_open();
    g_ring_count = 0;
    g_ring_head = 0;
    g_rx_total = 0;
    g_rate_logged = 0;
    g_have_peer = 0;
    g_stale_logged = 0;
    g_active = 0;
    g_settle_start = -1.0;
    XPLMScheduleFlightLoop(g_loop, -1.0f, 1);
    ht_log("enabled, tracking %s, interp %s", g_enabled ? "ON" : "OFF", g_interp ? "ON" : "OFF");
    return 1;   /* 即使 socket 失败也保持启用，便于在日志中看到错误 */
}

PLUGIN_API void XPluginDisable(void)
{
    XPLMScheduleFlightLoop(g_loop, 0.0f, 1);
    deactivate(1);
    socket_close();
    if (g_bad_packets) ht_log("dropped %ld malformed packets", g_bad_packets);
    ht_log("disabled");
}

PLUGIN_API void XPluginReceiveMessage(XPLMPluginID from, int msg, void *param)
{
    (void)from;
    /* 换飞机后飞行员眼点会变，丢弃旧基准，下一帧重新记录（不写回旧值） */
    if (msg == XPLM_MSG_PLANE_LOADED && (intptr_t)param == 0) {
        deactivate(0);
        ht_log("user aircraft loaded, base will be re-captured");
    }
}
