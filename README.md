# Desk Robot 绘图机器人

桌面双关节臂绘图机器人：上位机（PyQt5）负责轨迹编辑与指令下发，下位机（ESP32-C3）负责运动学解算与电机控制。

```
Desk Robot/
├── gui/        上位机（PyQt5，Python）—— 手绘/图片线稿/G-code/串口与仿真控制
│   ├── main.py 入口，app/ 核心与界面，tests/ 冒烟测试，cases/ 测试图片
│   └── README.md（上位机功能/协议/使用说明）
├── firmware/   下位机固件（ESP32-C3，PlatformIO）—— CAN 双关节臂电机控制
│   ├── src/    main.cpp / protocol / config / web
│   └── platformio.ini
├── docs/       参考资料（实习任务指导书等）
└── README.md
```

## 上位机（gui/）

```bash
cd gui
pip install -r requirements.txt
python main.py        # 或双击 run.bat
```

详见 [gui/README.md](gui/README.md)。

## 下位机固件（firmware/）

PlatformIO 工程（ESP32-C3），用 VS Code + PlatformIO 打开 `firmware/` 目录即可编译烧录。

- 上位机只发送**目标 xy 位置与指令**，运动学解算（逆解/可达性/插补）全部在下位机完成
- 抬笔机构为电机，上位机只发抬/落指令
- G-code 整段一次性下发（上位机 TCP 整段上传 ESP32，ESP32 按 STM32 ACK 流控逐行下发）
- 桌面上位机 ↔ ESP32 走 **TCP**（行协议，端口 8080，见 `firmware/web_api.md` 第 8 章）；网页端走 HTTP，与 TCP 互斥（单控制实例）
- 通信实现：上位机 `gui/app/comm/`（codec/tcp_transport/controller），固件 `firmware/src/main.cpp`（TCP 服务）