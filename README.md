# HeadTrack —— Mac 版 X-Plane 12 摄像头头部追踪

用 MacBook 内置的 FaceTime 摄像头做头部追踪，效果类似 TrackIR：头小幅转动，驾驶舱视角大幅转动，眼睛始终看着屏幕。不需要任何额外硬件。

```
摄像头 ──► 追踪程序（Python + MediaPipe，计算头部姿态）──UDP 127.0.0.1:4242──► X-Plane 插件（改写飞行员头部 dataref）
                                    ◄──────────────── 回中消息 ────────────────
```

- 插件**不使用** `XPLMControlCamera`，而是每帧写入 `sim/graphics/view/pilots_head_*`，驾驶舱里的鼠标点击交互完全不受影响（与 opentrack 官方 X-Plane 插件的思路相同）。
- UDP 数据格式与 opentrack 的 UDP 输出兼容，插件也可以直接接收 opentrack 的数据。
- 测试环境：M1 Max MacBook Pro、macOS 27、X-Plane 12.4.3（Steam 版）、MediaPipe 1.2.0。追踪程序约 30 fps，每帧处理约 8.5 ms。

---

## 目录

1. [快速开始](#快速开始)
2. [安装](#安装)
3. [日常使用](#日常使用)
4. [参数调节指南](#参数调节指南)
5. [坐标约定与方向修正](#坐标约定与方向修正)
6. [插件行为说明](#插件行为说明)
7. [测试与诊断工具](#测试与诊断工具)
8. [常见问题](#常见问题)
9. [目录结构](#目录结构)
10. [卸载](#卸载)

---

## 快速开始

已经装好的情况下，每次飞行只需三步：

1. 启动 X-Plane 12，进入飞行，切到 3D 驾驶舱视角。
2. 双击 `start.command`（或在终端运行 `./start.command`）。
3. 正对屏幕坐好，约 1 秒后自动回中，开始飞行。

第一次使用请先完成下面的[安装](#安装)。

---

## 安装

### 1. 准备 Python 环境（conda）

需要 [Miniforge](https://github.com/conda-forge/miniforge)（或其他 conda 发行版）。

```bash
cd head-tracking/tracker
conda env create -f environment.yml
```

会创建名为 `headtrack` 的环境：Python 3.12、MediaPipe 1.2.0、OpenCV、NumPy、PyYAML，以及用于防止后台降速的 `pyobjc-framework-cocoa`。全部是 Apple Silicon 原生版本。

> `start.command` 发现环境不存在时会自动创建，这一步也可以跳过。

### 2. 下载人脸模型

```bash
cd head-tracking/tracker
./download_model.sh
```

从 Google 官方存储下载 `face_landmarker.task`（约 3.6 MB），放到 `tracker/models/`。`start.command` 发现模型不存在时也会自动下载。

### 3. 编译并安装 X-Plane 插件

1. 从 <https://developer.x-plane.com/sdk/plugin-sdk-downloads/> 下载 X-Plane SDK（XP12 用 4.x，已在 4.3.0 上测试）。
2. 把压缩包里 `SDK/` 目录的**内容**放到 `head-tracking/plugin/SDK/`，确保存在：
   ```
   plugin/SDK/CHeaders/XPLM/XPLMPlugin.h
   plugin/SDK/Libraries/Mac/XPLM.framework
   ```
   注意不要变成 `plugin/SDK/SDK/...`。
3. 需要 Xcode 命令行工具（`xcode-select --install`）。然后编译并安装：
   ```bash
   cd head-tracking/plugin
   make install
   ```
   - 生成 universal binary（arm64 + x86_64），安装到 `X-Plane 12/Resources/plugins/HeadTrack/mac_x64/HeadTrack.xpl`。
   - 默认安装到 Steam 版目录 `~/Library/Application Support/Steam/steamapps/common/X-Plane 12`。其他位置：
     ```bash
     make install XPLANE_DIR="/Applications/X-Plane 12"
     ```
   - X-Plane 运行时也可以重新 `make install`（会先删除旧文件再复制，不会让 X-Plane 崩溃），但要重启 X-Plane 或在 Developer 菜单里 Reload Plugins 才会加载新版本。
4. 启动 X-Plane，在 插件 → Plugin Admin 里应能看到已启用的 `HeadTrack`。

### 4. 摄像头权限

macOS 的摄像头权限是授予**启动追踪程序的那个程序**的，比如 Terminal、Ghostty、iTerm、VS Code。双击 `start.command` 时，权限授予的是“终端”。

- 第一次运行会弹出授权请求，点“允许”。
- 如果之前点过“不允许”，或者没有弹窗：打开 系统设置 → 隐私与安全性 → 摄像头，打开对应终端程序的开关，然后**完全退出并重新打开该终端程序**。

### 5. 关闭 Game Mode（重要）

X-Plane 全屏运行时，macOS 会自动进入 Game Mode，把所有后台程序压到很低的优先级。实测追踪程序会从 30 fps 掉到 3~4 fps，视角会严重卡顿。请任选一种办法：

- **永久关闭（推荐）**：需要已安装 Xcode。
  ```bash
  /Applications/Xcode.app/Contents/Developer/usr/bin/gamepolicyctl game-mode set off
  ```
  恢复为系统自动判断：把 `off` 换成 `auto`。
- **临时关闭**：X-Plane 在前台时，点菜单栏的游戏手柄图标，选“关闭游戏模式”。
- **改用窗口模式**：Game Mode 只在全屏时触发，把 X-Plane 改为窗口模式并拉到最大即可。

`start.command` 每次启动都会检查，未关闭时会给出提示。

### 6. 在 X-Plane 里绑定按键

打开 设置 → 键盘（或 摇杆），搜索 `headtrack`，绑定以下命令：

| 命令 | 作用 | 建议 |
|---|---|---|
| `headtrack/recenter` | 回中：以当前头部姿态为正前方 | 必绑，放在摇杆上最方便 |
| `headtrack/toggle` | 开关追踪；关闭时视角回到默认头位 | 需要用鼠标看仪表时很有用 |
| `headtrack/toggle_interp` | 开关帧插值 | 一般不用，对比效果时用 |

---

## 日常使用

### 启动方式

```bash
./start.command                  # 推荐：自动检查环境、模型、插件、Game Mode
./start.command --no-preview     # 不显示预览窗口
```

也可以手动启动：

```bash
cd tracker
conda activate headtrack
python run.py                    # 参数：--config 指定配置文件，--no-preview，--record 诊断记录
```

X-Plane 和追踪程序哪个先启动都可以。

### 回中

- **自动**：启动后检测到人脸并持续约 1 秒，就以当时的姿态作为正前方。
- **手动**：按绑定的 `headtrack/recenter` 键；或者在预览窗口里按 `C` / 空格。
- 回中时视角会在 0.4 秒内平滑回到正前方，不会跳变。
- 坐姿变了、或者觉得视角“歪了”，就回中一次。

### 预览窗口

预览画面做了镜像，像照镜子一样。窗口显示：

- 第一行：状态、帧率、每帧处理耗时。状态有三种：`TRACKING` 正常追踪；`WAIT_FACE` 等待回中；`LOST` 人脸丢失。
- `head`：头部原始角度和位移（滤波前，相对回中姿态）。
- `view`：映射后发给 X-Plane 的值。`gain` 是当前的位移衰减系数（见[串扰衰减](#串扰衰减)）。
- 画面中心的十字和黄点：黄点表示当前视角偏向哪里。

快捷键：`C` / 空格回中，`Q` / `Esc` 退出。不想看预览时可以用 `--no-preview`，终端里会显示一行实时数值。

### 人脸丢失时

追踪程序会停止发送数据，插件保持最后的姿态，视角停在那里。人脸重新出现后，视角在 0.4 秒内平滑接上，不会跳变。

---

## 参数调节指南

所有参数都在 [`tracker/config.yaml`](tracker/config.yaml) 里，每项都有注释。**追踪程序运行时修改并保存，约 0.5 秒后自动生效**，不用重启。写错时终端会提示错误，并继续使用旧配置；字段名拼错会给出警告。只有 `camera` 和 `model_path` 需要重启程序。

建议一边飞一边调：把 `config.yaml` 和 X-Plane 并排打开，改一个数、保存、马上感受效果。

### 映射曲线（灵敏度）

每个轴单独设置：

```yaml
axes:
  yaw:   { in_max: 40, out_max: 150, expo: 1.4, deadzone: 1.5, invert: false, enabled: true }
```

计算过程：头部输入的绝对值不超过 `deadzone` 时输出 0；超过的部分按 `in_max` 归一化到 0~1，再做 `expo` 次方，最后乘以 `out_max`。头部转到 `in_max` 时，视角正好达到 `out_max`，再往外转也不再增加（相当于限幅）。

| 参数 | 含义 | 调大的效果 |
|---|---|---|
| `in_max` | 头部满量程（deg 或 cm） | 整体变迟钝，要转得更多才能到最大视角 |
| `out_max` | 最大视角（deg 或 cm） | 最大能看到的范围更大，整体也更灵敏 |
| `expo` | 曲线指数，1 为线性 | 正中附近更平缓、更稳，越往外越陡 |
| `deadzone` | 死区 | 正中附近小晃动不再影响视角，但会更“粘” |
| `invert` | 反向 | 方向反了时设为 `true` |
| `enabled` | 是否启用 | `false` 时该轴固定输出 0 |

当前默认值（头部转动 → 视角转动）：

| 头部 yaw | 5° | 10° | 20° | 30° | 40° |
|---|---|---|---|---|---|
| 视角 yaw | 5° | 18° | 54° | 98° | 150° |

| 头部 pitch | 5° | 10° | 15° | 20° | 25° |
|---|---|---|---|---|---|
| 视角 pitch | 3° | 12° | 23° | 36° | 50° |

位移 x/y/z 约为 1:1，限幅 ±15 / ±10 / ±15 cm；roll 默认关闭（`axes.roll.enabled: true` 开启）。

调节思路：

- **整体太灵敏或太迟钝**：先调 `in_max`。`out_max` 决定能不能看到身后，一般不用动。
- **正中附近太灵敏，看仪表时视角乱晃**：加大 `expo`（如 1.6~1.8），或稍微加大 `deadzone`（如 2°）。
- **正中附近太粘，小幅转头没反应**：减小 `deadzone`（如 0.5°），或减小 `expo`。
- **想用身体前倾凑近仪表**：保持 z 轴启用，调大 `axes.z.out_max`。

### 滤波（防抖 vs 跟手）

使用 One Euro 滤波器：头静止时强平滑，转得快时自动放松平滑。转角和位移各有一组参数：

```yaml
filter:
  rotation:    { min_cutoff: 0.8, beta: 0.02, d_cutoff: 1.0 }   # deg
  translation: { min_cutoff: 0.5, beta: 0.05, d_cutoff: 1.0 }   # cm
```

推荐调参步骤：

1. 先把 `beta` 设为 0。
2. 头保持不动，逐步减小 `min_cutoff`，直到视角不再抖动（如 0.8 → 0.5 → 0.3）。代价是转头时会有拖尾。
3. 逐步加大 `beta`，直到快速转头时不再拖尾（如 0.02 → 0.05 → 0.1）。
4. 如果快速转头时又开始抖，说明 `beta` 太大了，往回调一点。

| 现象 | 调整 |
|---|---|
| 头不动时视角轻微晃动 | 减小 `min_cutoff` |
| 慢慢转头时感觉发涩、滞后 | 加大 `min_cutoff` |
| 快速转头时跟不上、有拖尾 | 加大 `beta` |
| 快速转头时视角抖动 | 减小 `beta` |

`d_cutoff` 一般不用改。

### 串扰衰减

转头时，人脸是绕着颈部转动的，MediaPipe 会把其中一部分当成位移，于是转头时视角会跟着漂移。程序按头部转动的幅度把位移平滑压低：

```yaml
translation_attenuation:
  enabled: true
  yaw_start: 5      # 头部 yaw 超过 5° 开始衰减
  yaw_full: 25      # 到 25° 时衰减到 min_gain
  pitch_start: 5
  pitch_full: 15
  min_gain: 0.2     # 最多压到原来的 20%
```

- 转头时视角仍有明显漂移：减小 `*_start` / `*_full`，或减小 `min_gain`。
- 转头时想同时探身看两侧，却探不出去：加大 `min_gain`。
- 预览窗口里的 `gain` 就是当前的衰减系数。

### 其他

| 参数 | 默认 | 说明 |
|---|---|---|
| `auto_center_sec` | 1.0 | 启动后检测到人脸多久自动回中 |
| `resume_blend_sec` | 0.4 | 人脸恢复或回中时的平滑过渡时长 |
| `camera.index` | 0 | 摄像头编号，0 是内置 FaceTime 摄像头（需重启生效） |
| `udp.port` | 4242 | 要与插件一致（插件端口固定为 4242） |

---

## 坐标约定与方向修正

UDP 协议（与 opentrack 兼容）：每包 48 字节，是 6 个小端 double，顺序为 `x, y, z`（cm）、`yaw, pitch, roll`（deg）。“右”指飞行员自己的右手边。

| 轴 | 正方向 | 写入的 dataref |
|---|---|---|
| x | 向右 | `sim/graphics/view/pilots_head_x`（cm → m） |
| y | 向上 | `pilots_head_y` |
| z | 向后 | `pilots_head_z` |
| yaw | 向右转头 | `pilots_head_psi` |
| pitch | 抬头 | `pilots_head_the` |
| roll | 向右歪头 | `pilots_head_phi` |

插件写入的值 = 进入驾驶舱时记录的基准头位 + 上表的偏移。位移沿飞机机体坐标轴，不随头部朝向变化。

方向转换分两段，每段只在一个文件里定义：

| 段 | 文件 | 怎么修正 |
|---|---|---|
| 摄像头 → 协议 | [`tracker/headtrack/coords.py`](tracker/headtrack/coords.py)：MediaPipe 坐标系、欧拉角分解、符号推导 | **首选**：在 `config.yaml` 里把该轴的 `invert` 设为 `true`，保存即生效 |
| 协议 → X-Plane | [`plugin/src/coords.h`](plugin/src/coords.h) 中的 `kAxisSign` | 用测试脚本确认插件方向错了，才改这里，改完要重新编译 |

判断是哪一段的问题：用 `tools/test_sender.py --hold yaw=30` 发送“向右 30°”。X-Plane 里视角向右转，说明插件没问题，问题在摄像头那一段，改 `invert`；视角向左转，说明是插件的问题，改 `coords.h`。

---

## 插件行为说明

- **只在 3D 驾驶舱视角生效**（`sim/graphics/view/view_type == 1026`）。进入驾驶舱约 0.2 秒后记录基准头位；切到外部视角时把基准值写回去，下次进来重新记录。
- **从未收到数据时完全不动视角**，所以追踪程序没开时 X-Plane 一切照旧。
- **数据中断时保持姿态**：超过 0.5 秒没收到数据，就保持最后的姿态。
- **换飞机后**会重新记录基准头位，因为不同飞机的飞行员眼点位置不同。
- **帧插值**（默认开启）：摄像头是 30 Hz，X-Plane 的帧率不固定，每帧直接用最新数据会一顿一顿。插件记录每个数据包的到达时间，在“当前时间 − 一个包间隔”处做线性插值，让每帧转动均匀。代价是多出约 37 ms 的延迟。离线模拟中，每帧步长的波动从 48% 降到 2%。
- **回中**：`headtrack/recenter` 会向最近一次发来数据的地址回发 `HEADTRACK_RECENTER`，追踪程序收到后立即回中。
- **卸载或禁用插件时**会关闭 socket，并把头部 dataref 恢复为基准值。
- **日志**：Log.txt 里以 `HeadTrack:` 开头的行，包括启动、监听端口、收到第一个包、实测包频率和插值延迟、视角类型变化、开关状态、数据中断和恢复、错误。

---

## 测试与诊断工具

### 测试发送脚本（不需要摄像头）

发送假姿态数据，单独验证插件的方向和幅度。只依赖 Python 标准库：

```bash
python3 tools/test_sender.py --axes yaw              # yaw 正弦摆动 ±60°，周期 6 秒
python3 tools/test_sender.py --axes yaw --rate 30    # 用 30 Hz 模拟摄像头
python3 tools/test_sender.py --hold yaw=45           # 固定向右 45°
python3 tools/test_sender.py --axes x,y,z --amp 10   # 三个位移轴同时摆动 ±10 cm
python3 tools/test_sender.py --axes yaw --duration 3 # 3 秒后停止，验证“保持最后姿态”
python3 tools/test_sender.py -h                      # 全部参数
```

脚本收到插件的回中消息时会打印出来，可以用来验证 `headtrack/recenter`。

注意：测试脚本和追踪程序不要同时运行，两边的数据会混在一起。

### 诊断记录

```bash
python run.py --record /tmp/ht.csv
```

逐帧记录时间、状态、原始值、输出值，以及三段耗时：读摄像头（`read_ms`）、推理（`proc_ms`）、预览窗口（`ui_ms`）。卡顿时用它判断是哪一环变慢了。

### 单元测试

```bash
cd tracker && conda activate headtrack
python -m unittest discover -s tests
```

覆盖欧拉角分解、回中后的相对姿态、正负方向、映射曲线、One Euro 滤波、配置加载与热重载、串扰衰减、回中消息的收发。

---

## 常见问题

### 视角一卡一卡，切回预览窗口就好了
几乎可以确定是 macOS **Game Mode**，见[安装第 5 步](#5-关闭-game-mode重要)。X-Plane 全屏时菜单栏会出现游戏手柄图标，就说明 Game Mode 开着。

程序启动时已经禁用了 App Nap，并把线程优先级提到最高的交互级别，终端会显示 `App Nap: 已禁用`。但这挡不住 Game Mode。如果关掉 Game Mode 还卡，用 `--record` 录一段，看三段耗时中哪一段变慢了。

### 视角整体在动，但每一帧是跳的
- 确认插件的帧插值是开着的：Log.txt 里有 `interp ON`。
- 看 X-Plane 自身的帧率。低于 30 fps 时画面本来就不流畅，可以降低画质或渲染分辨率。

### 背光、逆光，或光线太暗
窗户或台灯在身后时，脸是暗的，人脸检测会不稳，表现为预览窗口里时常出现 `LOST`，或者静止时数值乱跳。
- 让光从正面或侧前方照到脸上。屏幕本身的亮光通常就够，夜里可以把屏幕亮度调高一点。
- 避免身后有很亮的窗户。实在避免不了，就在脸前方补一盏柔光灯。
- 光线暗时摄像头会自动降低帧率，预览左上角的 fps 会低于 30，这也会让追踪变得迟钝。

### 戴眼镜：反光、跟踪不稳
MediaPipe 依赖眼睛周围的关键点。镜片上有屏幕或灯的反光时，静止时可能抖、转头时可能跳。
- 屏幕略微调低，或者稍微调整眼镜和头的角度，让反光不落在镜片中央。
- 防蓝光镀膜的反光往往更明显，换一副普通镜片的眼镜会好很多。
- 抖动可以用滤波压下来：减小 `filter.rotation.min_cutoff`（如 0.5）。
- 墨镜、很厚的镜框会明显降低精度。

### 转头稍大一点人脸就丢了（LOST）
MediaPipe 在头转到约 40°~45° 以上时容易跟丢，因为侧脸太多。办法是让小幅转头就能达到大视角：
- 减小 `axes.yaw.in_max`，比如 30。
- 头的位置尽量在画面中央，离摄像头 50~70 cm。

### 方向反了
在 `config.yaml` 里把该轴的 `invert` 改为 `true`，保存即可，详见[坐标约定与方向修正](#坐标约定与方向修正)。

### 用一会儿视角就“歪了”
坐姿变化是正常的，按一下 `headtrack/recenter` 就好。如果每次都往同一个方向慢慢漂，检查是否开了 roll，或者串扰衰减的参数是否合适。

### 插件装了但视角没反应
按顺序检查：
1. Plugin Admin 里 HeadTrack 是否已启用。
2. 是否处于 **3D 驾驶舱**视角：外部视角和 2D 面板视角不生效。
3. Log.txt 里有没有 `first packet`。没有，说明没收到数据：追踪程序没在运行，或者人脸还没回中（预览状态为 `WAIT_FACE`）。
4. 有没有按过 `headtrack/toggle` 把追踪关掉了：日志里有 `tracking OFF`。
5. 日志里出现 `bind 127.0.0.1:4242 失败`，说明端口被占用了，一般是 opentrack 或另一个头部追踪插件。

### 无法打开摄像头
- 摄像头权限没给：见[安装第 4 步](#4-摄像头权限)，改完要**重启终端程序**。
- 摄像头被 FaceTime、Zoom、Photo Booth 等程序占用，先把它们关掉。
- 接了外接摄像头时，编号可能变了，在 `config.yaml` 里改 `camera.index`，然后重启程序。

### 能和 opentrack 一起用吗？
插件兼容 opentrack 的 UDP 协议，可以在 opentrack 里选 “UDP over network” 输出到 127.0.0.1:4242。但不要同时运行本项目的追踪程序。另外，opentrack 自带的 X-Plane 插件会写同样的 dataref，不要两个插件同时启用。

### X-Plane 更新后插件失效
通常不会，因为插件只用到稳定的 SDK 接口。万一出了问题，用新 SDK 重新 `make install`，然后看 Log.txt 里的错误信息。

---

## 目录结构

```
head-tracking/
├── start.command            一键启动（检查环境/模型/插件/Game Mode）
├── plugin/                  X-Plane 插件（C）
│   ├── src/headtrack.c      主逻辑：UDP 接收、插值、写 dataref、命令
│   ├── src/coords.h         协议 → X-Plane 的方向与单位（唯一定义处）
│   ├── Makefile             universal binary；make install 安装到 X-Plane
│   └── SDK/                 X-Plane SDK（自行下载，不纳入版本库）
├── tracker/                 追踪程序（Python）
│   ├── config.yaml          全部参数（保存后热重载）
│   ├── environment.yml      conda 环境 headtrack
│   ├── download_model.sh    下载 face_landmarker.task
│   ├── run.py               启动入口
│   ├── headtrack/
│   │   ├── coords.py        摄像头 → 协议：坐标系、欧拉角分解、符号（唯一定义处）
│   │   ├── filters.py       One Euro 滤波器
│   │   ├── curves.py        映射曲线
│   │   ├── config.py        配置默认值、YAML 加载、校验、热重载
│   │   ├── macos.py         禁用 App Nap、提高线程 QoS
│   │   └── tracker.py       主循环、回中、平滑过渡、UDP、预览
│   ├── models/              模型文件（下载生成，不纳入版本库）
│   └── tests/               单元测试
└── tools/
    └── test_sender.py       假数据发送脚本
```

---

## 卸载

1. 删除插件目录：`X-Plane 12/Resources/plugins/HeadTrack/`。
2. 删除 conda 环境：`conda env remove -n headtrack`。
3. 如果之前关闭过 Game Mode，想恢复系统默认：
   ```bash
   /Applications/Xcode.app/Contents/Developer/usr/bin/gamepolicyctl game-mode set auto
   ```
4. 删除 `head-tracking` 文件夹。

---

## 许可证

本项目代码以 [MIT 许可证](LICENSE) 发布。

不包含在本仓库中、需要另行获取的第三方组件，遵循各自的许可：
- X-Plane SDK：版权归 Sandy Barbour 和 Ben Supnik，许可见随 SDK 附带的 `license.txt`。
- MediaPipe 及 `face_landmarker.task` 模型：Google，Apache License 2.0。
