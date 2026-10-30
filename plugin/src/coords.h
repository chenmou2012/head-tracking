/*
 * coords.h —— 坐标系与正负方向的唯一定义处（插件侧）
 *
 * 整条链路分两段，各自只在一个地方处理方向：
 *   1. 摄像头 → 协议：在追踪程序里处理（tracker 的配置文件里有每个轴的 invert 开关）。
 *   2. 协议 → X-Plane：只在本文件处理。
 *
 * UDP 协议约定（与 opentrack UDP 输出兼容，6 个小端 double，顺序固定）：
 *   [0] x   cm   向右为正
 *   [1] y   cm   向上为正
 *   [2] z   cm   向后为正
 *   [3] yaw   deg  向右转为正
 *   [4] pitch deg  抬头为正
 *   [5] roll  deg  向右歪头为正
 *
 * X-Plane 驾驶舱 dataref（飞机本体坐标，相对重心）：
 *   pilots_head_x  m    +x 指向右翼
 *   pilots_head_y  m    +y 向上
 *   pilots_head_z  m    +z 指向机尾（OpenGL 约定，-z 为机头方向）
 *   pilots_head_psi deg 航向，顺时针（向右）为正
 *   pilots_head_the deg 俯仰，抬头为正
 *   pilots_head_phi deg 滚转，右倾为正
 *
 * 按上面的定义，两套约定方向一致，所以下表全部是 +1。
 * 如果阶段一测试发现某个轴在 X-Plane 里方向反了，只改这里对应的符号，然后重新编译。
 */
#ifndef HEADTRACK_COORDS_H
#define HEADTRACK_COORDS_H

enum { AX_X = 0, AX_Y, AX_Z, AX_YAW, AX_PITCH, AX_ROLL, AX_COUNT };

/* 协议轴 → X-Plane dataref 的符号 */
static const double kAxisSign[AX_COUNT] = {
    +1.0, /* x     → pilots_head_x   */
    +1.0, /* y     → pilots_head_y   */
    +1.0, /* z     → pilots_head_z   */
    +1.0, /* yaw   → pilots_head_psi */
    +1.0, /* pitch → pilots_head_the */
    +1.0, /* roll  → pilots_head_phi */
};

/* 协议单位 → dataref 单位：位移 cm → m，角度 deg → deg */
static const double kAxisScale[AX_COUNT] = {
    0.01, 0.01, 0.01, 1.0, 1.0, 1.0,
};

/* 对收到的数据做合理性检查（防止坏包把视角甩飞），超出即丢弃整包 */
static const double kAxisSanityLimit[AX_COUNT] = {
    100.0, 100.0, 100.0, /* cm  */
    360.0, 180.0, 180.0, /* deg */
};

#endif
