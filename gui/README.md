# Desk Robot 绘图机器人上位机（v0.3）

PyQt5 上位机：手绘轨迹、图片转线稿（6 种提取算法）、路径规划、G-code 生成、整段下发、仿真/串口/TCP 通信、末端位置可视化与已/未绘制分色。

## 分工约定（与下位机）

- 上位机只发送**目标 xy 位置与指令**；运动学解算（逆解/可达性/插补）全部在下位机完成
- 抬笔机构为电机，上位机只发抬/落指令，不做参数调整
- **G-code 整段一次性下发**：TCP 后端整段上传 ESP32（ESP32 按 STM32 ACK 流控逐行下发）；仿真后端分包 + ACK 传输，下位机缓冲执行
- 暂停/恢复/停止/急停是**下位机命令**，不是上位机停发
- **通信协议**：桌面上位机 ↔ ESP32 走 **TCP**（行文本，见下文「通信协议」）；仿真后端用占位文本协议（`simulation.py`）

## 运行

```bash
pip install -r requirements.txt
python main.py        # 或双击 run.bat
```

依赖：PyQt5 / pyserial / numpy / opencv-python。无硬件时选**仿真后端**即可跑通全链路；连 ESP32 选 **TCP（ESP32）** 后端，地址默认 `192.168.4.1:8080`。

## 功能

| 模块 | 说明 |
|---|---|
| 手绘 | 画布鼠标绘制（毫米坐标，默认横置 A4 297×210mm，启动自动填满窗口）、撤销/清空、Chaikin 平滑、笔画排序（贪心最近邻 + 2-opt，优先画相邻笔画、减少抬笔空走）、缩放平移（工具条/Ctrl+±/Ctrl+0） |
| 图片线稿 | 导入图片、方向调节（左/右转90°、水平/垂直翻转、重置）、6 种提取模式（二值化/Canny/自适应阈值/黑帽细线/Sobel 梯度/XDoG 线条）、模糊、轮廓简化、最小面积、反色、自动缩放居中；调参实时预览（灰色、100ms 防抖），预览期间自动隐藏旧蓝色线稿，「生成线稿」替换当前轨迹 |
| G-code | 从轨迹生成（G0 空走 + M3/M5 抬落笔 + G1 绘制，默认自动优化笔画顺序）、保存/复制/直接编辑 |
| 控制 | 仿真/串口/TCP(ESP32) 后端、jog 八方向（长按速度式 5~30mm/s）、设零点/回零、整段下发并运行、暂停/继续/停止/急停、绘制速度 |
| 调试 | 单电机正反转（M1/M2，长按移动松开停，速度 0.1~10 rad/s 可调）、抬笔/落笔 |
| 监视 | 状态轮询（100ms = 10Hz）：状态机/忙碌/故障码、末端 xy、双电机（位置/速度/扭矩/状态码/MOS温度/线圈温度）、笔状态、执行行号、队列、ESP32 下发进度（feedIdx/feedTotal）、失联标记；画布实时末端十字标记，执行中轨迹按 已绘制(蓝)/未绘制(灰) 分色；收发日志（高频状态回报不刷日志） |

## 项目结构

```
main.py                入口
app/
  settings.py          QSettings 参数持久化
  core/
    trajectory.py      Stroke / Trajectory 轨迹模型
    lineart.py         图片 → 折线（cv2）
    gcode.py           轨迹 → G-code 文本 + 单行解析
  comm/
    transport.py       通信后端抽象（字节流 + 行帧）
    serial_transport.py 串口后端（pyserial + 读线程）
    tcp_transport.py   TCP 后端（QTcpSocket，连 ESP32，行帧协议）
    simulation.py      仿真下位机（缓冲执行 G-code，虚拟末端运动）
    codec.py           协议编解码（仿真文本协议 + TCP 行协议）
    controller.py      会话层：整段下发、状态轮询、Status 解析（全字段）
  ui/
    canvas.py          画布（手绘/预览/末端标记）
    hand_tab.py / image_tab.py / gcode_tab.py / control_tab.py
    log_panel.py       日志
tests/smoke_test.py    离屏端到端冒烟测试（含 TCP mock 联调）
tests/tcp_mock_esp32.py ESP32 TCP 协议模拟器（测试用）
```

## 通信协议（TCP 后端，与 ESP32 固件对齐）

行文本，UTF-8，`\n` 分隔。上位机 → ESP32：

```
CMD:<name> [args]     start/stop/pause/resume/estop/clear_estop/setzero/return_zero/
                      pen_up/pen_down/jogx <v>/jogy <v>（v=mm/s，0=停止）/
                      jogm <电机> <rad/s>（单电机调试，0=停止）
GCODE:<n>             开始上传 n 行 G-code，随后 n 行裸 G-code 正文
STATUS                查询状态
CONFIG                查询配置
```

ESP32 → 上位机（每行一帧）：

```
R:{json}   命令/上传 ACK（ok/code/msg；code: 0成功 1无响应 2状态不允许 3STM32拒绝
           4未知命令 5未缓存G-code 6被占用 7未实现）
S:{json}   状态帧（st/stateName/busy/pen/fault/m1/m2/p/v/t/e/mo/rt/x/y/q/line/
           feeding/feedIdx/feedTotal/stale —— 覆盖 ESP32 可获取的全部信息）
C:{json}   配置帧（A4 尺寸/画板比例/留边/进给/点动速度范围）
```

- **单连接实例**：ESP32 同时只允许一个 TCP 客户端；TCP 占用期间网页端写接口（/api/ctl、/api/gcode）返回 code=6「TCP上位机占用中」，读接口（状态/配置）不受影响
- 上传 G-code 后点「整段下发并运行」会自动触发 `CMD:start`（ESP32 内部完成"下发首行→START→边画边下发"）
- 点动为**长按速度式**（按住移动、松开即停，按住期间 400ms 自动重发防 STM32 看门狗），与网页端行为一致；调试模式单电机正反转走 `CMD_JOG_MOTOR(0x0B)`，速度上限 5 rad/s（固件 config.h 集中定义）
- 抬笔/落笔走 `CMD_SET_PEN(0x0D)`：STM32 侧实现待确认，未实现时上位机会收到「无响应/拒绝」提示
- 协议细节与网页端 `/api/*` 对齐，见 `firmware/web_api.md` 第 8 章

## 占位协议（仿真后端，仅无硬件调试用）

仿真后端仍使用可读文本帧（`CMD:`/`?STATUS`/`SEG_BEGIN`/`SEG_DATA`/`SEG_END`/`ACK`/`ERR`/`STATUS:`），方便无硬件时跑通全链路。

## TODO（后续版本）

- [ ] 真实协议帧格式（帧头/长度/CRC/重传，若 TCP 线路上需要）
- [ ] 串口真机联调；断电/断线保护
- [ ] 多笔（换笔指令，协议已预留笔号概念）
- [ ] 执行轨迹回放（当前只有末端标记 + 已/未绘制分色）
- [ ] 断点/单步调试
- [ ] 工程文件保存/加载（图片+参数+G-code）
