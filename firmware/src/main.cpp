/**
 * @file main.cpp
 * @brief 绘画机器人 ESP32C3 主控: WiFi AP + Web 控制台 + STM32 通信
 *
 * 本文件是 ESP32 固件的核心入口文件，承担以下职责：
 *   1. 系统初始化：串口、WiFi AP 模式、Web 服务器
 *   2. STM32 下位机通信：帧收发、ACK 流控、G-code 下发
 *   3. HTTP RESTful API：状态轮询、控制命令、G-code 上传、配置下发
 *   4. 主循环调度：Web 客户端处理、STM32 帧接收、G-code 流式下发
 *
 * 硬件接线：
 *   ESP32 GPIO6 (RX) <- STM32 PB10 (USART3_TX)
 *   ESP32 GPIO7 (TX) -> STM32 PB11 (USART3_RX)
 *   GND 共地（必须连接，否则串口通信异常）
 *
 * 网络访问：
 *   - AP SSID: DrawingRobot（见 config.h）
 *   - AP 密码: 12345678
 *   - 网关 IP: 192.168.4.1（ESP32 softAP 默认地址）
 *   - Web 控制台: http://192.168.4.1
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <vector>
#include "protocol.h"
#include "webpage.h"
#include "config.h"

/* ================================================================
 * 全局对象
 * ================================================================ */
HardwareSerial stm32Serial(1);     /* UART1：与 STM32 通信的串口对象
                                      参数 1 表示使用 UART1（UART0 默认用于 USB CDC 调试）*/
WebServer server(80);             /* HTTP 服务器：监听 80 端口（标准 HTTP 端口）*/

/* ================================================================
 * 全局状态 - 机器人状态缓存
 * 这些变量保存最近一次从 STM32 收到的机器人状态
 * ================================================================ */
static RobotStatus_t g_status = {0};      /* 机器人完整状态结构体（详见 protocol.h）*/
static bool g_statusValid = false;        /* 状态有效性标记：true = 已至少收到一次状态帧 */
static uint32_t g_lastStatusMs = 0;       /* 最后一次收到状态帧的时间戳（ms），用于失联判定 */

/* ================================================================
 * 全局状态 - G-code 程序缓存与下发进度
 * 网页端通过 /api/gcode 上传 G-code 文本，在此处按行拆分缓存
 * ESP32 采用"ACK 流控逐行下发"策略，而非一次性全部推送
 * ================================================================ */
static std::vector<String> g_prog;        /* G-code 程序行缓存（每元素一行 G-code）*/
static size_t   g_feedIdx = 0;            /* 已下发（且收到 ACK_OK）的行数索引 */
static bool     g_feeding = false;        /* 流式下发开关：true = 正在后台逐行下发 */
static bool     g_endSent = false;        /* GCODE_END 结束标志帧是否已发送 */
static uint32_t g_feedRetryMs = 0;        /* ACK_QUEUE_FULL 后的重试时间戳（ms）
                                             收到队列满时延迟 60ms 再重发当前行 */

/* ================================================================
 * 全局状态 - 在途命令（等待 STM32 ACK）
 *
 * 设计思路：系统同时只允许一条命令"在途"（pending）。
 *   - blocking 模式：HTTP 线程同步等待 ACK（控制接口使用）
 *   - 非 blocking 模式：feeder 异步下发，主循环轮询 ACK（G-code 下发使用）
 * ================================================================ */
static struct {
  uint8_t  cmd;              /* 在途命令的命令码 */
  bool     waiting;          /* true = 正在等待 ACK 回复 */
  bool     blocking;         /* true = HTTP 阻塞等待模式
                                 false = feeder 异步下发模式 */
  uint32_t deadline;         /* ACK 超时截止时间戳（仅非阻塞模式使用）*/
  int      ackResult;        /* 收到的 ACK 结果码（阻塞模式使用）
                                 -1 = 超时未收到，0~3 = ACK_* */
} g_pending = {0, false, false, 0, -1};

/* ================================================================
 * STM32 链路层：帧接收与分发处理
 *
 * 该函数在指定时间窗口内持续读取串口，并将字节喂给协议解析状态机。
 * 解析到完整帧后根据帧类型分发：
 *   - CMD_STATUS：更新状态缓存
 *   - CMD_ACK：匹配到在途命令时更新 pending / feeder 状态
 *
 * @param maxMs 最大处理时长（毫秒），防止阻塞过长影响 Web 响应
 * ================================================================ */
static void stm32ProcessFrames(uint32_t maxMs)
{
  uint32_t t0 = millis();
  while ((uint32_t)(millis() - t0) <= maxMs) {
    while (stm32Serial.available()) {
      ProtoFrame_t f;
      /* 逐字节喂入状态机，解析到完整帧时返回 true */
      if (protoParseByte((uint8_t)stm32Serial.read(), f)) {
        /* 分支 1：状态上报帧 */
        if (f.cmd == CMD_STATUS) {
          if (protoParseStatus(f, g_status)) {
            g_statusValid = true;
            g_lastStatusMs = millis();
          }
        }
        /* 分支 2：ACK 应答帧
           匹配条件：存在在途命令 && ACK 长度足够 && ACK 的目标命令匹配 */
        else if (f.cmd == CMD_ACK && g_pending.waiting &&
                   f.len >= 2 && f.payload[0] == g_pending.cmd) {
          int result = f.payload[1];   /* ACK 结果码：ACK_OK/QUEUE_FULL/BAD_STATE/ERROR */

          /* 2a: 阻塞模式（HTTP 控制接口）*/
          if (g_pending.blocking) {
            g_pending.ackResult = result;  /* 保存结果供等待循环读取 */
            g_pending.waiting = false;     /* 解除等待 */
          }
          /* 2b: 非阻塞模式（feeder G-code 下发）*/
          else {
            g_pending.waiting = false;    /* 清除在途标记 */
            /* 根据命令类型分别处理 ACK 结果 */
            if (g_pending.cmd == CMD_GCODE_LINE) {
              if (result == ACK_OK) {
                /* STM32 已接收该行 → 推进下发索引，下一轮发送下一行 */
                g_feedIdx++;
              } else if (result == ACK_QUEUE_FULL) {
                /* STM32 队列已满 → 60ms 后重发同一行（不推进索引）*/
                g_feedRetryMs = millis() + 60;
              } else {
                /* ACK_BAD_STATE / ACK_ERROR → 停止下发并打印错误 */
                g_feeding = false;
                Serial.printf("[ERR] G-code line %u rejected, code=%d\n",
                              (unsigned)g_feedIdx, result);
              }
            } else if (g_pending.cmd == CMD_GCODE_END) {
              /* GCODE_END 帧已送达 → 标志整个下发流程结束 */
              g_feeding = false;
              g_endSent = false;   /* 重置 END 标志，为下一次绘画做准备 */
            }
          }
        }
      }
    }
    delay(1);   /* 无数据时短暂让出 CPU，防止忙等占满 CPU */
  }
}

/* ================================================================
 * 阻塞式命令发送（供 HTTP 控制接口使用）
 *
 * 执行流程：
 *   1. 等待 feeder 在途命令结束（最多 200ms），避免冲突
 *   2. 设置 pending 为阻塞等待模式
 *   3. 发送帧
 *   4. 持续调用 stm32ProcessFrames 等待 ACK，直到超时或收到
 *
 * @param  cmd       命令码
 * @param  payload   负载数据（可为 nullptr）
 * @param  len       负载长度
 * @param  timeoutMs 超时时间（毫秒）
 * @return ACK 结果码，或 -1 表示超时无响应
 * ================================================================ */
static int sendCtlWaitAck(uint8_t cmd, const uint8_t *payload, uint16_t len, uint32_t timeoutMs)
{
  /* 第 1 步：等待 feeder 的在途命令结束（最多 200ms）
     防止两个控制/下发命令同时在途，破坏 pending 单例模型 */
  uint32_t t0 = millis();
  while (g_pending.waiting && (uint32_t)(millis() - t0) < 200) {
    stm32ProcessFrames(20);
  }

  /* 第 2 步：设置在途命令为阻塞等待模式 */
  g_pending.cmd = cmd;
  g_pending.waiting = true;
  g_pending.blocking = true;
  g_pending.ackResult = -1;

  /* 第 3 步：发送协议帧 */
  protoSend(stm32Serial, cmd, payload, len);

  /* 第 4 步：循环等待 ACK 直到超时 */
  uint32_t sendMs = millis();
  while (g_pending.waiting && (uint32_t)(millis() - sendMs) < timeoutMs) {
    stm32ProcessFrames(20);
  }
  if (g_pending.waiting) {
    g_pending.waiting = false;   /* 超时退出，清除等待标记 */
    return -1;
  }
  return g_pending.ackResult;
}

/* ================================================================
 * G-code 流式下发调度器（非阻塞，在 loop 中周期性调用）
 *
 * 下发状态机：
 *   IDLE (g_feeding=false) → 等待开始
 *   SEND_LINE → 等待 ACK →
 *     OK       → feedIdx++, 继续下一行或发送 END
 *     QUEUE_FULL → 60ms 后重发同一行
 *     其他错误 → 终止下发
 *   SEND_END → 等待 END ACK → DONE
 *
 * 设计要点：
 *   - 与 STM32 ACK 流控配合，避免溢出 16 行的 G-code 队列
 *   - 超时重发机制保证串口偶发丢包不影响整体
 * ================================================================ */
static void feederTick()
{
  if (!g_feeding) return;         /* 未开始下发，直接退出 */

  /* 若有在途命令未确认 */
  if (g_pending.waiting) {
    /* 非阻塞模式（feeder 模式）的 ACK 超时处理 */
    if (!g_pending.blocking && (uint32_t)millis() >= g_pending.deadline) {
      g_pending.waiting = false;   /* 清除等待，下一轮会重发当前行 */
      Serial.println("[W] ACK timeout, will resend line");
    } else {
      return;                      /* 仍在等待或非 feeder 命令，暂不处理 */
    }
  }

  /* 全部程序行已成功下发完毕 */
  if (g_feedIdx >= g_prog.size()) {
    /* 发送 GCODE_END 帧：通知 STM32 后续无更多行，
       使其在"队列清空 + 收到 END"时正确判定绘画完成
       （避免流式下发过程中队列短暂为空导致误判完成）*/
    if (!g_endSent) {
      g_pending.cmd = CMD_GCODE_END;
      g_pending.waiting = true;
      g_pending.blocking = false;
      g_pending.ackResult = -1;
      g_pending.deadline = millis() + ACK_TIMEOUT_MS;
      protoSend(stm32Serial, CMD_GCODE_END, nullptr, 0);
      g_endSent = true;
      return;
    }
    /* END 帧 ACK 已收到 → 整个下发流程结束 */
    g_feeding = false;
    Serial.println("[i] G-code delivery complete");
    return;
  }

  /* ACK_QUEUE_FULL 退避时间未到 → 暂不发送 */
  if ((uint32_t)millis() < g_feedRetryMs) return;

  /* 正常发送当前行 */
  const String &line = g_prog[g_feedIdx];
  g_pending.cmd = CMD_GCODE_LINE;
  g_pending.waiting = true;
  g_pending.blocking = false;
  g_pending.ackResult = -1;
  g_pending.deadline = millis() + ACK_TIMEOUT_MS;
  protoSend(stm32Serial, CMD_GCODE_LINE, (const uint8_t *)line.c_str(), (uint16_t)line.length());
}

/* ================================================================
 * 阻塞下发首行（用于"开始绘画"前保证 STM32 队列非空）
 *
 * 为什么需要先下发首行？
 *   START_DRAW 命令要求 STM32 队列中有内容，否则绘画会立即结束。
 *   因此先阻塞式下发第 0 行并确认收到，再发 START_DRAW。
 *
 * @return ACK 结果码
 * ================================================================ */
static int feedOneLineBlocking()
{
  if (g_feedIdx >= g_prog.size()) return ACK_ERROR;   /* 无内容可发 */
  const String &line = g_prog[g_feedIdx];
  int r = sendCtlWaitAck(CMD_GCODE_LINE, (const uint8_t *)line.c_str(),
                         (uint16_t)line.length(), ACK_TIMEOUT_MS);
  if (r == ACK_OK) g_feedIdx++;   /* 成功则推进索引，后续 feeder 从下一行开始 */
  return r;
}

/* ================================================================
 * HTTP 接口定义
 *
 * 路由表：
 *   GET  /              → 控制台网页（INDEX_HTML）
 *   GET  /api/status    → 状态 JSON（网页端 200ms 轮询一次）
 *   GET  /api/config    → 集中参数 JSON（网页启动时拉取一次）
 *   POST /api/ctl       → 控制命令（body 为纯文本命令）
 *   POST /api/gcode     → G-code 上传（body 为 G-code 文本，\n 分行）
 * ================================================================ */

/**
 * @brief 根路径处理：返回网页控制台 HTML
 */
static void handleRoot()
{
  /* send_P 表示从 PROGMEM（Flash）发送，避免将大字符串拷贝到 RAM */
  server.send_P(200, "text/html", INDEX_HTML);
}

/**
 * @brief /api/status 处理：返回机器人完整状态 JSON
 *
 * 网页端每 200ms 轮询一次，用于实时刷新状态面板和画板上的绿色十字。
 */
static void handleStatus()
{
  char buf[640];   /* JSON 输出缓冲区（足够容纳完整状态）*/
  /* stale = 失联标记：从未收到状态 或 超过 STATUS_STALE_MS(1500ms) 未更新 */
  bool stale = !g_statusValid || ((uint32_t)(millis() - g_lastStatusMs) > STATUS_STALE_MS);

  /* 使用 snprintf 手动组装 JSON（不依赖 ArduinoJson 库，节省 Flash）*/
  snprintf(buf, sizeof(buf),
    "{\"st\":%u,\"stateName\":\"%s\",\"busy\":%u,\"pen\":%u,\"fault\":%u,"
    "\"m1\":{\"p\":%.2f,\"v\":%.2f,\"t\":%.2f,\"e\":%u,\"mo\":%u,\"rt\":%u},"
    "\"m2\":{\"p\":%.2f,\"v\":%.2f,\"t\":%.2f,\"e\":%u,\"mo\":%u,\"rt\":%u},"
    "\"x\":%.2f,\"y\":%.2f,\"q\":%u,\"line\":%u,"
    "\"feeding\":%s,\"feedIdx\":%u,\"feedTotal\":%u,\"stale\":%s}",
    /* 基础状态 */
    g_status.state, stateName(g_status.state), g_status.busy, g_status.pen, g_status.fault,
    /* 电机 1：位置/速度/扭矩/状态码/MOS温度/线圈温度 */
    g_status.m1.pos, g_status.m1.vel, g_status.m1.torque,
    g_status.m1.err, g_status.m1.t_mos, g_status.m1.t_rotor,
    /* 电机 2：同电机 1 */
    g_status.m2.pos, g_status.m2.vel, g_status.m2.torque,
    g_status.m2.err, g_status.m2.t_mos, g_status.m2.t_rotor,
    /* 末端位置/队列进度/下发进度 */
    g_status.x, g_status.y, g_status.queue_used, g_status.line_index,
    g_feeding ? "true" : "false", (unsigned)g_feedIdx, (unsigned)g_prog.size(),
    stale ? "true" : "false");
  server.send(200, "application/json", buf);
}

/**
 * @brief ACK 结果 → JSON 响应通用封装
 * @param r ACK 结果码（ACK_OK / ACK_BAD_STATE / ACK_ERROR / -1 超时）
 */
static void respondAck(int r)
{
  char buf[128];
  if (r == ACK_OK) {
    snprintf(buf, sizeof(buf), "{\"ok\":1,\"code\":0,\"msg\":\"ok\"}");
  } else if (r == ACK_BAD_STATE) {
    snprintf(buf, sizeof(buf), "{\"ok\":0,\"code\":2,\"msg\":\"当前状态不允许\"}");
  } else if (r == ACK_ERROR) {
    snprintf(buf, sizeof(buf), "{\"ok\":0,\"code\":3,\"msg\":\"STM32拒绝\"}");
  } else {
    /* -1 或其他值：视为 STM32 无响应（串口超时）*/
    snprintf(buf, sizeof(buf), "{\"ok\":0,\"code\":1,\"msg\":\"STM32无响应\"}");
  }
  server.send(200, "application/json", buf);
}

/**
 * @brief /api/ctl 处理：控制命令分发
 *
 * 请求 body 为纯文本（非 JSON），支持的命令详见 web_api.md。
 */
static void handleCtl()
{
  String body = server.arg("plain");
  body.trim();

  /* ================= 特殊命令 1：start（开始绘画）================= */
  if (body == "start") {
    /* 前置检查：G-code 程序缓存不能为空 */
    if (g_prog.empty()) {
      server.send(200, "application/json", "{\"ok\":0,\"code\":5,\"msg\":\"请先生成Gcode\"}");
      return;
    }
    /* 步骤 1：先阻塞式下发首行，保证 START_DRAW 时 STM32 队列非空 */
    int r = feedOneLineBlocking();
    if (r != ACK_OK) {
      respondAck(r);
      return;
    }
    /* 步骤 2：发送 START_DRAW 启动绘画 */
    int r2 = sendCtlWaitAck(CMD_START_DRAW, nullptr, 0, ACK_TIMEOUT_MS);
    /* 步骤 3：启动成功则打开 feeder 开关，后台自动流式下发剩余行 */
    if (r2 == ACK_OK) {
      g_feeding = true;
    }
    respondAck(r2);
    return;
  }

  /* ================= 特殊命令 2：笛卡尔点动（jogx/jogy）================= */
  if (body.startsWith("jogx") || body.startsWith("jogy")) {
    bool isX = body.startsWith("jogx");          /* true = X 轴，false = Y 轴 */
    String vs = body.substring(4);               /* 去除 "jogx"/"jogy" 前缀（4 字符）*/
    vs.trim();
    float v = (vs.length() > 0) ? vs.toFloat() : 0.0f;   /* 空 body 视为 0 = 停止 */
    /* 速度限幅：±JOG_SPEED_MAX_MM_S（±30mm/s = ±3cm/s）*/
    if (v > JOG_SPEED_MAX_MM_S) v = JOG_SPEED_MAX_MM_S;
    if (v < -JOG_SPEED_MAX_MM_S) v = -JOG_SPEED_MAX_MM_S;
    /* 将 float32 速度序列化为 4 字节小端 payload */
    uint8_t p[4];
    protoPutFloat(p, v);
    int r = sendCtlWaitAck(isX ? CMD_JOG_X : CMD_JOG_Y, p, 4, ACK_TIMEOUT_MS);
    respondAck(r);
    return;
  }

  /* ================= 通用命令映射（无 payload 的简单命令）================= */
  uint8_t cmd = 0;
  if (body == "stop")            cmd = CMD_STOP_DRAW;
  else if (body == "pause")       cmd = CMD_PAUSE_DRAW;
  else if (body == "resume")      cmd = CMD_RESUME_DRAW;
  else if (body == "estop")       cmd = CMD_ESTOP;
  else if (body == "clear_estop") cmd = CMD_CLEAR_ESTOP;
  else if (body == "setzero")     cmd = CMD_SET_ZERO;
  else if (body == "return_zero") cmd = CMD_RETURN_ZERO;
  else {
    server.send(400, "application/json", "{\"ok\":0,\"code\":4,\"msg\":\"unknown cmd\"}");
    return;
  }

  /* 停止 / 急停：除了下发命令，还要同步终止本地下发并清空缓存
     防止"已停止但还在下发 G-code"的状态不一致 */
  if (cmd == CMD_STOP_DRAW || cmd == CMD_ESTOP) {
    g_feeding = false;
    g_endSent = false;
    g_prog.clear();
    g_feedIdx = 0;
  }

  /* 下发通用命令并响应 */
  int r = sendCtlWaitAck(cmd, nullptr, 0, ACK_TIMEOUT_MS);
  respondAck(r);
}

/**
 * @brief /api/config 处理：返回集中配置参数 JSON
 *
 * 所有参数均来自 config.h，网页启动时拉取一次并覆盖默认值。
 * 修改 config.h 后无需修改网页代码即可自动同步。
 */
static void handleConfig()
{
  char buf[256];
  snprintf(buf, sizeof(buf),
    "{\"A4_W\":%.0f,\"A4_H\":%.0f,\"PX\":%.0f,\"MARGIN\":%.0f,\"FEED\":%.0f,"
    "\"jogMin\":%.0f,\"jogMax\":%.0f,\"jogDef\":%.0f}",
    A4_W_MM, A4_H_MM, CANVAS_PX_PER_MM, DRAW_MARGIN_MM, DRAW_FEED_MM_MIN,
    JOG_SPEED_MIN_MM_S, JOG_SPEED_MAX_MM_S, JOG_SPEED_DEF_MM_S);
  server.send(200, "application/json", buf);
}

/**
 * @brief /api/gcode 处理：接收并缓存 G-code 程序
 *
 * 请求 body：G-code 文本，每行一条，以 \n 分隔。
 * 注意：此处仅缓存，不会立即下发；用户点"开始绘画"后才开始流式下发。
 */
static void handleGcode()
{
  /* 安全检查：绘画进行中（RUNNING / PAUSED）禁止覆盖程序 */
  if (g_statusValid &&
      (g_status.state == STATE_RUNNING || g_status.state == STATE_PAUSED)) {
    server.send(200, "application/json",
                "{\"ok\":0,\"lines\":0,\"msg\":\"绘画进行中, 请先停止\"}");
    return;
  }

  String body = server.arg("plain");

  /* 清空旧程序与下发状态 */
  g_prog.clear();
  g_feedIdx = 0;
  g_feeding = false;
  g_endSent = false;

  /* 按换行符拆分，逐行 trim 后存入 g_prog */
  int count = 0;
  bool overflow = false;
  int start = 0;
  while (true) {
    int nl = body.indexOf('\n', start);
    String line = (nl < 0) ? body.substring(start) : body.substring(start, nl);
    line.trim();
    if (line.length() > 0) {
      /* 行数上限检查：防止内存溢出 */
      if (count >= MAX_PROG_LINES) { overflow = true; break; }
      g_prog.push_back(line);
      count++;
    }
    if (nl < 0) break;
    start = nl + 1;
  }

  /* 组装响应 JSON */
  char buf[160];
  snprintf(buf, sizeof(buf), "{\"ok\":%d,\"lines\":%d,\"msg\":\"%s\"}",
           overflow ? 0 : 1, count,
           overflow ? "超出最大行数" : "已缓存, 点击开始绘画后自动下发");
  server.send(200, "application/json", buf);

  /* 调试日志：USB CDC 串口输出 */
  if (!overflow && count > 0) {
    Serial.printf("[i] received %d gcode lines, buffered (start to feed)\n", count);
  }
}

/* ================================================================
 * Arduino 入口函数：setup() 上电初始化，仅执行一次
 * ================================================================ */
void setup()
{
  /* 1. USB CDC 调试串口初始化（波特率 115200，用于 PC 端查看日志）*/
  Serial.begin(115200);
  delay(500);     /* 等待 USB 枚举稳定 */
  Serial.println("\n===== Drawing Robot ESP32C3 =====");

  /* 2. STM32 通信串口初始化（UART1，115200-8N1，指定 RX/TX 引脚）*/
  stm32Serial.begin(STM32_BAUD, SERIAL_8N1, STM32_RX_PIN, STM32_TX_PIN);
  Serial.println("STM32 link: UART1 115200-8N1, TX=GPIO7 RX=GPIO6");

  /* 3. WiFi AP 模式启动 */
  WiFi.mode(WIFI_AP);                        /* 设置为 AP 模式（不连接路由器）*/
  if (!WiFi.softAP(AP_SSID, AP_PASS)) {
    Serial.println("[ERR] softAP failed");
  }
  Serial.printf("AP SSID: %s, pass: %s\n", AP_SSID, AP_PASS);
  Serial.printf("Web: http://%s\n", WiFi.softAPIP().toString().c_str());   /* 默认 192.168.4.1 */

  /* 4. HTTP 服务器路由注册与启动 */
  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/status", HTTP_GET, handleStatus);
  server.on("/api/config", HTTP_GET, handleConfig);
  server.on("/api/ctl", HTTP_POST, handleCtl);
  server.on("/api/gcode", HTTP_POST, handleGcode);
  server.begin();
  Serial.println("web server started");
}

/* ================================================================
 * Arduino 入口函数：loop() 主循环，反复执行
 *
 * 单次循环耗时约 2~3ms，保证：
 *   - Web 客户端请求及时响应
 *   - STM32 串口帧不丢失（2ms 窗口读取一次）
 *   - Feeder 及时推进下发（每轮都调用）
 * ================================================================ */
void loop()
{
  server.handleClient();               /* 处理 HTTP 请求（非阻塞）*/
  stm32ProcessFrames(2);               /* 接收 STM32 状态帧/ACK（最多 2ms）*/
  feederTick();                        /* G-code 流式下发调度 */
}
