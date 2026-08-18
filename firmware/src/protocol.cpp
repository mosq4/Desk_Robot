/**
 * @file protocol.cpp
 * @brief 板间通信协议实现 (与 STM32 侧 protocol.c 对应)
 *
 * 本文件实现 ESP32 <-> STM32 板间通信协议的全部功能，包括：
 *   1. CRC8 校验算法
 *   2. 字节流解析状态机（逐字节接收，帧同步定界）
 *   3. 帧组装与发送
 *   4. 阻塞式帧接收（带超时）
 *   5. 状态帧解析（44 字节 payload → RobotStatus_t）
 *   6. float32 小端序序列化/反序列化
 *   7. 状态码 → 字符串映射
 */
#include "protocol.h"
#include <string.h>

/* ================================================================
 * CRC8 校验算法
 * 多项式 poly = 0x07，初始值 init = 0x00
 * 与 STM32 端使用的 CRC8 算法完全一致，用于帧数据完整性校验
 *
 * 算法原理：逐字节异或后，对每一位进行多项式模 2 除法
 * ================================================================ */
uint8_t protoCrc8(const uint8_t *p, uint16_t len)
{
  uint8_t crc = 0x00;           /* CRC 寄存器初始值 */
  while (len--) {
    crc ^= *p++;                /* 先将当前字节与 CRC 异或 */
    /* 对 8 位逐位处理：如果最高位为 1，则左移后异或多项式 0x07 */
    for (uint8_t i = 0; i < 8; i++) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

/* ================================================================
 * 接收状态机
 *
 * 协议帧格式：
 *   [HEAD 1B] [CMD 1B] [LEN_LO 1B] [LEN_HI 1B] [payload LEN B] [CRC8 1B]
 *
 * 状态流转：
 *   PS_HEAD → PS_CMD → PS_LEN_LO → PS_LEN_HI → PS_PAYLOAD → PS_CRC
 *               ↑                                              ↓
 *               └──────────（出错/CRC 失败）──────────────────┘
 *
 * 使用静态变量保存解析上下文，因此同一时刻只能有一个解析流
 * ================================================================ */
typedef enum {
  PS_HEAD = 0,      /* 等待帧头 0xA5 */
  PS_CMD,           /* 接收命令码字节 */
  PS_LEN_LO,        /* 接收长度低字节 */
  PS_LEN_HI,        /* 接收长度高字节 */
  PS_PAYLOAD,       /* 接收 payload 数据 */
  PS_CRC            /* 接收并校验 CRC8 */
} ParseState_t;

static ParseState_t s_state = PS_HEAD;    /* 当前解析状态 */
static uint8_t  s_buf[PROTO_MAX_PAYLOAD + 3];  /* 接收缓冲区（CMD+LEN+payload）
                                                   [0]=CMD, [1]=LEN_LO, [2]=LEN_HI,
                                                   [3..3+s_len-1]=payload */
static uint16_t s_len = 0, s_idx = 0;     /* s_len = payload 总长度
                                             s_idx = 已接收 payload 字节数 */

/**
 * @brief  逐字节喂入解析状态机
 *
 * 该函数设计为在串口接收循环中被调用，每次传入一个字节。
 * 当完整帧解析成功时返回 true，out 输出帧数据。
 *
 * @param  b    接收到的一个字节
 * @param  out  输出帧（仅当返回 true 时有效）
 * @return true = 解析到完整帧；false = 继续等待
 */
bool protoParseByte(uint8_t b, ProtoFrame_t &out)
{
  switch (s_state) {
    /* 状态 1：等待帧头 0xA5 */
    case PS_HEAD:
      /* 只有收到 0xA5 才进入下一状态，其他字节全部丢弃
         这保证了即使字节流错位也能重新同步到下一帧开头 */
      if (b == PROTO_HEAD) s_state = PS_CMD;
      break;

    /* 状态 2：接收命令码（1 字节），存入 s_buf[0] */
    case PS_CMD:
      s_buf[0] = b;
      s_state = PS_LEN_LO;
      break;

    /* 状态 3：接收长度低字节（1 字节），存入 s_buf[1] */
    case PS_LEN_LO:
      s_buf[1] = b;
      s_len = b;                      /* 先暂存低字节 */
      s_state = PS_LEN_HI;
      break;

    /* 状态 4：接收长度高字节（1 字节），存入 s_buf[2] */
    case PS_LEN_HI:
      s_buf[2] = b;
      s_len |= (uint16_t)b << 8;      /* 与低字节组合成 16 位小端长度 */
      /* 长度合法性校验：超过最大 payload 直接丢弃，回到初始状态
         避免恶意或损坏的超长帧导致缓冲区溢出 */
      if (s_len > PROTO_MAX_PAYLOAD) { s_state = PS_HEAD; break; }
      s_idx = 0;                      /* payload 接收字节计数清零 */
      /* 根据 payload 长度决定下一状态：有数据则进入 payload 接收，否则直接到 CRC */
      s_state = (s_len > 0) ? PS_PAYLOAD : PS_CRC;
      break;

    /* 状态 5：接收 payload 数据（可变长度）*/
    case PS_PAYLOAD:
      s_buf[3 + s_idx] = b;           /* 依次存入 s_buf[3] 开始的位置 */
      if (++s_idx >= s_len) s_state = PS_CRC;  /* 收齐全部 payload 后进入 CRC 校验 */
      break;

    /* 状态 6：接收 CRC 字节并校验 */
    case PS_CRC:
    {
      /* 计算本地 CRC：覆盖 CMD + LEN(2B) + payload */
      uint8_t crc = protoCrc8(s_buf, 3 + s_len);
      s_state = PS_HEAD;              /* 无论成功失败都回到初始状态等待下一帧 */
      /* CRC 匹配：帧完整且正确，输出帧数据 */
      if (b == crc) {
        out.cmd = s_buf[0];
        out.len = s_len;
        if (s_len) memcpy(out.payload, &s_buf[3], s_len);
        return true;
      }
      /* CRC 不匹配：帧损坏，静默丢弃，不返回错误（调用方通过无返回感知）*/
      break;
    }
  }
  return false;
}

/* ================================================================
 * 帧组装与发送
 *
 * 将命令码、payload 组装成完整协议帧后写入 Stream
 * 帧格式：[0xA5] [CMD] [LEN_LO] [LEN_HI] [payload...] [CRC8]
 * ================================================================ */
void protoSend(Stream &s, uint8_t cmd, const uint8_t *payload, uint16_t len)
{
  /* 发送缓冲区：最大 = HEAD(1) + CMD(1) + LEN(2) + PROTO_MAX_PAYLOAD + CRC(1) */
  uint8_t buf[PROTO_MAX_PAYLOAD + 5];
  buf[0] = PROTO_HEAD;                 /* 字节 0：帧头 0xA5 */
  buf[1] = cmd;                        /* 字节 1：命令码 */
  buf[2] = (uint8_t)(len & 0xFF);      /* 字节 2：长度低字节（小端）*/
  buf[3] = (uint8_t)(len >> 8);        /* 字节 3：长度高字节（小端）*/
  if (len) memcpy(&buf[4], payload, len);  /* 字节 4 起：payload 数据 */
  /* CRC8 计算范围：CMD + LEN + payload（从 buf[1] 开始，共 3 + len 字节）*/
  buf[4 + len] = protoCrc8(&buf[1], 3 + len);
  /* 一次性写入完整帧（总长度 = 5 + len 字节）*/
  s.write(buf, 5 + len);
}

/* ================================================================
 * 阻塞式帧接收（带超时）
 *
 * 在指定时间内持续读取串口并喂给状态机，直到收到完整帧或超时
 * 注意：该函数会阻塞调用线程，仅适合初始化或非实时场景使用
 * ================================================================ */
bool protoRecvFrame(Stream &s, ProtoFrame_t &f, uint32_t timeoutMs)
{
  uint32_t t0 = millis();              /* 记录开始时间 */
  while (millis() - t0 <= timeoutMs) {  /* 在超时时间内循环 */
    while (s.available()) {             /* 尽可能多地读取串口缓冲区 */
      if (protoParseByte((uint8_t)s.read(), f)) return true;  /* 解析到完整帧立即返回 */
    }
    delay(1);                           /* 无数据时让出 CPU，避免忙等 */
  }
  return false;                         /* 超时未收到完整帧 */
}

/* ================================================================
 * CMD_STATUS 状态帧解析（44 字节 payload）
 *
 * 帧 payload 内存布局（与 STM32 端严格一致）：
 *   偏移  长度  字段
 *    0      1   state         - 状态机状态
 *    1      1   busy          - 过渡态标记
 *    2      1   pen           - 笔状态
 *    3     12   m1 (pos/vel/torque 各 4B float)
 *   15      3   m1 (err/t_mos/t_rotor 各 1B)
 *   18     12   m2 (pos/vel/torque 各 4B float)
 *   30      3   m2 (err/t_mos/t_rotor 各 1B)
 *   33      4   x (float, mm)
 *   37      4   y (float, mm)
 *   41      1   queue_used
 *   42      1   line_index
 *   43      1   fault
 * ================================================================ */
bool protoParseStatus(const ProtoFrame_t &f, RobotStatus_t &st)
{
  /* 合法性检查：必须是 CMD_STATUS 命令且 payload ≥ 44 字节 */
  if (f.cmd != CMD_STATUS || f.len < 44) return false;
  const uint8_t *p = f.payload;         /* p 作为移动指针逐步向后解析 */

  /* 字节 0~2：基础状态 */
  st.state = p[0];                      /* 状态机状态码 */
  st.busy  = p[1];                      /* 过渡态忙标记 */
  st.pen   = p[2];                      /* 抬/落笔状态 */
  p += 3;

  /* 字节 3~17：电机 1 信息（12B 浮点 + 3B 状态）*/
  st.m1.pos    = protoGetFloat(p); p += 4;   /* 位置 rad */
  st.m1.vel    = protoGetFloat(p); p += 4;   /* 速度 rad/s */
  st.m1.torque = protoGetFloat(p); p += 4;   /* 扭矩 N·m */
  st.m1.err = *p++; st.m1.t_mos = *p++; st.m1.t_rotor = *p++;  /* 状态码/温度 */

  /* 字节 18~32：电机 2 信息（结构同电机 1）*/
  st.m2.pos    = protoGetFloat(p); p += 4;
  st.m2.vel    = protoGetFloat(p); p += 4;
  st.m2.torque = protoGetFloat(p); p += 4;
  st.m2.err = *p++; st.m2.t_mos = *p++; st.m2.t_rotor = *p++;

  /* 字节 33~43：末端位置与队列进度 */
  st.x = protoGetFloat(p); p += 4;     /* 末端 X 坐标 mm */
  st.y = protoGetFloat(p); p += 4;     /* 末端 Y 坐标 mm */
  st.queue_used = *p++;                /* STM32 队列已用行数 */
  st.line_index = *p++;                /* 当前执行行号 */
  st.fault = *p;                       /* 故障码 */

  return true;
}

/* ================================================================
 * float32 小端序序列化 / 反序列化
 *
 * ESP32 (Xtensa / RISC-V) 和 STM32 (Cortex-M) 都是小端架构，
 * 因此直接 memcpy 即可，不需要字节序转换。
 * 使用 memcpy 而非强制类型转换是为了：
 *   1. 避开未对齐访问（某些架构上会触发总线错误）
 *   2. 避免 strict aliasing 规则导致的 UB
 * ================================================================ */

/**
 * @brief  将 float32 值以小端序写入 4 字节数组
 */
void protoPutFloat(uint8_t *p, float v)
{
  memcpy(p, &v, 4);
}

/**
 * @brief  从 4 字节小端数组读取 float32 值
 */
float protoGetFloat(const uint8_t *p)
{
  float v;
  memcpy(&v, p, 4);
  return v;
}

/* ================================================================
 * 状态码 → 英文名称映射
 * 用于：
 *   1. Serial 调试日志打印
 *   2. JSON 状态响应中的 stateName 字段
 * ================================================================ */
const char *stateName(uint8_t s)
{
  switch (s) {
    case STATE_INIT:      return "INIT";        /* 初始化 */
    case STATE_READY:     return "READY";       /* 就绪 */
    case STATE_RUNNING:   return "RUNNING";     /* 运行中 */
    case STATE_PAUSED:    return "PAUSED";      /* 暂停 */
    case STATE_JOG:       return "JOG";         /* 点动 */
    case STATE_EMERGENCY: return "EMERGENCY";   /* 急停 */
    case STATE_ERROR:     return "ERROR";       /* 故障 */
    case STATE_HOMING:    return "HOMING";      /* 回零中 */
    case STATE_ZEROING:   return "ZEROING";     /* 标定中 */
    default:              return "?";           /* 未知状态码 */
  }
}
