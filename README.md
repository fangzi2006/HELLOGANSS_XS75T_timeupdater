
# ⌨️ 键盘时间同步工具

这是一个用于同步 **xs75t 键盘**（VID: `05AC`, PID: `024F`）屏幕时间的工具。其实就是有点精神洁癖，不想一直挂着驱动，但是这破键盘几天不同步时间就会有误差，所以 <del>让AI写了这个程序</del> 在AI的辅助下写了个程序

> **更新：现在有线模式和 2.4G 无线模式都支持了！**
> 之前只有 2.4G 能用<del>主要是我懒</del>，这次把官方驱动逆向了一遍，
> 把有线和无线两套协议都扒出来了。程序会**自动识别**当前是插着线还是用接收器。

---

## ⚙️ 打包说明

如果您想从源代码运行或自行打包，请参考以下步骤。

### 1. 依赖安装

只用 Python 标准库 + Windows 自带的
`hid.dll` / `cfgmgr32.dll` / `kernel32.dll`：

```bash
# 无需安装任何依赖
```

### 2. 打包所需

```bash
pip install pyinstaller
```

### 3. 打包步骤

#### 步骤 1：生成 `.spec` 文件

使用以下命令生成 PyInstaller 配置文件：

```bash
pyi-makespec timeupdater.py --onefile --noconsole --name timeupdater
```

#### 步骤 2：执行最终打包

使用生成的 `timeupdater.spec` 文件进行打包。最终的可执行文件将在 `dist/` 目录下。

```bash
pyinstaller timeupdater.spec  --clean
```

> **启动速度提示**：`--onefile` 打包出来的单文件 exe，每次运行都要把内容解压到临时目录，
> 容易被 Windows Defender 实时扫描，在部分机器上启动会明显变慢
> 如果介意启动速度，可以改用 `--onedir` 打包成目录，启动快得多：
> ```bash
> pyi-makespec timeupdater.py --onedir --noconsole --name timeupdater
> ```
> 代价是产物是一个目录（`dist/timeupdater/`），分发时需要打个压缩包。

## 📦 使用方法

请从 [Release 页面](https://github.com/fangzi2006/HELLOGANSS_XS75T_timeupdater/releases) 下载 [`timeupdater.exe`](https://github.com/fangzi2006/HELLOGANSS_XS75T_timeupdater/releases) 文件。

### 默认行为

如果不带任何参数运行，程序将**自动识别**连接方式（优先有线 USB，其次是 2.4G 接收器），
获取当前电脑的系统时间并同步到键盘。

运行完**静默退出、不弹任何窗口**：成功时不输出任何内容、返回退出码 `0`；
失败时才打印错误并返回非 `0`。适合丢进计划任务里无人值守运行。

### 支持的参数

| 参数 | 全称 | 格式 / 可选值 | 说明 | 示例 |
|------|------|------|------|------|
| `-t` | `--time` | `HH:MM:SS` | 自定义时间 | `20:30:00` |
| `-d` | `--date` | `YYYY-MM-DD` | 自定义日期 | `2025-11-24` |
| | `--mode` | `auto` / `usb` / `24g` | 强制指定连接方式（默认 `auto`） | `--mode usb` |
| | `--debug` | - | 输出详细调试信息（默认静默） | `--debug` |
| | `--list` | - | 只列出识别到的通道，不发送 | `--list` |
| | `--dry-run` | - | 只打印将要发送的报文，不发送 | `--dry-run` |
| | `--screen` | 数字 | 屏幕序号 +1（默认 `1`） | `--screen 1` |

### 示例：设置指定日期和时间

将键盘时间设置为 **2025年11月24日 20点30分00秒**：

```bash
timeupdater.exe -t 20:30:00 -d 2025-11-24

```

### 示例：开机 / 登录时自动同步

用管理员权限跑一次这条命令，以后每次登录 Windows 就会自动静默同步一次：

```powershell
schtasks /Create /TN "KbdTimeSync" /TR "\"完整路径\timeupdater.exe\"" /SC ONLOGON /RL LIMITED /F
```

也可以在「任务计划程序」里手动创建：触发器选「登录时」或「每天」，操作选「启动程序」填 exe 路径。

---

## 🔍 它是怎么工作的（技术向）

<del>其实就是把官方驱动扒了一遍</del>。

键盘在两种连接下是**两套完全不同的 HID 协议**：

| | 有线 USB | 2.4G 无线 |
|---|---|---|
| 设备 | 键盘本体（SONiX XS75T） | 2.4G 接收器（Dongle） |
| 接口 | `MI_03`，UsagePage `0xFF13` | `MI_03`，UsagePage `0xFF60` |
| 传输 | `HidD_SetFeature`，65 字节 Feature Report | `WriteFile`，33 字节输出报告 |
| 命令 | 握手1 → 握手2 → 时间 → 提交（共 4 条） | 单条报文 |
| 校验 | 无 | 异或 `payload[5:32]` |

有线的 4 条命令之间需要短暂间隔（实测 2~100ms 都行），
太长或太短都会让键盘拒绝应用时间 —— 具体见 [`SPEC.md`](SPEC.md)。

另外还发现一个好用的技巧：键盘会把收到的报文**回显**回来，
所以程序可以自己 `HidD_GetFeature` 读回显来判断同步成功与否，
不用每次都盯着屏幕看时间变了没有（详见 [`SPEC.md`](SPEC.md)）。

> 想从零复现这个程序的话，看 [`SPEC.md`](SPEC.md) 就够了 ——
> 里面有完整的字节级协议定义、时序要求、踩坑清单和一份可运行的参考实现。


---

## 📄 许可证

见 [LICENSE](LICENSE)。
