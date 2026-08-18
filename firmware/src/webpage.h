/**
 * @file webpage.h
 * @brief 网页控制台 (A4竖放画板 -> 世界坐标G-code, 控制按钮, 状态展示)
 *
 * 挂在 ESP32 AP (192.168.4.1) 上的单页应用，采用纯原生 HTML + CSS + JavaScript 实现
 * （不依赖任何前端框架），以保证在 ESP32 有限的 Flash/RAM 资源下正常运行。
 *
 * 页面布局（左右两栏 flex 布局，自动换行适配手机窄屏）：
 *   左栏 - 手绘画板：
 *     - 画板比例 = 竖放 A4 纸 (210x297mm, 2px/mm)，初始尺寸 420x594 像素
 *     - 灰色虚线框为四周留边 10mm 的可绘画区域边界
 *     - 橙色线条为用户手绘笔画
 *     - 绿色十字 + 圆圈 = 机器人末端实际位置（由 /api/status 实时更新）
 *   右栏 - 控制与状态区：
 *     - 方向键 D-Pad（▲▼◀▶）+ 停止键：笛卡尔点动控制，按住移动松开停止
 *     - 速度滑杆：点动速度 5~30 mm/s，默认 10 mm/s
 *     - 控制按钮：开始绘画 / 停止 / 暂停 / 继续 / 急停 / 取消急停 / 回零 / 设置零点
 *     - 状态面板：状态机、抬落笔、双电机参数、末端位置、队列进度、下发进度
 *
 * 坐标系约定（世界坐标系 mm）：
 *   - 原点 (0,0) = A4 纸正中心，用户将笔尖手动移到此位置后点"设置零点"标定
 *   - X 轴：平行 A4 短边，向右为正（范围约 -105 ~ +105 mm）
 *   - Y 轴：平行 A4 长边，向上为正（范围约 -148.5 ~ +148.5 mm）
 *   - 网页画板左上像素 (0,0) 对应世界坐标 (-105, 148.5) 左右
 *
 * G-code 生成规则：
 *   - 每一笔独立笔画：第一点用 G0（抬笔快速移动），后续点用 G1（落笔直线插补）
 *   - 每行末尾附加 F<进给> 指定速度（默认 1500 mm/min = 25 mm/s）
 *
 * 安全机制：
 *   - 点动看门狗：STM32 有 1s 看门狗，方向键按住时网页每 400ms 重发指令
 *     网页断线 / 浏览器崩溃 → 看门狗超时 → STM32 自动停止点动
 *   - 按钮可用性：仅在 READY(1)/JOG(4) 状态下启用点动和回零按钮，其余状态置灰
 *
 * 存储说明：
 *   - 整段 HTML 字符串存放在 PROGMEM（Flash 只读存储区），不占用宝贵的 RAM
 *   - ESP32 启动时 HTTP 根路径直接以 send_P 方式从 Flash 流式输出
 *
 * 依赖 ESP32 后端接口：
 *   - GET  /api/config   → 获取画板/速度等集中参数
 *   - GET  /api/status   → 200ms 轮询机器人状态
 *   - POST /api/gcode    → 上传 G-code 程序
 *   - POST /api/ctl      → 下发控制命令
 */
#pragma once

/* INDEX_HTML 是一个 PROGMEM 字符串（存储于 Flash），包含完整的单页 Web 应用
   R"rawliteral(...)rawliteral" 是 C++11 的原始字符串字面量语法，
   避免 HTML/JS 中的引号、反斜杠需要转义 */
const char INDEX_HTML[] PROGMEM = R"rawliteral(<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>绘画机器人控制台</title>
<style>
/* ===== 全局重置与基础样式 ===== */
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI","Microsoft YaHei",Arial,sans-serif;background:#f2f4f7;color:#222;padding:12px}
h1{font-size:20px;margin-bottom:10px;text-align:center}
/* ===== 两栏 flex 布局容器 ===== */
.wrap{display:flex;flex-wrap:wrap;gap:12px;justify-content:center}
/* ===== 面板卡片样式（白色圆角阴影）===== */
.panel{background:#fff;border-radius:8px;padding:12px;box-shadow:0 1px 4px rgba(0,0,0,.12)}
/* ===== 画板画布样式 ===== */
canvas{border:1px solid #bbb;border-radius:6px;background:#fff;touch-action:none;display:block;max-width:100%}
.hint{font-size:12px;color:#888;margin-top:4px}
.btns{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
/* ===== 通用按钮样式 ===== */
button{border:none;border-radius:6px;padding:10px 14px;font-size:14px;cursor:pointer;background:#e8edf3;color:#222}
button:active{filter:brightness(.92)}
button.primary{background:#1a73e8;color:#fff}    /* 蓝色：主要操作（开始绘画、生成Gcode）*/
button.danger{background:#d93025;color:#fff}     /* 红色：危险操作（急停）*/
button.warn{background:#f29900;color:#fff}       /* 橙色：警告操作（取消急停）*/
button.cal{background:#0b8043;color:#fff}        /* 绿色：标定操作（设置零点）*/
button:disabled{opacity:.45;cursor:not-allowed}
/* ===== 方向键 D-Pad 样式 ===== */
.dpad{display:inline-block;margin-top:10px}
.drow{display:flex;gap:6px;justify-content:center;margin:3px 0}
.dpad button{width:60px;height:48px;font-size:18px;touch-action:none;user-select:none;-webkit-user-select:none}
.dpad .dir{background:#dbe4f0}                    /* 浅蓝：方向键 */
.dpad span{width:60px;display:inline-block}       /* 占位用（使 D-Pad 呈十字布局）*/
#jStop{background:#f5d8d4;color:#b3261e}         /* 浅红：停止键 */
/* ===== 状态面板样式 ===== */
#statusBox{font-size:13px;margin-top:10px;line-height:2}
#msg{margin-top:8px;font-size:13px;color:#b3261e;min-height:1.3em;max-width:500px}
.k{color:#777}                                    /* 键名灰色辅助色 */
/* ===== 状态徽章（不同状态不同背景色）===== */
.state-badge{display:inline-block;padding:2px 10px;border-radius:10px;background:#e0e7ef;font-weight:600}
.st-RUNNING{background:#d2f0d8;color:#137333}    /* 绿色：运行中 */
.st-EMERGENCY,.st-ERROR{background:#fbdcd9;color:#b3261e}  /* 红色：急停/故障 */
.st-READY{background:#d7e6fb;color:#1a56b0}      /* 蓝色：就绪 */
.st-PAUSED{background:#fdeecd;color:#b06d00}     /* 黄色：暂停 */
</style>
</head>
<body>
<h1>绘画机器人控制台</h1>
<div class="wrap">
  <!-- 左栏：画板 + 手绘控制 -->
  <div class="panel">
    <canvas id="cv" width="420" height="594"></canvas>
    <div class="hint">画板=竖放A4纸(210x297mm)，虚线框内为可绘画区域(四周留边10mm)，绿色十字=机器人末端实际位置</div>
    <div class="btns">
      <button class="primary" id="btnGen">生成Gcode</button>
      <button id="btnClr">清空画布</button>
    </div>
    <div id="msg"></div>
  </div>
  <!-- 右栏：点动方向键 + 控制按钮 + 状态展示 -->
  <div class="panel" style="min-width:340px">
    <div class="dpad">
      <div class="drow"><span></span><button class="dir" id="jUp">▲</button><span></span></div>
      <div class="drow"><button class="dir" id="jLeft">◀</button><button id="jStop">■</button><button class="dir" id="jRight">▶</button></div>
      <div class="drow"><span></span><button class="dir" id="jDown">▼</button><span></span></div>
    </div>
    <div class="hint" style="text-align:center">笛卡尔点动: 按住移动松开停止 · 速度
      <input type="range" id="jogSpeed" min="5" max="30" step="5" value="10" style="vertical-align:middle;width:130px">
      <span id="jogSpeedVal">10</span> mm/s (上限3cm/s) · 绿色十字到纸中心后点"设置零点"</div>
    <div class="btns">
      <button class="primary" id="btnStart">开始绘画</button>
      <button id="btnStop">停止绘画</button>
      <button id="btnPause">暂停</button>
      <button id="btnResume">继续</button>
      <button class="danger" id="btnEstop">急停</button>
      <button class="warn" id="btnClearEstop">取消急停</button>
      <button id="btnReturnZero">回零</button>
      <button class="cal" id="btnSetZero">设置零点</button>
    </div>
    <div id="statusBox">连接中...</div>
  </div>
</div>
<script>
'use strict';
/* =====================================================================
 * 配置参数区：CFG 保存画板比例/留边/进给/点动范围等
 * 启动时通过 loadCfg() 从 ESP32 /api/config 拉取，拉取失败则使用下方默认值
 * ===================================================================== */
let CFG={A4_W:210,A4_H:297,PX:2,MARGIN:10,FEED:1500,jogMin:5,jogMax:30,jogDef:10};

/* =====================================================================
 * DOM 引用与手绘数据结构
 *   strokes: 二维数组，每元素是一笔（多点数组），例如 [[{x,y}...], [{x,y}...]]
 *   cur:     当前正在绘制的那一笔的引用（pointerdown 时创建，pointerup 时置 null）
 * ===================================================================== */
const CV=document.getElementById('cv'), CTX=CV.getContext('2d');
const strokes=[];
let cur=null;

/* 数字格式化：保留两位小数，用于 G-code 坐标输出 */
function fnum(v){return v.toFixed(2);}

/* =====================================================================
 * 坐标变换函数
 *  画板像素坐标系（左上原点，X右Y下）<-> 世界坐标系（纸中心原点，X右Y上）
 * ===================================================================== */
/* 画板像素 -> 世界坐标 mm (纸中心为零点, X右正 Y上正) */
function toMM(p){
  return {x:(p.x-CV.width/2)/CFG.PX, y:(CV.height/2-p.y)/CFG.PX};
}
/* 世界坐标 mm -> 画板像素 */
function toPX(x,y){return {x:CV.width/2+x*CFG.PX, y:CV.height/2-y*CFG.PX};}

/* =====================================================================
 * 指针事件坐标获取
 *  考虑 CSS 缩放：实际画布尺寸可能因窗口宽度而缩小，需要按比例换算
 * ===================================================================== */
function getPos(e){
  const r=CV.getBoundingClientRect();   /* 适配 CSS 缩放 */
  return {x:(e.clientX-r.left)*(CV.width/r.width), y:(e.clientY-r.top)*(CV.height/r.height)};
}

/* =====================================================================
 * 画板重绘（每次指针移动/状态轮询后调用）
 *  绘制顺序（后画的在上层）：1.可绘画区虚线框 2.用户手绘笔画 3.绿色十字标记
 * @param mx,my  机器人末端实际像素位置（不传则不画十字）
 * ===================================================================== */
function redraw(mx,my){
  CTX.clearRect(0,0,CV.width,CV.height);
  /* 1. 可绘画区域参考线 (四周留边 CFG.MARGIN mm) */
  CTX.strokeStyle='#c9d4e0'; CTX.lineWidth=1; CTX.setLineDash([6,6]);
  CTX.strokeRect(CFG.MARGIN*CFG.PX, CFG.MARGIN*CFG.PX,
                 CV.width-2*CFG.MARGIN*CFG.PX, CV.height-2*CFG.MARGIN*CFG.PX);
  CTX.setLineDash([]);
  /* 2. 手绘笔画（橙色粗线）*/
  CTX.strokeStyle='#e8710a'; CTX.lineWidth=2; CTX.lineCap='round'; CTX.lineJoin='round';
  for(const s of strokes){
    if(!s.length) continue;
    if(s.length<2){CTX.beginPath();CTX.arc(s[0].x,s[0].y,2,0,6.28);CTX.fill();continue;}
    CTX.beginPath(); CTX.moveTo(s[0].x,s[0].y);
    for(let i=1;i<s.length;i++) CTX.lineTo(s[i].x,s[i].y);
    CTX.stroke();
  }
  /* 3. 末端实际位置标记（绿色十字 + 圆圈）*/
  if(mx!==undefined){
    CTX.strokeStyle='#137333'; CTX.lineWidth=2;
    CTX.beginPath(); CTX.moveTo(mx-8,my); CTX.lineTo(mx+8,my);
    CTX.moveTo(mx,my-8); CTX.lineTo(mx,my+8); CTX.stroke();
    CTX.beginPath(); CTX.arc(mx,my,5,0,6.28); CTX.stroke();
  }
}

/* =====================================================================
 * 手绘指针事件（统一使用 Pointer Events，同时兼容鼠标/触摸/触控笔）
 *  pointerdown: 开始新一笔 → setPointerCapture 捕获指针保证离开画布也能收到事件
 *  pointermove: 距离阈值 >3 像素才追加采样点（避免过密点造成 G-code 行数爆炸）
 *  pointerup/cancel: 结束当前笔
 * ===================================================================== */
function endStroke(){cur=null;}
CV.addEventListener('pointerdown',e=>{
  CV.setPointerCapture(e.pointerId);
  cur=[getPos(e)];
  strokes.push(cur); redraw();
});
CV.addEventListener('pointermove',e=>{
  if(!cur) return;
  const p=getPos(e);
  const last=cur[cur.length-1];
  if(Math.hypot(p.x-last.x,p.y-last.y)>3){cur.push(p); redraw();}
});
CV.addEventListener('pointerup',endStroke);
CV.addEventListener('pointercancel',endStroke);
document.getElementById('btnClr').onclick=()=>{strokes.length=0; redraw();};

/* =====================================================================
 * 通用工具函数
 * ===================================================================== */
/* 消息提示：在画板下方显示操作结果，ok=true 绿色，false 红色 */
function say(m,ok){const el=document.getElementById('msg');el.textContent=m;el.style.color=ok?'#137333':'#b3261e';}
/* POST 纯文本到指定 URL，返回解析后的 JSON */
async function postText(url,body){const r=await fetch(url,{method:'POST',body:body});return r.json();}

/* =====================================================================
 * "生成Gcode"按钮处理
 *  将手绘笔画数组转换为世界坐标 G-code：
 *    第一点 → G0 抬笔快速移动到起点
 *    后续点 → G1 落笔直线插补
 *  然后 POST 到 /api/gcode 缓存到 ESP32
 * ===================================================================== */
document.getElementById('btnGen').onclick=async()=>{
  if(!strokes.length){say('请先在画板上手绘图案',false);return;}
  const lines=[];
  for(const s of strokes){
    if(!s.length) continue;
    const p0=toMM(s[0]);
    lines.push('G0 X'+fnum(p0.x)+' Y'+fnum(p0.y)+' F'+CFG.FEED);
    for(let i=1;i<s.length;i++){
      const p=toMM(s[i]);
      lines.push('G1 X'+fnum(p.x)+' Y'+fnum(p.y)+' F'+CFG.FEED);
    }
  }
  try{
    const res=await postText('/api/gcode',lines.join('\n'));
    say(res.ok?('已缓存 '+res.lines+' 行 Gcode，点击"开始绘画"后自动下发'):('失败: '+res.msg),!!res.ok);
  }catch(e){say('网络错误',false);}
};

/* =====================================================================
 * 控制命令通用封装（POST /api/ctl + 消息提示）
 * ===================================================================== */
async function ctl(cmd,okMsg){
  try{
    const res=await postText('/api/ctl',cmd);
    say(res.ok?(okMsg||('['+cmd+'] 成功')):('['+cmd+'] 失败: '+res.msg),!!res.ok);
  }catch(e){say('网络错误',false);}
}
/* 各按钮绑定对应命令 */
document.getElementById('btnStart').onclick=()=>ctl('start','绘画已开始');
document.getElementById('btnStop').onclick=()=>ctl('stop');
document.getElementById('btnPause').onclick=()=>ctl('pause');
document.getElementById('btnResume').onclick=()=>ctl('resume');
document.getElementById('btnEstop').onclick=()=>ctl('estop');
document.getElementById('btnClearEstop').onclick=()=>ctl('clear_estop','急停已取消, 电机重新使能');
document.getElementById('btnReturnZero').onclick=()=>ctl('return_zero','回零中...');
document.getElementById('btnSetZero').onclick=()=>ctl('setzero','零点已标定到当前笔尖位置(约0.4s完成)');

/* =====================================================================
 * 方向键点动控制
 *  核心安全机制：按住期间每 400ms 周期重发速度指令，配合 STM32 端 1s 看门狗。
 *    若浏览器崩溃/断网，看门狗超时自动停止，防止失控。
 *  实现：
 *    startJog() → 立即发送一次 + 启动 400ms 定时器循环发送
 *    stopJog()  → 清除定时器 + 发送速度 0 指令停止该轴
 *    stopAllJog() → 同时停止两轴（停止按钮用）
 * ===================================================================== */
let JOG_SPEED=10;                   /* 点动速度 mm/s, 由滑杆设置 (5~30) */
let jogTimer=null;
/* 发送单轴速度指令：dir=+1 正方向 / -1 负方向 / 0 停止 */
function jogSend(axis,dir){         /* dir: +1/-1/0=停止 */
  const v=(dir>0)?JOG_SPEED:((dir<0)?-JOG_SPEED:0);
  postText('/api/ctl','jog'+axis+' '+v).then(res=>{
    /* code===2 (BAD_STATE) 不算错误（比如绘画中点动本来就被禁用）*/
    if(!res.ok && res.code!==2) say('点动失败: '+res.msg,false);
  }).catch(()=>{});
}
/* 按住开始点动 */
function startJog(axis,dir){
  jogSend(axis,dir);
  if(jogTimer) clearInterval(jogTimer);
  jogTimer=setInterval(()=>jogSend(axis,dir),400);   /* 保持指令, 防止看门狗停止 */
}
/* 松开停止单轴 */
function stopJog(axis){
  if(jogTimer){clearInterval(jogTimer);jogTimer=null;}
  jogSend(axis,0);
}
/* 紧急停止两轴 */
function stopAllJog(){
  if(jogTimer){clearInterval(jogTimer);jogTimer=null;}
  jogSend('x',0); jogSend('y',0);
}
/* 绑定单个方向键的 pointer 事件 */
function bindDir(btn,axis,dir){
  btn.addEventListener('pointerdown',e=>{
    e.preventDefault();
    btn.setPointerCapture(e.pointerId);
    startJog(axis,dir);
  });
  btn.addEventListener('pointerup',()=>stopJog(axis));
  btn.addEventListener('pointercancel',()=>stopJog(axis));
}
/* 方向绑定：▲=Y+ 朝向纸面上方, ▼=Y- 朝向纸面下方, ◀=X- 向左, ▶=X+ 向右 */
bindDir(document.getElementById('jUp'),'y',+1);      /* ▲ 纸面上方向 = Y+ */
bindDir(document.getElementById('jDown'),'y',-1);
bindDir(document.getElementById('jLeft'),'x',-1);
bindDir(document.getElementById('jRight'),'x',+1);
document.getElementById('jStop').onclick=stopAllJog;
/* 点动速度滑杆事件：实时更新 JOG_SPEED 和数值显示 */
const jogSpeedEl=document.getElementById('jogSpeed');
jogSpeedEl.addEventListener('input',()=>{
  JOG_SPEED=parseFloat(jogSpeedEl.value);
  document.getElementById('jogSpeedVal').textContent=String(JOG_SPEED);
});

/* =====================================================================
 * 200ms 轮询状态（/api/status）
 *  功能：
 *    1. 更新状态面板 HTML（状态机/抬落笔/电机参数/位置/进度）
 *    2. 根据状态动态启用/禁用方向键与回零按钮
 *    3. 取出末端 x/y 映射为像素后传给 redraw() 显示绿色十字
 * ===================================================================== */
const ST_NAME={'INIT':'初始化','READY':'就绪','RUNNING':'绘画中','PAUSED':'暂停','JOG':'点动',
               'EMERGENCY':'急停','ERROR':'故障','HOMING':'回零中','ZEROING':'标定中'};
/* 电机值显示：err===255 表示无反馈数据，显示 "--" 代替数值 */
function mv(s,e){return (e===255)?'--':s.toFixed(2);}
async function poll(){
  try{
    const r=await fetch('/api/status');
    const s=await r.json();
    const name=ST_NAME[s.stateName]||s.stateName;
    /* 可用性控制：READY(1)/JOG(4) 状态才允许点动和回零 */
    const jogOk=(s.state===1||s.state===4);
    ['jUp','jDown','jLeft','jRight','jStop','btnReturnZero'].forEach(id=>{document.getElementById(id).disabled=!jogOk;});
    /* 拼接状态面板 HTML（含状态徽章、busy 过渡态标记、stale 失联警告）*/
    document.getElementById('statusBox').innerHTML=
      '状态: <span class="state-badge st-'+s.stateName+'">'+name+'</span>'
      +(s.busy?' <span class="k">(处理中)</span>':'')
      +(s.stale?' <span style="color:#b3261e">[STM32无响应]</span>':'')
      +'<br><span class="k">笔:</span> '+(s.pen?'落笔':'抬笔')
      +' &nbsp;<span class="k">故障:</span> '+(s.fault===0?'无':('F'+s.fault))
      +'<br><span class="k">电机1:</span> 位置 '+mv(s.m1.p,s.m1.e)+' rad, 速度 '+mv(s.m1.v,s.m1.e)+' rad/s, 扭矩 '+mv(s.m1.t,s.m1.e)
      +', 状态码 '+s.m1.e+', MOS '+s.m1.mo+'℃, 线圈 '+s.m1.rt+'℃'
      +'<br><span class="k">电机2:</span> 位置 '+mv(s.m2.p,s.m2.e)+' rad, 速度 '+mv(s.m2.v,s.m2.e)+' rad/s, 扭矩 '+mv(s.m2.t,s.m2.e)
      +', 状态码 '+s.m2.e+', MOS '+s.m2.mo+'℃, 线圈 '+s.m2.rt+'℃'
      +'<br><span class="k">末端位置:</span> X '+s.x.toFixed(2)+' mm, Y '+s.y.toFixed(2)+' mm'
      +'<br><span class="k">STM32队列:</span> '+s.q+' 行 &nbsp;<span class="k">已执行:</span> '+s.line+' 行'
      +'<br><span class="k">下发进度:</span> '
      +(s.feeding?(s.feedIdx+' / '+s.feedTotal+' 行'):(s.feedTotal+' 行就绪'+(s.feedTotal>0?'（点击开始绘画后自动下发）':'')));
    /* 绿色十字定位：仅在未失联且末端位置在 A4 范围内时显示 */
    let mx,my;
    if(!s.stale && Math.abs(s.x)<=CFG.A4_W/2 && Math.abs(s.y)<=CFG.A4_H/2){
      const q=toPX(s.x,s.y);
      mx=q.x; my=q.y;
    }
    redraw(mx,my);
  }catch(e){}
}
setInterval(poll,200);

/* =====================================================================
 * 启动初始化：
 *   1. loadCfg() 拉取 ESP32 集中参数（修改画板尺寸、点动滑杆范围）
 *   2. redraw() 绘制初始空白画布（虚线框）
 * ===================================================================== */
async function loadCfg(){
  try{
    const r=await fetch('/api/config');
    const c=await r.json();
    if(c.A4_W) Object.assign(CFG,c);                  /* 参数合并（非空才覆盖，兼容旧版后端）*/
    CV.width=Math.round(CFG.A4_W*CFG.PX);             /* 按参数重新设置画布像素尺寸 */
    CV.height=Math.round(CFG.A4_H*CFG.PX);
    const sl=document.getElementById('jogSpeed');
    sl.min=CFG.jogMin; sl.max=CFG.jogMax; sl.value=CFG.jogDef;  /* 更新滑杆范围与默认值 */
    JOG_SPEED=CFG.jogDef;
    document.getElementById('jogSpeedVal').textContent=String(CFG.jogDef);
    redraw();
  }catch(e){}
}
loadCfg();
redraw();
</script>
</body>
</html>
)rawliteral";
