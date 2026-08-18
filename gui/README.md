# Desk Robot 绘图机器人上位机（v0.2）

PyQt5 上位机：手绘轨迹、图片转线稿（6 种提取算法）、路径规划、G-code 生成、整段下发、仿真/串口通信、末端位置可视化与已/未绘制分色。

## 分工约定（与下位机）

- 上位机只发送**目标 xy 位置与指令**；运动学解算（逆解/可达性/插补）全部在下位机完成
- 抬笔机构为电机，上位机只发抬/落指令，不做参数调整
- **G-code 整段一次性下发**：上位机实际分块传输（分包 + ACK + 超时中止 = 流量控制），下位机缓冲执行
- 暂停/恢复/停止/急停是**下位机命令**，不是上位机停发
- **通信协议留空**：见下文「占位协议」，定稿后只改 `app/comm/codec.py`

## 运行

```bash
pip install -r requirements.txt
python main.py        # 或双击 run.bat
```

依赖：PyQt5 / pyserial / numpy / opencv-python。无硬件时选**仿真后端**即可跑通全链路。

## 功能

| 模块 | 说明 |
|---|---|
| 手绘 | 画布鼠标绘制（毫米坐标，默认横置 A4 297×210mm，启动自动填满窗口）、撤销/清空、Chaikin 平滑、笔画排序（贪心最近邻 + 2-opt，优先画相邻笔画、减少抬笔空走）、缩放平移（工具条/Ctrl+±/Ctrl+0） |
| 图片线稿 | 导入图片、方向调节（左/右转90°、水平/垂直翻转、重置）、6 种提取模式（二值化/Canny/自适应阈值/黑帽细线/Sobel 梯度/XDoG 线条）、模糊、轮廓简化、最小面积、反色、自动缩放居中；调参实时预览（灰色、100ms 防抖），预览期间自动隐藏旧蓝色线稿，「生成线稿」替换当前轨迹 |
| G-code | 从轨迹生成（G0 空走 + M3/M5 抬落笔 + G1 绘制，默认自动优化笔画顺序）、保存/复制/直接编辑 |
| 控制 | 仿真/串口后端、jog 八方向、电机使能/失能、设零点/回零、整段下发并运行、暂停/继续/停止/急停、绘制速度 |
| 监视 | 状态轮询（100ms = 10Hz）：末端 xy、状态机、电机状态、执行行号、缓冲剩余；画布实时末端十字标记，执行中轨迹按 已绘制(蓝)/未绘制(灰) 分色；收发日志（高频状态回报不刷日志） |

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
    simulation.py      仿真下位机（缓冲执行 G-code，虚拟末端运动）
    codec.py           ★ 占位协议，定稿后替换这里
    controller.py      会话层：整段下发(分包+ACK)、状态轮询、Status 解析
  ui/
    canvas.py          画布（手绘/预览/末端标记）
    hand_tab.py / image_tab.py / gcode_tab.py / control_tab.py
    log_panel.py       日志
tests/smoke_test.py    离屏端到端冒烟测试
```

## 占位协议（协议留空，临时文本帧）

上位机 → 下位机：

```
CMD:<name> [args]     ENABLE/DISABLE/ZERO/HOME/PEN_UP/PEN_DOWN/PAUSE/RESUME/STOP/ESTOP/START/JOG <dx> <dy>/FEED <mm/min>
?STATUS               状态查询
SEG_BEGIN <n>         整段开始（n=总行数）
SEG_DATA <k>          k 行 G-code 分块（k 行正文紧跟其后）
SEG_END               整段结束，下位机开始缓冲执行
```

下位机 → 上位机：`ACK` / `ERR <msg>` / `STATUS:state=...;x=...;y=...;motors=...;line=...;buf=...;pen=...`

协议定稿（帧头/长度/CRC16 等）后，重写 `codec.py` 的 `encode_*` 与 `decode_line` 即可，上层与界面不动。

## TODO（后续版本）

- [ ] 真实协议帧格式（帧头/长度/CRC/重传）
- [ ] 串口真机联调；断电/断线保护
- [ ] 多笔（换笔指令，协议已预留笔号概念）
- [ ] 执行轨迹回放（当前只有末端标记 + 已/未绘制分色）
- [ ] 断点/单步调试
- [ ] 工程文件保存/加载（图片+参数+G-code）
