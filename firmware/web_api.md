# ESP32 Web 控制接口 (网页 ↔ ESP32)

ESP32 以 AP 模式运行，网页通过 HTTP 与 ESP32 交互；ESP32 再按《板间通信协议》转发给 STM32。

## 1. 接入

| 项目 | 内容 |
|---|---|
| WiFi | SSID `DrawingRobot`，密码 `12345678`（`main.cpp` 中可改） |
| 地址 | http://192.168.4.1 |
| 方式 | 手机/电脑连接 AP 后浏览器访问 |

## 2. 接口一览

| 方法 | 路径 | 请求 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/` | - | HTML | 控制台网页 |
| GET | `/api/status` | - | JSON | 状态轮询（网页 200ms 一次） |
| GET | `/api/config` | - | JSON | 集中参数下发（A4 尺寸/画板比例/留边/进给/点动速度范围），网页启动时拉取 |
| POST | `/api/ctl` | 文本命令 | JSON | 控制指令 |
| POST | `/api/gcode` | G-code 文本 | JSON | 下发绘画程序 |

## 3. POST /api/ctl

请求 body 为纯文本命令（非 JSON）：

| 命令 | 对应 STM32 帧 | 说明 |
|---|---|---|
| `start` | GCODE_LINE + START_DRAW | 开始绘画：先下发首行（保证 STM32 队列非空）再 START，之后后台流式下发剩余行 |
| `stop` | STOP_DRAW | 停止绘画（同时清空 ESP32 缓存程序） |
| `pause` | PAUSE_DRAW | 暂停 |
| `resume` | RESUME_DRAW | 继续 |
| `estop` | ESTOP | 急停（同时清空缓存程序） |
| `clear_estop` | CLEAR_ESTOP | 取消急停（重新使能，保持零点） |
| `setzero` | SET_ZERO | 设置零点：把当前笔尖位置标定为世界坐标原点（约 0.4s，状态经 ZEROING→READY） |
| `return_zero` | RETURN_ZERO | 回零：抬笔回到零点（状态经 HOMING→READY） |
| `jogx <v>` / `jogy <v>` | JOG_X / JOG_Y | 笛卡尔点动：末端 X/Y 速度 mm/s（限幅 ±60），`0` 停止该轴；网页方向键按住移动、松开停止 |

响应 JSON：

```json
{"ok":1,"code":0,"msg":"ok"}
```

| code | 含义 |
|---|---|
| 0 | 成功（STM32 ACK_OK） |
| 1 | STM32 无响应（串口超时） |
| 2 | 当前状态不允许该命令（STM32 ACK_BAD_STATE） |
| 3 | STM32 拒绝（ACK_ERROR） |
| 4 | 未知命令 |
| 5 | 未缓存 G-code（start 时） |

## 4. POST /api/gcode

请求 body 为 G-code 文本，每行一条（`\n` 分隔），**坐标为世界坐标 mm（以 A4 纸中心为零点，X 右正 / Y 上正）**，例如：

```
G0 X-40.00 Y20.00 F1500
G1 X-20.00 Y20.00 F1500
G1 X-20.00 Y50.00 F1500
```

- 单次最多 400 行，超出返回 `ok:0`
- ESP32 **仅缓存不立即下发**；点击"开始绘画"后才边画边按 STM32 的 ACK 流控逐行下发（STM32 队列仅 16 行，队列满自动退避），进度见 `/api/status` 的 `feedIdx/feedTotal`
- **全部行送达后 ESP32 自动发送 GCODE_END 帧**，STM32 在"队列清空且收到 END"时才判定绘画完成（流式下发中队列短暂为空不会误停）
- 绘画执行中（RUNNING/PAUSED）上传会被拒绝，需先停止
- 响应 JSON：`{"ok":1,"lines":3,"msg":"已缓存, 点击开始绘画后自动下发"}`

## 5. GET /api/config

集中参数下发（定义在 ESP32 `src/config.h`，网页启动时拉取并覆盖内置默认值）：

```json
{"A4_W":210,"A4_H":297,"PX":2,"MARGIN":10,"FEED":1500,"jogMin":5,"jogMax":30,"jogDef":10}
```

| 字段 | 含义 |
|---|---|
| A4_W / A4_H | A4 纸短边/长边 mm（与 STM32 robot_config.h 一致） |
| PX | 画板比例 px/mm |
| MARGIN | 四周留边 mm |
| FEED | 默认进给 mm/min（25mm/s ≤ 3cm/s 上限） |
| jogMin/jogMax/jogDef | 点动速度滑杆范围与默认值 mm/s |

## 6. GET /api/status

响应 JSON（200ms 轮询）：

```json
{
  "st": 2, "stateName": "RUNNING", "busy": 0, "pen": 1, "fault": 0,
  "m1": {"p":1.23,"v":0.05,"t":0.10,"e":1,"mo":35,"rt":41},
  "m2": {"p":2.34,"v":0.05,"t":0.10,"e":1,"mo":36,"rt":42},
  "x": 30.12, "y": 20.34, "q": 5, "line": 12,
  "feeding": true, "feedIdx": 8, "feedTotal": 40, "stale": false
}
```

| 字段 | 含义 |
|---|---|
| st / stateName | 状态机编号 / 名称（INIT/READY/RUNNING/PAUSED/JOG/EMERGENCY/ERROR/HOMING/ZEROING） |
| busy | 1 = 处于回零/标定过渡态 |
| pen | 0=抬笔 1=落笔 |
| fault | 0=无 1=逆解失败 2=电机故障 |
| m1/m2.p/v/t | 电机位置(rad)/速度(rad/s)/扭矩(N·m) |
| m1/m2.e | 状态码（0=失能 1=使能 8~E=故障，255=无反馈数据） |
| m1/m2.mo/rt | MOS 温度 / 线圈温度 (℃) |
| x/y | 正解末端实际位置 (mm，相对零点) |
| q / line | STM32 队列已用 / 已执行行数 |
| feeding/feedIdx/feedTotal | 本地下发进度 |
| stale | true = 超过 1.5s 未收到 STM32 状态帧（串口失联） |

## 7. 网页功能

- **手绘画板**：画板比例 = 竖放 A4 纸（210×297mm，2px/mm），虚线框为四周留边 10mm 的可绘画区域；鼠标/触摸绘制多笔画图案
- **"生成Gcode"**：把画板图案映射为**世界坐标**（A4 纸中心为零点，X 右正 / Y 上正，mm），每笔画生成 `G0`（抬笔到起点）+ `G1`（落笔逐点直线，进给 `FEED` mm/min），缓存到 ESP32
- **"设置零点"**：把笔尖手动放到 A4 纸正中心后点击，标定为世界坐标原点（此后 G-code 直接以纸中心为参考）
- **笛卡尔点动方向键**（▲▼◀▶）：按住移动笔尖、松开即停（按住期间每 400ms 重发指令；STM32 有 1s 点动看门狗，网页断线自动停车）；**速度滑杆 5~30mm/s 可调**（默认 10，末端全线限速 3cm/s）；画板绿色十字实时显示位置，把它摇到纸中心后点"设置零点"即可，无需手推；仅就绪/点动态可用，绘画中自动置灰。方向约定：X 平行 A4 短边（右正），Y 平行 A4 长边（上正=朝向基座）
- **控制按钮**：开始绘画 / 停止绘画 / 暂停 / 继续 / 急停 / 取消急停；"开始绘画"会自动完成"下发首行→START→边画边下发"
- **状态展示**：状态机（中文）、抬落笔、双电机位置/速度/扭矩/温度/状态码、正解末端位置、队列与执行/下发进度；画板上的**绿色十字**实时标记末端实际位置（超出 A4 范围则不显示）

## 8. 桌面上位机 TCP 接口

ESP32 在 AP 模式下额外提供 **TCP 服务**（端口 8080，见 `config.h` 的 `TCP_PORT`），供桌面 PyQt5 上位机连接。**单连接实例**：同时只允许一个 TCP 客户端，已有连接时新连接被拒绝；TCP 连接存在期间，网页端 `/api/ctl` 与 `/api/gcode` 返回占用（code=6），保证网页与桌面上位机不同时操控下位机。

### 8.1 连接

| 项目 | 内容 |
|---|---|
| 地址 | 192.168.4.1 |
| 端口 | 8080（`config.h` 集中定义） |
| 帧格式 | 行文本，UTF-8，`\n` 分隔，每行一条指令/一帧数据 |

### 8.2 上位机 → ESP32

| 帧 | 说明 |
|---|---|
| `CMD:<name> [args]` | 控制命令，命令集与 `/api/ctl` 完全一致：`start` / `stop` / `pause` / `resume` / `estop` / `clear_estop` / `setzero` / `return_zero` / `pen_up` / `pen_down` / `enable` / `disable` / `jogx <v>` / `jogy <v>`（v 为末端速度 mm/s，限幅 ±30，0=停止） |
| `GCODE:<n>` | 开始上传 n 行 G-code（1~600），随后发送 n 行裸 G-code 正文，收满后自动缓存；绘画中（RUNNING/PAUSED）上传被拒 |
| `STATUS` | 查询状态，返回 `S:{json}` |
| `CONFIG` | 查询配置，返回 `C:{json}` |

### 8.3 ESP32 → 上位机

| 帧 | 说明 |
|---|---|
| `R:{json}` | 命令/上传 ACK：`{"ok":0|1,"code":0~7,"msg":"..."}`，code 含义同 `/api/ctl`（0 成功 / 1 无响应 / 2 状态不允许 / 3 STM32拒绝 / 4 未知命令 / 5 未缓存G-code / 6 被占用 / 7 未实现） |
| `S:{json}` | 状态帧，字段同 `/api/status`（上位机可每 100ms 发 `STATUS` 轮询） |
| `C:{json}` | 配置帧，字段同 `/api/config` |

### 8.4 说明

- 上传 G-code 后需发 `CMD:start` 才开始绘画；`CMD:start` 内部自动完成"下发首行→START→边画边下发"（同网页端）
- `pen_up` / `pen_down` 映射 STM32 协议 `CMD_SET_PEN(0x0D)`；若 STM32 侧未实现会返回 code=3 或 1
- `enable` / `disable`（电机使能/失能）当前返回 code=7"未实现"，需要在下位机协议中新增命令码并实现驱动使能后补全
