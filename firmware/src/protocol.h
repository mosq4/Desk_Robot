/**
 * @file protocol.h
 * @brief ESP32 <-> STM32 板间通信协议 (与 STM32 侧 protocol.h 一致)
 *
 * 帧格式: [0xA5][CMD 1B][LEN 2B 小端][payload][CRC8]
 * CRC8 (poly 0x07, init 0x00) 覆盖 CMD + LEN + payload。
 * 详见 STM32 工程根目录《板间通信协议.md》。
 *
 * 本协议头文件必须与 STM32 下位机中的 protocol.h 保持完全一致，
 * 否则会导致命令/响应解析错误。任何修改都需要同时更新两个工程。
 *
 * 通信方向定义：
 *   - 下行（Downlink）：ESP32 → STM32（命令下发）
 *   - 上行（Uplink）：STM32 → ESP32（状态上报、ACK 应答）
 */
#pragma once

#include <Arduino.h>

/* ================================================================
 * 协议帧基础常量
 * ================================================================ */
#define PROTO_HEAD          0xA5     /* 帧头字节：所有通信帧都以 0xA5 开头
                                        接收端用此字节进行帧同步和帧定界 */
#define PROTO_MAX_PAYLOAD   128      /* 单帧最大 payload 长度（字节）
                                        防止超长帧导致缓冲区溢出 */

/* ================================================================
 * 下行命令码（ESP32 -> STM32）
 * 命令码范围：0x00 ~ 0x7F（最高位为 0）
 * ================================================================ */
#define CMD_GCODE_LINE      0x01    /* G-code 行
                                       payload: ASCII 字符串，例如 "G1 X10 Y20 F1500" */
#define CMD_START_DRAW      0x02    /* 开始绘画命令
                                       前提：已下发至少一行 G-code 到 STM32 队列
                                       payload: 无 */
#define CMD_SET_ZERO        0x03    /* 设定零点（标定世界坐标系原点）
                                       将当前笔尖位置设为 (0, 0)
                                       状态变化：READY → ZEROING → READY（约 0.4s）
                                       payload: 无 */
#define CMD_STOP_DRAW       0x04    /* 停止绘画
                                       终止当前绘画，清空 STM32 队列，但不清除零点
                                       payload: 无 */
#define CMD_PAUSE_DRAW      0x05    /* 暂停绘画
                                       暂停队列执行，保持当前位置和上下文
                                       payload: 无 */
#define CMD_RESUME_DRAW     0x06    /* 继续绘画
                                       从暂停处继续执行队列
                                       payload: 无 */
#define CMD_RETURN_ZERO     0x07    /* 回到零点
                                       先抬笔，再运动回 (0, 0) 位置
                                       状态变化：READY → HOMING → READY
                                       payload: 无 */
#define CMD_ESTOP           0x08    /* 急停（紧急停止）
                                       立即切断电机 PWM，停止一切运动
                                       触发后进入 EMERGENCY 状态，需 CLEAR_ESTOP 解除
                                       payload: 无 */
#define CMD_JOG_X           0x09    /* 末端 X 方向点动速度
                                       payload: float32（4 字节小端），单位 mm/s
                                       正值向右，负值向左，0 = 停止该轴 */
#define CMD_JOG_Y           0x0A    /* 末端 Y 方向点动速度
                                       payload: float32（4 字节小端），单位 mm/s
                                       正值向上，负值向下，0 = 停止该轴 */
#define CMD_JOG_MOTOR       0x0B    /* 单电机点动（调试用，网页未使用）
                                       payload: [u8 电机号][float32 速度 rad/s]
                                       电机号：1 = M1，2 = M2 */
#define CMD_GET_STATUS      0x0C    /* 主动查询状态（当前未使用）
                                       STM32 收到后立即回复一帧 CMD_STATUS
                                       payload: 无
                                       注：当前实现中 STM32 会周期性主动上报状态 */
#define CMD_SET_PEN         0x0D    /* 直接设置抬/落笔（网页未使用，由 G-code 控制）
                                       payload: u8（0 = 抬笔，1 = 落笔） */
#define CMD_CLEAR_ESTOP     0x0E    /* 取消急停
                                       退出 EMERGENCY 状态，重新使能电机驱动器
                                       零点信息保持不变（无需重新标定）
                                       payload: 无 */
#define CMD_GCODE_END       0x0F    /* G-code 流结束标志
                                       通知 STM32：所有 G-code 行已发送完毕
                                       STM32 在"队列为空 且 收到 END"时才判定绘画完成
                                       防止流式下发过程中队列短暂为空导致误判
                                       payload: 无 */

/* ================================================================
 * 上行命令码（STM32 -> ESP32）
 * 命令码范围：0x80 ~ 0xFF（最高位为 1）
 * ================================================================ */
#define CMD_ACK             0x80    /* ACK 应答帧
                                       payload: [u8 对应命令码][u8 结果码]
                                       例如：ACK GCODE_LINE OK = [0x01, 0x00] */
#define CMD_STATUS          0x81    /* 机器人状态帧
                                       payload: 44 字节二进制（详见 protoParseStatus）
                                       STM32 周期性主动上报（约 50~100ms 一次）*/

/* ================================================================
 * ACK 结果码（ACK 帧 payload 的第二个字节）
 * ================================================================ */
#define ACK_OK              0      /* 命令执行成功 / 已接收 */
#define ACK_QUEUE_FULL      1      /* G-code 队列已满，请稍后重试
                                      ESP32 收到后会延迟 60ms 再重发当前行 */
#define ACK_BAD_STATE       2      /* 当前状态不允许执行该命令
                                      例如：在 RUNNING 状态下发 SET_ZERO */
#define ACK_ERROR           3      /* 其他错误（STM32 拒绝执行）*/

/* ================================================================
 * 机器人状态机（state 字段取值）
 * STM32 状态机定义，ESP32 仅用于展示和逻辑判断
 * ================================================================ */
#define STATE_INIT          0      /* 初始化态：上电后电机未使能，正在初始化 */
#define STATE_READY         1      /* 就绪态：空闲，可接收绘画/点动/回零等命令 */
#define STATE_RUNNING       2      /* 运行态：正在执行绘画队列 */
#define STATE_PAUSED        3      /* 暂停态：绘画暂停中，可继续或停止 */
#define STATE_JOG           4      /* 点动态：正在执行笛卡尔点动命令 */
#define STATE_EMERGENCY     5      /* 急停态：触发急停，电机失能，需 CLEAR_ESTOP */
#define STATE_ERROR         6      /* 故障态：电机故障/逆解失败等，需排查原因 */
#define STATE_HOMING        7      /* 回零中：正在执行 RETURN_ZERO 回零动作 */
#define STATE_ZEROING       8      /* 标定中：正在执行 SET_ZERO 零点标定动作 */

/* ================================================================
 * 数据结构定义
 * ================================================================ */

/**
 * @brief 协议帧结构体
 *
 * 用于存放解析后的一帧完整数据，或组装发送前的帧数据
 */
typedef struct {
  uint8_t  cmd;                           /* 命令码（1 字节）*/
  uint8_t  payload[PROTO_MAX_PAYLOAD];    /* 负载数据（变长，最大 128 字节）*/
  uint16_t len;                           /* 负载实际长度（字节）*/
} ProtoFrame_t;

/**
 * @brief 单电机信息结构体
 *
 * 每个电机的实时运行参数，来自电机驱动器反馈
 */
typedef struct {
  float    pos, vel, torque;   /* 位置(rad) / 角速度(rad/s) / 扭矩(N·m) */
  uint8_t  err;                /* 电机状态码：0=失能 1=使能 8~0x0E=故障 0xFF=无反馈 */
  uint8_t  t_mos;              /* MOS 管温度（摄氏度），来自驱动器内置温度传感器 */
  uint8_t  t_rotor;            /* 电机线圈温度（摄氏度），估算或直接测量值 */
} MotorInfo_t;

/**
 * @brief 机器人完整状态结构体
 *
 * STM32 上报的完整机器人状态，用于网页端实时展示
 */
typedef struct {
  uint8_t  state;              /* 当前状态机状态，见 STATE_* 宏定义 */
  uint8_t  busy;               /* 过渡态标记：1=处于回零/标定等过渡过程中 */
  uint8_t  pen;                /* 笔状态：0=抬笔，1=落笔 */
  MotorInfo_t m1, m2;          /* 电机 1 / 电机 2 的详细信息 */
  float    x, y;               /* 运动学正解得到的末端实际位置(mm)，相对零点
                                    X：平行 A4 短边，右为正
                                    Y：平行 A4 长边，向上为正 */
  uint8_t  queue_used;         /* STM32 端 G-code 队列已用行数（最大 16）*/
  uint8_t  line_index;         /* 当前正在执行的 G-code 行号（从 0 开始）*/
  uint8_t  fault;              /* 故障码：0=无，1=逆解失败，2=电机故障 */
} RobotStatus_t;

/* ================================================================
 * 公共函数接口声明
 * ================================================================ */

/**
 * @brief  计算 CRC8 校验值
 * @param  p   数据指针
 * @param  len 数据长度（字节）
 * @return CRC8 结果（poly=0x07, init=0x00）
 */
uint8_t  protoCrc8(const uint8_t *p, uint16_t len);

/**
 * @brief  单字节解析状态机（逐字节喂入）
 * @param  b   输入的一个字节
 * @param  out 解析完成时输出的完整帧
 * @return true = 解析到完整帧，out 有效；false = 帧不完整
 */
bool     protoParseByte(uint8_t b, ProtoFrame_t &out);

/**
 * @brief  组帧并通过 Stream 发送
 * @param  s       输出流对象（HardwareSerial 等）
 * @param  cmd     命令码
 * @param  payload 负载数据（可为 nullptr）
 * @param  len     负载长度（字节）
 */
void     protoSend(Stream &s, uint8_t cmd, const uint8_t *payload, uint16_t len);

/**
 * @brief  阻塞接收一帧（带超时）
 * @param  s          输入流对象
 * @param  f          输出帧
 * @param  timeoutMs  超时时间（毫秒）
 * @return true = 收到完整帧；false = 超时
 */
bool     protoRecvFrame(Stream &s, ProtoFrame_t &f, uint32_t timeoutMs);

/**
 * @brief  解析 CMD_STATUS 状态帧（44 字节 payload）
 * @param  f  输入帧（cmd 应为 CMD_STATUS）
 * @param  st 输出的机器人状态结构体
 * @return true = 解析成功；false = 帧类型错误或长度不足
 */
bool     protoParseStatus(const ProtoFrame_t &f, RobotStatus_t &st);

/**
 * @brief  将 float32 以小端序写入字节数组
 * @param  p  目标字节数组指针（至少 4 字节）
 * @param  v  待写入的浮点值
 */
void     protoPutFloat(uint8_t *p, float v);

/**
 * @brief  从小端字节数组读取 float32
 * @param  p  源字节数组指针（至少 4 字节）
 * @return 解析出的浮点值
 */
float    protoGetFloat(const uint8_t *p);

/**
 * @brief  状态码 → 字符串名称（用于调试和网页显示）
 * @param  s  状态码（STATE_* 宏定义值）
 * @return 状态名称字符串（如 "RUNNING"、"READY" 等）
 */
const char *stateName(uint8_t s);
