# Sutando 移动端远程控制功能文档

## 概述

Sutando 移动端远程控制功能允许用户通过手机 App 实时查看并控制 macOS 电脑。该功能基于 LiveKit 实时音视频通信框架和自定义的输入控制协议实现，提供低延迟的屏幕共享和精确的鼠标键盘控制。

**核心能力：**
- 实时屏幕流传输（基于 LiveKit WebRTC）
- 触摸转鼠标点击（单击、双击、右键）
- 虚拟键盘输入（支持中文等非 ASCII 字符）
- 手势操作（滚动、双指滑动切换控制栏）
- 系统控制（音量、静音）

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                  Mobile App (Flutter)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ LiveKit      │  │ Input Control│  │ UI Controls  │      │
│  │ Video Client │  │ HTTP Client  │  │ (Keyboard/   │      │
│  │              │  │              │  │  Volume)     │      │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘      │
│         │                  │                                 │
│         │ WebRTC           │ HTTPS                           │
└─────────┼──────────────────┼─────────────────────────────────┘
          │                  │
          │                  │ (LAN or VPN)
          │                  │
┌─────────┼──────────────────┼─────────────────────────────────┐
│         │                  │          macOS PC                │
│         ▼                  ▼                                  │
│  ┌─────────────┐    ┌─────────────────────────────┐         │
│  │ LiveKit     │    │ Mobile Control Server       │         │
│  │ Agent       │    │ (Python HTTP, Port 7851)    │         │
│  │ (Screen     │    │ - Click (Quartz CGEvents)   │         │
│  │  Publisher) │    │ - Type (Clipboard Paste)    │         │
│  └─────────────┘    │ - Scroll (CGScrollWheel)    │         │
│                     │ - Volume (AppleScript)      │         │
│                     └─────────────────────────────┘         │
│                                                               │
│  ┌─────────────────────────────────────────────────┐        │
│  │ Token Server (Port 7850)                        │        │
│  │ - JWT 生成                                       │        │
│  │ - 用户认证 (SHA-256)                             │        │
│  └─────────────────────────────────────────────────┘        │
│                                                               │
│  ┌─────────────────────────────────────────────────┐        │
│  │ Screen Publisher Server (Port 8080, HTTPS)      │        │
│  │ - 静态文件服务 (mobile.html)                     │        │
│  │ - Token 代理                                     │        │
│  └─────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────┘
```

### 核心组件

#### 1. **LiveKit 视频流** (WebRTC)
- **作用**: 实时传输 macOS 屏幕画面到手机
- **技术**: WebRTC (VP8/VP9 编码)
- **延迟**: < 200ms (局域网)
- **分辨率**: 自适应 (默认 1920x1080)

#### 2. **Mobile Control Server** (Python HTTP)
- **端口**: 7851
- **协议**: HTTP REST API
- **认证**: 用户名 + 密码 (SHA-256 哈希验证)
- **功能**: 接收手机发送的输入指令，转换为 macOS 系统事件

#### 3. **Token Server** (Python HTTP)
- **端口**: 7850
- **作用**: 生成 LiveKit JWT 令牌，用于客户端连接房间
- **认证**: 基于 `users.json` 的用户凭证验证

#### 4. **Screen Publisher Server** (Python HTTPS)
- **端口**: 8080
- **作用**: 提供 HTTPS 访问入口，代理 Token 请求
- **证书**: 自签名证书 (开发环境)

---

## 技术实现原理

### 1. 屏幕流传输

**流程：**
1. LiveKit Agent 在 macOS 上捕获屏幕 (使用 `screencapture` 或 AVFoundation)
2. 编码为 VP8/VP9 视频流
3. 通过 WebRTC 传输到 LiveKit Cloud
4. 手机 App 通过 LiveKit SDK 订阅视频轨道
5. Flutter `VideoTrackRenderer` 渲染视频帧

**关键代码 (Flutter):**
```dart
// lib/pages/room_page.dart
VideoTrackRenderer(
  track,
  fit: RTCVideoViewObjectFit.RTCVideoViewObjectFitContain,
  mirrorMode: VideoViewMirrorMode.auto,
)
```

---

### 2. 触摸坐标映射

**挑战**: 手机触摸坐标需要映射到 PC 屏幕的绝对坐标。

**解决方案**: 考虑视频的 `contain` 适配模式（letterbox/pillarbox）。

**算法 (Dart):**
```dart
// lib/controllers/room_controller.dart
Offset? mapToScreenCoords(Offset touchPos, Size viewSize) {
  final screenW = inputControl!.screenWidth!.toDouble();
  final screenH = inputControl!.screenHeight!.toDouble();
  
  // 计算视频在 view 中的实际显示区域
  final viewAspect = viewSize.width / viewSize.height;
  final screenAspect = screenW / screenH;
  
  double videoW, videoH, offsetX, offsetY;
  if (viewAspect > screenAspect) {
    // Pillarboxing (左右黑边)
    videoH = viewSize.height;
    videoW = videoH * screenAspect;
    offsetX = (viewSize.width - videoW) / 2;
    offsetY = 0;
  } else {
    // Letterboxing (上下黑边)
    videoW = viewSize.width;
    videoH = videoW / screenAspect;
    offsetX = 0;
    offsetY = (viewSize.height - videoH) / 2;
  }
  
  // 检查触摸是否在视频区域内
  final relX = touchPos.dx - offsetX;
  final relY = touchPos.dy - offsetY;
  if (relX < 0 || relX > videoW || relY < 0 || relY > videoH) {
    return null; // 点击在黑边上
  }
  
  // 映射到屏幕坐标
  final pcX = (relX / videoW) * screenW;
  final pcY = (relY / videoH) * screenH;
  return Offset(pcX, pcY);
}
```

---

### 3. 鼠标点击实现 (macOS)

**技术选型**: Quartz CGEvents (绝对坐标) > AppleScript (应用相对坐标)

**为什么不用 AppleScript?**
- AppleScript 的 `click at {x, y}` 使用的是应用窗口相对坐标
- 无法精确控制屏幕绝对位置的点击

**Quartz CGEvents 实现 (Python):**
```python
# src/mobile-control-server.py
import Quartz

def do_click(x: int, y: int, button: str = "left", double: bool = False):
    pt = (float(x), float(y))
    
    # 1. 移动鼠标到目标位置
    move = Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, pt, Quartz.kCGMouseButtonLeft
    )
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
    time.sleep(0.02)
    
    # 2. 执行点击
    if button == "right":
        down_type = Quartz.kCGEventRightMouseDown
        up_type = Quartz.kCGEventRightMouseUp
        btn = Quartz.kCGMouseButtonRight
    else:
        down_type = Quartz.kCGEventLeftMouseDown
        up_type = Quartz.kCGEventLeftMouseUp
        btn = Quartz.kCGMouseButtonLeft
    
    clicks = 2 if double else 1
    for i in range(clicks):
        if i > 0:
            time.sleep(0.05)
        down = Quartz.CGEventCreateMouseEvent(None, down_type, pt, btn)
        if double:
            Quartz.CGEventSetIntegerValueField(
                down, Quartz.kCGMouseEventClickState, i + 1
            )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.02)
        up = Quartz.CGEventCreateMouseEvent(None, up_type, pt, btn)
        if double:
            Quartz.CGEventSetIntegerValueField(
                up, Quartz.kCGMouseEventClickState, i + 1
            )
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
```

**依赖安装:**
```bash
pip install pyobjc-framework-Quartz pyobjc-framework-Cocoa
```

---

### 4. 中文输入实现

**挑战**: AppleScript 的 `keystroke` 只支持 ASCII 字符。

**解决方案**: 检测非 ASCII 字符，使用剪贴板粘贴。

**实现 (Python):**
```python
# src/mobile-control-server.py
def do_type(text: str):
    # 检测是否包含非 ASCII 字符
    has_non_ascii = any(ord(c) > 127 for c in text)
    has_newline = "\n" in text or "\r" in text
    use_paste = has_non_ascii or has_newline or len(text) > 80
    
    if use_paste:
        # 保存当前剪贴板内容
        saved = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        ).stdout
        
        # 写入临时文件并复制到剪贴板
        tmp = f"/tmp/sutando-mobile-type-{int(time.time()*1000)}.txt"
        Path(tmp).write_text(text, encoding='utf-8')  # 关键：UTF-8 编码
        subprocess.run(f"pbcopy < {tmp}", shell=True, timeout=2)
        
        # 执行粘贴 (Cmd+V)
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            timeout=5, capture_output=True,
        )
        time.sleep(0.3)
        
        # 恢复原剪贴板内容
        if saved:
            tmp_r = f"/tmp/sutando-mobile-restore-{int(time.time()*1000)}.txt"
            Path(tmp_r).write_text(saved, encoding='utf-8')
            subprocess.run(f"pbcopy < {tmp_r}", shell=True, timeout=2)
            os.unlink(tmp_r)
        os.unlink(tmp)
    else:
        # ASCII 短文本：直接使用 keystroke
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(
            ["osascript", "-e",
             f'tell application "System Events" to keystroke "{safe}"'],
            timeout=5, capture_output=True,
        )
```

---

### 5. 手势识别

**双指上下滑动切换控制栏:**

```dart
// lib/pages/room_page.dart
GestureDetector(
  onScaleStart: (details) {
    _lastFocalPoint = details.focalPoint;
  },
  onScaleUpdate: (details) {
    // 检测双指滑动 (scale == 1.0 表示没有缩放)
    if (details.pointerCount == 2 && details.scale == 1.0) {
      final delta = details.focalPoint - _lastFocalPoint;
      if (delta.dy.abs() > 50) {
        if (delta.dy > 0) {
          controller.showControlBar.value = true;  // 下滑显示
        } else {
          controller.showControlBar.value = false; // 上滑隐藏
        }
        _lastFocalPoint = details.focalPoint;
      }
    }
  },
)
```

---

### 6. 认证与安全

**用户凭证存储 (`src/users.json`):**
```json
{
  "zqy": {
    "secret_sha256": "96cae35ce8a9b0244178bf28e4966c2ce1b8385723a96a6b838858cdd6ca0a1e",
    "room": "sutando-zqy"
  }
}
```

**认证流程:**
1. 手机 App 发送用户名 + 明文密码
2. Token Server 计算 `SHA256(password)`
3. 与 `users.json` 中的哈希值比对
4. 验证通过后生成 JWT 令牌

**HTTP 请求头认证 (Mobile Control Server):**
```python
# src/mobile-control-server.py
def _check_auth(self) -> bool:
    user = self.headers.get("X-User", "")
    secret = self.headers.get("X-Secret", "")
    if not user or not secret:
        # 也支持 query 参数
        params = parse_qs(urlparse(self.path).query)
        user = params.get("user", [""])[0]
        secret = params.get("secret", [""])[0]
    if not verify_user(user, secret):
        self._json(401, {"error": "Invalid credentials"})
        return False
    return True
```

---

## 使用方法

### 前置条件

1. **macOS 权限设置**
   - 系统设置 → 隐私与安全性 → 辅助功能
   - 添加 `Terminal.app` 和 `Python` 到允许列表

2. **Python 依赖安装**
   ```bash
   cd ~/project/sutando
   pip install -r requirements-livekit.txt
   ```

3. **用户配置**
   ```bash
   # 添加用户 (如果还没有)
   python3 src/add-user.py <username> <password>
   ```

4. **环境变量配置 (`.env`)**
   ```bash
   LIVEKIT_API_KEY=your_api_key
   LIVEKIT_API_SECRET=your_api_secret
   LIVEKIT_URL=wss://your-livekit-server.com
   ```

---

### 启动服务

**方式 1: 一键启动 (推荐)**
```bash
bash src/start-livekit.sh
```

这会启动：
- Token Server (端口 7850)
- Mobile Control Server (端口 7851)
- Screen Publisher Server (端口 8080, HTTPS)

**方式 2: 单独启动**
```bash
# Terminal 1: Token Server
python3 src/livekit-token-server.py

# Terminal 2: Mobile Control Server
python3 src/mobile-control-server.py

# Terminal 3: Screen Publisher Server
python3 src/screen-publisher-server.py
```

**验证服务状态:**
```bash
# 检查进程
pgrep -fl "livekit-token-server|mobile-control-server|screen-publisher-server"

# 测试 Token Server
curl "http://localhost:7850/token?user=zqy&secret=your_password&identity=phone-user"

# 测试 Mobile Control Server
curl -H "X-User: zqy" -H "X-Secret: your_password" http://localhost:7851/screen/info
```

---

### 手机 App 使用

#### 1. 安装 App
```bash
cd ~/project/sutando/app
flutter run  # 开发模式
# 或
flutter build apk  # Android 发布包
flutter build ios  # iOS 发布包
```

#### 2. 连接配置

**局域网连接 (同一 WiFi):**
1. 查看 Mac 的局域网 IP:
   ```bash
   ifconfig | grep "inet 192"
   # 例如: 192.168.1.100
   ```

2. 在 App 中填写:
   - 服务器地址: `https://192.168.1.100:8080`
   - 用户名: `zqy`
   - 密码: `your_password`

3. 点击 "Connect"

**远程连接 (通过 Tailscale VPN):**
1. 在 Mac 和手机上安装 Tailscale
2. 使用 Tailscale IP (例如 `100.x.x.x`)
3. 服务器地址: `https://100.x.x.x:8080`

#### 3. 操作说明

**视频区域手势:**
- **单指点击** → PC 鼠标左键点击
- **双击** → PC 鼠标双击
- **长按** → PC 鼠标右键点击
- **单指上下拖动** → PC 滚动
- **双指上下滑动** → 显示/隐藏控制栏

**控制栏按钮 (底部):**
- **Keyboard** → 打开虚拟键盘 (支持中文输入)
- **Controls** → 打开快捷控制面板 (音量、静音)
- **Mute** → 麦克风静音/取消静音
- **Leave** → 断开连接

**横屏模式:**
- Show/Hide 按钮显示在右上角 (Connected 状态下方)
- 点击可切换控制栏显示/隐藏

**竖屏模式:**
- Show/Hide 按钮显示在底部中央
- 双指上下滑动也可切换控制栏

---

## 故障排查

### 1. 连接失败 (ERR_EMPTY_RESPONSE)

**症状**: 浏览器访问 `https://192.168.1.x:8080/mobile` 显示 ERR_EMPTY_RESPONSE

**原因**: Token Server 超时或异常未捕获

**解决方案**:
```bash
# 检查 Token Server 日志
tail -f ~/project/sutando/logs/token-server.log

# 重启服务
bash src/start-livekit.sh --stop
bash src/start-livekit.sh
```

---

### 2. 点击无反应

**症状**: 手机点击视频，PC 上没有鼠标点击

**排查步骤**:

1. **检查 Mobile Control Server 是否运行:**
   ```bash
   curl -H "X-User: zqy" -H "X-Secret: your_password" \
     http://localhost:7851/screen/info
   # 应返回: {"width": 1920, "height": 1080}
   ```

2. **检查 macOS 辅助功能权限:**
   - 系统设置 → 隐私与安全性 → 辅助功能
   - 确保 Terminal 或 Python 已授权

3. **手动测试点击:**
   ```bash
   curl -X POST http://localhost:7851/input/click \
     -H "Content-Type: application/json" \
     -H "X-User: zqy" -H "X-Secret: your_password" \
     -d '{"x": 960, "y": 540}'
   # 应该看到鼠标移动到屏幕中心并点击
   ```

4. **检查 App 日志:**
   ```bash
   flutter logs
   # 查找 "[SUTANDO] Input control ready=true"
   ```

---

### 3. 中文输入显示为 "a"

**症状**: 在 Keyboard 面板输入中文，PC 上只显示字母 "a"

**原因**: 旧版本使用 AppleScript `keystroke`，不支持非 ASCII 字符

**解决方案**: 确保使用最新版本的 `mobile-control-server.py`，其中 `do_type()` 函数会自动检测非 ASCII 字符并使用剪贴板粘贴。

**验证修复:**
```bash
curl -X POST http://localhost:7851/input/type \
  -H "Content-Type: application/json" \
  -H "X-User: zqy" -H "X-Secret: your_password" \
  -d '{"text": "你好世界"}'
# 应该在 PC 上看到 "你好世界"
```

---

### 4. 横屏时底部按钮不显示

**症状**: App 横屏后，底部控制栏消失

**原因**: 旧版本在横屏时会隐藏整个 overlay

**解决方案**: 更新到最新版本，现在横屏时 Show/Hide 按钮会显示在右上角。

---

### 5. 视频黑屏或卡顿

**可能原因:**
- LiveKit Agent 未启动
- 网络延迟过高
- 屏幕捕获权限未授予

**排查步骤:**

1. **检查 LiveKit Agent 状态:**
   ```bash
   # 查看 LiveKit 日志
   tail -f ~/project/sutando/logs/livekit-agent.log
   ```

2. **测试网络延迟:**
   ```bash
   # 从手机 ping Mac
   ping 192.168.1.100
   # 延迟应 < 50ms
   ```

3. **检查屏幕录制权限:**
   - 系统设置 → 隐私与安全性 → 屏幕录制
   - 确保 Terminal 或 Python 已授权

---

## API 参考

### Mobile Control Server API

**Base URL**: `http://localhost:7851`

**认证**: 所有请求需要 `X-User` 和 `X-Secret` 请求头

#### 1. 获取屏幕信息
```http
GET /screen/info
Headers:
  X-User: zqy
  X-Secret: your_password

Response:
{
  "width": 1920,
  "height": 1080
}
```

#### 2. 鼠标点击
```http
POST /input/click
Headers:
  X-User: zqy
  X-Secret: your_password
  Content-Type: application/json
Body:
{
  "x": 960,
  "y": 540,
  "button": "left",  // "left" | "right"
  "double": false    // true for double-click
}

Response:
{
  "status": "ok",
  "x": 960,
  "y": 540,
  "button": "left",
  "double": false
}
```

#### 3. 键盘输入
```http
POST /input/key
Body:
{
  "key": "enter",  // "enter" | "escape" | "tab" | "delete" | "space" | "up" | "down" | "left" | "right"
  "modifiers": ["command"]  // ["command", "shift", "control", "option"]
}
```

#### 4. 文本输入
```http
POST /input/type
Body:
{
  "text": "Hello 你好"
}
```

#### 5. 滚动
```http
POST /input/scroll
Body:
{
  "deltaX": 0,
  "deltaY": 3  // 正数向上滚动，负数向下滚动
}
```

#### 6. 音量控制
```http
POST /system/volume
Body:
{
  "level": 50  // 0-100
}
// 或
{
  "mute": true  // true | false
}
```

#### 7. 亮度控制
```http
POST /system/brightness
Body:
{
  "level": 50  // 0-100
}
```

---

## 性能指标

### 实测数据 (局域网环境)

| 指标 | 数值 | 说明 |
|------|------|------|
| 视频延迟 | 150-200ms | 从屏幕捕获到手机显示 |
| 点击延迟 | 50-100ms | 从触摸到 PC 响应 |
| 输入延迟 | 100-150ms | 从键盘输入到 PC 显示 |
| 视频帧率 | 30 FPS | LiveKit 自适应 |
| 视频分辨率 | 1920x1080 | 可配置 |
| 带宽占用 | 2-5 Mbps | 取决于屏幕内容变化 |
| CPU 占用 (Mac) | 10-15% | 单核 |
| 电池消耗 (手机) | ~8%/小时 | 持续使用 |

---

## 未来优化方向

### 短期 (1-2 周)
- [ ] 添加文件传输功能 (上传/下载)
- [ ] 支持多显示器切换
- [ ] 添加快捷键面板 (Cmd+C, Cmd+V, Cmd+Tab 等)
- [ ] 优化横屏 UI 布局

### 中期 (1-2 月)
- [ ] 支持触控板模式 (相对移动)
- [ ] 添加语音控制集成
- [ ] 支持 Windows/Linux 平台
- [ ] 实现端到端加密

### 长期 (3-6 月)
- [ ] 发布到 App Store / Google Play
- [ ] 添加协作功能 (多人同时控制)
- [ ] 实现 AI 辅助操作 (语音转操作)
- [ ] 支持游戏模式 (低延迟优化)

---

## 相关文件清单

### 服务端 (Python)
- `src/livekit-token-server.py` - JWT 令牌生成服务
- `src/mobile-control-server.py` - 输入控制 API 服务
- `src/screen-publisher-server.py` - HTTPS 静态文件服务
- `src/users.json` - 用户凭证配置
- `src/start-livekit.sh` - 一键启动脚本
- `requirements-livekit.txt` - Python 依赖

### 客户端 (Flutter)
- `app/lib/main.dart` - App 入口
- `app/lib/pages/connect_page.dart` - 连接配置页面
- `app/lib/pages/room_page.dart` - 远程控制主界面
- `app/lib/controllers/room_controller.dart` - 业务逻辑控制器
- `app/lib/services/input_control_service.dart` - HTTP 客户端
- `app/lib/config/app_config.dart` - 配置常量

### 配置文件
- `.env` - 环境变量 (LiveKit 凭证)
- `app/pubspec.yaml` - Flutter 依赖

---

## 许可证

本项目为 Sutando 的一部分，遵循项目主许可证。

---

## 联系方式

如有问题或建议，请通过以下方式联系：
- GitHub Issues: [sutando/issues](https://github.com/your-repo/sutando/issues)
- Email: your-email@example.com

---

**文档版本**: 1.0  
**最后更新**: 2026-05-05  
**作者**: Sutando Team
