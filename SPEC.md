# helloganss 键盘屏幕时间同步 —— 完整规格与参考实现

> **本文档自包含。** 目标是：只读这一份文档，就能从零写出替代官方驱动的时间同步程序。
> 第 1~5 节是协议规范，第 6 节是完整可运行源码，第 7~8 节是踩过的坑与排障表，
> 附录 A~C 是结论溯源，**附录 D 是可复用的逆向方法论**（如何自己把这套协议扒出来）。

**目录**

| 章节 | 内容 |
|---|---|
| 0 | 三十秒速览 |
| 1 | 硬件接口与通道识别 |
| 2 | Windows API 用法与坑 |
| 3 | 有线 USB 协议（65 字节 ×4 条） |
| 4 | 2.4G 无线协议（33 字节 ×1 条） |
| 5 | 回显自验证机制 |
| 6 | 完整参考实现（源码） |
| 7 | 已知坑 11 条 |
| 8 | 排障表 |
| 9 | 打包与自动运行 |
| 附录 A | 逆向溯源：函数地址表 |
| 附录 B | Frida 抓包复现代码 |
| 附录 C | 未覆盖范围 |
| **附录 D** | **逆向方法论：从闭源驱动到协议规范** |

- **目标程序**：`D:\Program Files (x86)\helloganss Driver\DeviceDriver.exe`（32 位 PE / MFC，Beta 1.0.0.2）
- **硬件**：helloganss XS75T / XS98T（SONiX 方案，带 1.14" LCD 屏）
- **USB 标识**：VID `0x05AC`，PID `0x024F`
- **验证状态**：有线、2.4G 无线两条通道均已实测通过（屏幕时间成功更新）

---

## 0. 三十秒速览

| | 有线 USB | 2.4G 无线 |
|---|---|---|
| 物理设备 | 键盘本体（Manufacturer = `SONiX`） | 接收器（Product = `2.4G Dongle`） |
| HID 接口 | `MI_03`，UsagePage `0xFF13` / Usage `0x0001` | `MI_03`，UsagePage `0xFF60` / Usage `0x0061` |
| 传输 API | **`HidD_SetFeature`** | **`WriteFile`** |
| 报文长度 | **65 字节** Feature Report | **33 字节** Output Report |
| 命令条数 | **4 条**（握手 ×2 + 时间 + 提交） | **1 条** |
| 校验和 | 无 | 有（XOR） |

**核心难点**：有线与无线是两套完全不同的协议。用无线的 33 字节思路去写有线，永远发不进去——
有线键盘的 MI_03 只接受 65 字节 Feature Report，且必须先握手、最后提交。

---

## 1. 硬件接口：认准这两条通道

用 `HidP_GetCaps` 实测本机各接口的报告长度：

| 设备 | 接口 | UsagePage / Usage | IN | OUT | FEATURE | 用途 |
|---|---|---|---|---|---|---|
| XS75T（键盘本体） | MI_00 | 0x0001 / 0x06 | 9 | 2 | 0 | 标准键盘输入 |
| XS75T | MI_01 | 0x000C 等 | — | — | 0 | 消费者控制 |
| XS75T | MI_02 | 0xFF68 / 0x61 | 65 | **4097** | 0 | LCD 图片/固件批量通道 |
| XS75T | **MI_03** | 0xFF13 / 0x01 | 65 | 65 | **65** | ✅ **有线命令通道** |
| 2.4G Dongle | **MI_03** | 0xFF60 / 0x61 | 33 | 33 | 0 | ✅ **无线命令通道** |
| 2.4G Dongle | MI_04 | 0xFF60 / 0x61 | 65 | 65 | 0 | （不用） |

**识别规则**（写代码照抄即可）：

```python
# 有线：键盘本体上 feature_len == 65 且产品名不含 DONGLE
if caps['feature_len'] == 65 and 'DONGLE' not in product.upper():
    → usb 通道
# 无线：接收器上 output_len == 33 且 usage_page == 0xFF60
elif caps['output_len'] == 33 and caps['usage_page'] == 0xFF60:
    → 24g 通道
```

**两个易错点**：

1. **只探测 `MI_03`**。遍历打开键盘输入接口（MI_00）、LCD 图片接口（MI_02）等无关接口，
   可能把键盘固件带入异常状态（表现为「模式切换后脚本和官方驱动双双失效」）。
   官方驱动也只碰它需要的接口。
2. `config.xml` 里 USB 模式的 `hid_interface="…&MI_00"` **只用于判定连线状态**，不是命令通道
   （MI_00 完全没有 Feature Report，对它调用 `HidD_SetFeature` 必然失败）。

---

## 2. Windows 侧 API 用法

程序只依赖系统自带 DLL：`kernel32.dll` / `hid.dll` / `cfgmgr32.dll`，无需 pip 安装任何包。

### 2.1 枚举设备路径

```python
cfgmgr32.CM_Get_Device_Interface_List_SizeW(&size, &HID_GUID, NULL, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
cfgmgr32.CM_Get_Device_Interface_ListW(&HID_GUID, NULL, buf, size, CM_GET_DEVICE_INTERFACE_LIST_PRESENT)
```

`HID_GUID = {4D1E55B2-F16F-11CF-88CB-001111000030}`，标志位 `CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0x1`。

> **坑**：返回的是 **multi-sz**（多个以 `\0` 结尾的 UTF-16 字符串，整体以 `\0\0` 结束）。
> 必须用 `create_string_buffer` 拿原始字节、按 `utf-16-le` 解码后 `split('\0')`；
> 用 `create_unicode_buffer().value` 只能拿到第一个字符串。

筛出路径里含 `VID_05AC&PID_024F` 且含 `&MI_03` 的项即可。

### 2.2 打开设备

```python
CreateFileW(path,
            GENERIC_READ | GENERIC_WRITE,      # 0xC0000000
            FILE_SHARE_READ | FILE_SHARE_WRITE, # 3
            NULL, OPEN_EXISTING,               # 3
            flags, NULL)
```

- **有线（SetFeature）**：官方驱动用 `FILE_FLAG_OVERLAPPED`（`0x40000000`），照抄即可；
  `HidD_SetFeature` 不关心这个标志，但保持一致最安全。
- **无线（WriteFile）**：**必须不带 OVERLAPPED**。若用 OVERLAPPED 打开却给 `WriteFile` 传 `NULL`
  的 `lpOverlapped`，会返回 `ERROR_INVALID_PARAMETER (87)`。

### 2.3 取报告长度

```python
HidD_GetAttributes(h, &attr)            # 拿 VID/PID
HidD_GetPreparsedData(h, &pp)
HidP_GetCaps(pp, &caps)                 # caps.FeatureReportByteLength / OutputReportByteLength / UsagePage
HidD_FreePreparsedData(pp)
```

> **坑**：本机 `DeviceIoControl(IOCTL_HID_GET_REPORT_DESCRIPTOR, 0x000B000B)` 一律返回
> `ERROR_INVALID_FUNCTION`，拿不到原始报告描述符。改用 `HidP_GetCaps` 拿长度就够了。

### 2.4 读写

```python
HidD_SetFeature(h, buf, 65)   # 写 Feature Report（有线）
HidD_GetFeature(h, buf, 65)   # 读回键盘回显（有线，用于同步等待/自校验）
WriteFile(h, buf, 33, &written, NULL)   # 写输出报告（无线）
```

---

## 3. 有线 USB 协议（65 字节 Feature Report）

### 3.1 命令序列

官方驱动一次完整同步会依次发 **4 条** 65 字节 Feature Report，缺一不可：

| # | 命令 | 首字节（索引 1~2） | 作用 |
|---|---|---|---|
| 1 | 握手 1 | `04 18` | 进入时间设置模式 |
| 2 | 握手 2 | `04 28` | 带参数 `0x01`（索引 9） |
| 3 | 时间报文 | `00 01` | 写年月日时分秒 + 星期 |
| 4 | 提交 | `04 02` | **触发键盘把刚写入的时间应用到屏幕** |

> 漏掉第 4 条提交命令，键盘会正确接收时间（回显也对），但屏幕**不会刷新**。

### 3.2 报文逐字节定义

**① 握手 1**（65 字节，其余为 0）
```
[0]  0x00        报告 ID（恒 0）
[1]  0x04
[2]  0x18
[3..64] 0x00
```

**② 握手 2**（65 字节，其余为 0）
```
[0]  0x00
[1]  0x04
[2]  0x28
[9]  0x01        ← 注意是索引 9，不是 8
[其余] 0x00
```

**③ 时间报文**（65 字节，其余为 0）
```
[0]  0x00        报告 ID（恒 0）
[1]  0x00        固定 0（发送封装插入的额外字节，见 3.4）
[2]  屏幕序号 + 1（默认 1；官方取自 GUI 列表当前选中项 + 1）
[3]  0x5A
[4]  年 - 2000       例：2026 → 0x1A
[5]  月
[6]  日
[7]  时
[8]  分
[9]  秒
[10] 0x00
[11] 星期（Windows SYSTEMTIME 约定：0=周日 … 6=周六）
[12..62] 0x00
[63] 0xAA        ← 结尾标记
[64] 0x55
```

> ⚠️ **`AA 55` 必须在索引 63/64**，写成 62/63 也能在冷启动时侥幸生效，
> 但键盘模式切换后会进入严格校验状态而失效（详见第 7 节）。这是本项目踩过最深的坑。

**④ 提交**（65 字节，其余为 0）
```
[0]  0x00
[1]  0x04
[2]  0x02
[3..64] 0x00
```

> 📌 **读报文请按十六进制解析字段值**：`0x27` 是 39 而不是 27，`0x33` 是 51 而不是 33。
> （调试时我曾把 `08 27 33` 误读成 08:27:33，实际是 08:39:51，白白怀疑了一轮脚本 bug。）

### 3.3 时序要求（关键）

每条 `HidD_SetFeature` 之后必须 **Sleep 一小段时间再 `HidD_GetFeature`**，
让键盘有时间处理并推进状态机。实测：

| Sleep | 结果 |
|---|---|
| 0 ms | ❌ 失败（提交 `resp[4]=0`，命令根本没被处理） |
| 2 / 5 / 10 ms | ✅ 成功（总耗时 60~70 ms） |
| 30 / 40 / 50 ms | ✅ 成功（总耗时 140~250 ms） |
| 120 / 200 ms | ❌ 失败（时间报文→提交间隔超时，回显 `resp[4]=255`） |

**安全区间约 `[2ms, 100ms]`，取 10ms。**

注意 **Sleep 不是越长越好**——时间报文与提交之间隔太久会超时。
真正的耗时主体是 4 次 `GetFeature` 的 USB 同步 I/O（每次约 15ms），不是 Sleep。

### 3.4 为什么索引 1 有个"多余"的 0（右移规则）

官方驱动在 `0x00423680` 里把字段写进**源缓冲**（`[0]=0`、`[1]=屏幕+1`、`[2]=0x5A`、`[3]=年`…），
然后发送封装 `0x0044F660` 把源缓冲的 **前 64 字节复制到 `buf[1..64]`**，并强制 `buf[0]=0` 作为报告 ID。

结果：**源缓冲整体右移一位**，最终报文首部多出一个 0。

所以计算最终报文偏移时，要把「源缓冲布局」和「发送封装右移」**叠加**起来算，
只看其中一步必然错位——这正是本项目初版报文整体错一位的根因。

---

## 4. 2.4G 无线协议（33 字节 Output Report）

一次同步只需 **1 条** 33 字节输出报告，无握手、无提交。

```
[0]  0x00        报告 ID（恒 0）
[1]  0x0C
[2]  0x10
[3]  0x00
[4]  0x00
[5]  屏幕序号 + 1（默认 1）
[6]  0x5A
[7]  年 - 2000
[8]  月
[9]  日
[10] 时
[11] 分
[12] 秒
[13] 0x00
[14] 0x01        固定值（不是星期！）
[15..17] 0x00
[18] 0xAA
[19] 0x55
[20..31] 0x00
[32] 校验和 = XOR(f[5:32]) & 0xFF
```

- **校验和是异或，不是累加和**，范围是含报告 ID 的 `buf[5..31]`（对应原始脚本的 `data_bytes[4:31]`）。
- 索引 14 是**固定 `0x01`**，不是星期字段（无线报文里根本没有星期）。
- 用 `WriteFile` 发送，句柄**不要**用 OVERLAPPED 打开（否则报错 87）。

---

## 5. 回显自验证：不用盯着屏幕看

键盘的 65 字节 Feature Report 是**双向通道**：`HidD_SetFeature` 写入后，
键盘会在同一通道回显它收到的内容并返回状态码，用 `HidD_GetFeature` 读回即可自动判断成败。

### 5.1 回显字段表

**握手 1 / 握手 2 / 提交**类命令：
```
resp[1..2] = 回显命令字（04 18 / 04 28 / 04 02）
resp[4]    = 状态码
```

**时间报文**（几乎原样回显）：
```
resp[2]  = 屏幕序号+1      resp[7] = 时
resp[3]  = 0x5A            resp[8] = 分
resp[4]  = 年 - 2000       resp[9] = 秒
resp[5]  = 月              resp[10] = 0x00
resp[6]  = 日              resp[11] = 星期
resp[63] = 0xAA            resp[64] = 0x55
```

### 5.2 状态码语义

| 命令类型 | `resp[4]` | 含义 |
|---|---|---|
| 握手 / 提交 | `0x01` | ✅ 成功（时间已应用到屏幕） |
| 握手 / 提交 | `0xFF` | ❌ 未完成 / 超时（Sleep 过长） |
| 提交 | `0x00` | ❌ 未处理（Sleep 过短或命令没被接收） |
| **时间报文** | `年-2000` | ✅ 正常回显（**此时 resp[4] 是年字段，不是状态码**） |
| 时间报文 | `0xFF` | ❌ 异常（回显被破坏） |

> **验证时间报文要「比对回显字段」，不能看 `resp[4]==1`。**

### 5.3 用途

1. **自动化自测**：脚本内建「发→读→比对」，成功与否自己就知道。
2. **快速定位**：握手 `resp[4]!=1` → 通道问题；时间回显不符 → 报文字段错位；
   提交 `resp[4]!=1` → 时序问题。

---

## 6. 完整参考实现

零第三方依赖，Python 3 标准库即可运行。以下代码即 `timeupdater.py` 全文。

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
helloganss XS75T / XS98T 键盘屏幕时间同步（无需官方驱动）
协议规范见 SPEC.md；本文件为可直接运行的参考实现。

用法
----
    python timeupdater.py                # 自动识别模式，同步为系统时间（静默）
    python timeupdater.py --debug        # 输出详细调试信息
    python timeupdater.py --list         # 只列出识别到的通道，不发送
    python timeupdater.py --dry-run      # 打印将要发送的报文，不发送
    python timeupdater.py -t 18:30:00 -d 2026-10-02
    python timeupdater.py --mode usb     # 强制有线
    python timeupdater.py --mode 24g     # 强制 2.4G

退出码：0 成功 / 1 未找到键盘 / 2 指定模式的通道不存在 / 3 发送失败
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import datetime
import sys

VID = 0x05AC
PID = 0x024F

# ---------------------------------------------------------------- Windows API

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
hid = ctypes.WinDLL('hid', use_last_error=True)
cfgmgr32 = ctypes.WinDLL('cfgmgr32', use_last_error=True)

INVALID_HANDLE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0x00000001


class GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort),
                ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]


HID_GUID = GUID(0x4D1E55B2, 0xF16F, 0x11CF,
                (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30))


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Size', wt.ULONG), ('VendorID', wt.USHORT), ('ProductID', wt.USHORT),
                ('VersionNumber', wt.USHORT)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [('Usage', wt.USHORT), ('UsagePage', wt.USHORT),
                ('InputReportByteLength', wt.USHORT), ('OutputReportByteLength', wt.USHORT),
                ('FeatureReportByteLength', wt.USHORT), ('Reserved', wt.USHORT * 17),
                ('NumberLinkCollectionNodes', wt.USHORT),
                ('NumberInputButtonCaps', wt.USHORT), ('NumberInputValueCaps', wt.USHORT),
                ('NumberInputDataIndices', wt.USHORT),
                ('NumberOutputButtonCaps', wt.USHORT), ('NumberOutputValueCaps', wt.USHORT),
                ('NumberOutputDataIndices', wt.USHORT),
                ('NumberFeatureButtonCaps', wt.USHORT), ('NumberFeatureValueCaps', wt.USHORT),
                ('NumberFeatureDataIndices', wt.USHORT)]


kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                 wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                               ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.WriteFile.restype = wt.BOOL
kernel32.Sleep.argtypes = [wt.DWORD]
kernel32.Sleep.restype = None

hid.HidD_SetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_SetFeature.restype = wt.BOOLEAN
hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetFeature.restype = wt.BOOLEAN
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.c_void_p]
hid.HidD_GetAttributes.restype = wt.BOOLEAN
hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_GetPreparsedData.restype = wt.BOOLEAN
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidD_FreePreparsedData.restype = wt.BOOLEAN
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetCaps.restype = wt.ULONG
hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
hid.HidD_GetProductString.restype = wt.BOOLEAN

cfgmgr32.CM_Get_Device_Interface_List_SizeW.argtypes = [ctypes.POINTER(wt.DWORD),
                                                        ctypes.POINTER(GUID), wt.LPCWSTR,
                                                        wt.DWORD]
cfgmgr32.CM_Get_Device_Interface_List_SizeW.restype = wt.DWORD
cfgmgr32.CM_Get_Device_Interface_ListW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR,
                                                   ctypes.c_void_p, wt.DWORD, wt.DWORD]
cfgmgr32.CM_Get_Device_Interface_ListW.restype = wt.DWORD


def open_device(path, write=True, overlapped=False):
    access = GENERIC_READ | GENERIC_WRITE if write else 0
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    h = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, flags, None)
    return None if h == INVALID_HANDLE else h


def caps_of(h):
    """返回 (vid, pid, caps_dict)。"""
    attr = HIDD_ATTRIBUTES()
    attr.Size = ctypes.sizeof(attr)
    vid = pid = 0
    if hid.HidD_GetAttributes(h, ctypes.byref(attr)):
        vid, pid = attr.VendorID, attr.ProductID
    pp = ctypes.c_void_p()
    caps = None
    if hid.HidD_GetPreparsedData(h, ctypes.byref(pp)) and pp:
        c = HIDP_CAPS()
        hid.HidP_GetCaps(pp, ctypes.byref(c))
        caps = dict(usage_page=c.UsagePage, usage=c.Usage,
                    input_len=c.InputReportByteLength,
                    output_len=c.OutputReportByteLength,
                    feature_len=c.FeatureReportByteLength)
        hid.HidD_FreePreparsedData(pp)
    return vid, pid, caps


def product_of(h):
    buf = ctypes.create_unicode_buffer(128)
    return buf.value if hid.HidD_GetProductString(h, buf, 128) else ''


# --------------------------------------------------------------- 枚举 / 识别

class Channel(object):
    def __init__(self, path, mode, product, caps):
        self.path = path
        self.mode = mode          # 'usb' | '24g'
        self.product = product
        self.caps = caps

    def __repr__(self):
        return '<%s %s %s>' % (self.mode, self.product, self.path)


def enumerate_hid_paths():
    """枚举全部 HID 设备接口路径（multi-sz，需按 utf-16-le 拆分）。"""
    size = wt.DWORD(0)
    if cfgmgr32.CM_Get_Device_Interface_List_SizeW(ctypes.byref(size), ctypes.byref(HID_GUID),
                                                   None,
                                                   CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != 0:
        return []
    buf = ctypes.create_string_buffer(size.value * 2)
    if cfgmgr32.CM_Get_Device_Interface_ListW(ctypes.byref(HID_GUID), None, buf, size.value,
                                              CM_GET_DEVICE_INTERFACE_LIST_PRESENT) != 0:
        return []
    return [p for p in buf.raw.decode('utf-16-le').split('\0') if p]


def find_channels():
    """返回 (usb_channels, dongle_channels)。只读取，不写入。

    只探测 MI_03：官方驱动也只碰它需要的接口，遍历打开键盘输入(MI_00)、
    LCD 图片(MI_02)等无关接口可能把固件带入异常状态。
    """
    usb, dongle = [], []
    for path in enumerate_hid_paths():
        up = path.upper()
        if 'VID_%04X&PID_%04X' % (VID, PID) not in up:
            continue
        if '&MI_03' not in up:
            continue
        h = open_device(path, write=False)
        if h is None:
            continue
        try:
            vid, pid, caps = caps_of(h)
            if (vid, pid) != (VID, PID) or caps is None:
                continue
            prod = product_of(h)
            # 有线：键盘本体上唯一带 65 字节 Feature Report 的接口（MI_03, UP=0xFF13）
            if caps['feature_len'] == 65 and 'DONGLE' not in prod.upper():
                usb.append(Channel(path, 'usb', prod, caps))
            # 2.4G：接收器上 33 字节输出报告接口（MI_03, UP=0xFF60）
            elif caps['output_len'] == 33 and caps['usage_page'] == 0xFF60:
                dongle.append(Channel(path, '24g', prod, caps))
        finally:
            kernel32.CloseHandle(h)
    return usb, dongle


# ------------------------------------------------------------------ 报文构造

def build_usb_frame(dt, screen=1):
    """有线时间报文（65 字节 Feature Report）。

        [0] 0x00  报告 ID
        [1] 0x00  固定 0（发送封装右移插入的额外字节）
        [2] 屏幕序号+1      [3] 0x5A
        [4] 年-2000  [5] 月  [6] 日  [7] 时  [8] 分  [9] 秒
        [10] 0x00    [11] 星期（0=周日）
        [63] 0xAA    [64] 0x55      ← 必须是 63/64，写 62/63 会埋雷
    """
    f = bytearray(65)
    f[0] = 0x00
    f[1] = 0x00
    f[2] = screen & 0xFF
    f[3] = 0x5A
    f[4] = (dt.year - 2000) & 0xFF
    f[5] = dt.month
    f[6] = dt.day
    f[7] = dt.hour
    f[8] = dt.minute
    f[9] = dt.second
    f[10] = 0x00
    f[11] = (dt.weekday() + 1) % 7    # Windows: 0=Sunday
    f[63] = 0xAA
    f[64] = 0x55
    return bytes(f)


def build_usb_handshake1():
    """握手 1：00 04 18 00…00"""
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x18
    return bytes(f)


def build_usb_handshake2():
    """握手 2：00 04 28 00 00 00 00 00 00 01 00…00（0x01 在索引 9）"""
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x28
    f[9] = 0x01
    return bytes(f)


def build_usb_commit():
    """提交：00 04 02 00…00（触发键盘应用刚写入的时间）"""
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x02
    return bytes(f)


def build_24g_frame(dt, screen=1):
    """无线报文（33 字节 Output Report）。

        [0] 0x00   [1..4] 0C 10 00 00   [5] 屏幕序号+1   [6] 0x5A
        [7..12] 年/月/日/时/分/秒   [13] 0x00   [14] 0x01（固定，非星期）
        [18] 0xAA  [19] 0x55
        [32] 校验 = XOR(f[5:32])
    """
    f = bytearray(33)
    f[0] = 0x00
    f[1] = 0x0C
    f[2] = 0x10
    f[3] = 0x00
    f[4] = 0x00
    f[5] = screen & 0xFF
    f[6] = 0x5A
    f[7] = (dt.year - 2000) & 0xFF
    f[8] = dt.month
    f[9] = dt.day
    f[10] = dt.hour
    f[11] = dt.minute
    f[12] = dt.second
    f[13] = 0x00
    f[14] = 0x01          # 固定 0x01
    f[18] = 0xAA
    f[19] = 0x55
    acc = 0
    for b in f[5:32]:     # 校验 = XOR(f[5:32])
        acc ^= b
    f[32] = acc
    return bytes(f)


# ---------------------------------------------------------------------- 发送

def usb_sync(path, dt, screen=1, sleep_ms=10):
    """有线时间同步：握手1 -> 握手2 -> 时间报文 -> 提交。

    每条 SetFeature 后必须 Sleep 再 GetFeature：
      - 0ms      → 提交不被处理（resp[4]=0）
      - ≥120ms   → 时间→提交间隔超时（resp[4]=255）
      - 安全区间约 [2ms, 100ms]，取 10ms
    """
    h = open_device(path, write=True, overlapped=True)
    if h is None:
        return False, 'CreateFile 失败 (%d)' % ctypes.get_last_error()
    try:
        seq = [
            ('握手1', build_usb_handshake1()),
            ('握手2', build_usb_handshake2()),
            ('时间报文', build_usb_frame(dt, screen)),
            ('提交', build_usb_commit()),
        ]
        for label, frame in seq:
            buf = ctypes.create_string_buffer(bytes(frame), 65)
            if not hid.HidD_SetFeature(h, buf, 65):
                return False, '%s: HidD_SetFeature 失败 (%d)' % (label, ctypes.get_last_error())
            kernel32.Sleep(sleep_ms)
            rbuf = ctypes.create_string_buffer(65)
            hid.HidD_GetFeature(h, rbuf, 65)   # 同步等待键盘处理（可在此校验回显，见 SPEC.md 第 5 节）
        return True, None
    finally:
        kernel32.CloseHandle(h)


def send_output(path, frame, report_len=33):
    """无线：WriteFile 输出报告。句柄不可用 OVERLAPPED 打开（否则报错 87）。"""
    h = open_device(path, write=True)
    if h is None:
        return False, 'CreateFile 失败 (%d)' % ctypes.get_last_error()
    try:
        data = bytes(frame)
        if len(data) < report_len:
            data = data + b'\x00' * (report_len - len(data))
        written = wt.DWORD(0)
        buf = ctypes.create_string_buffer(data, len(data))
        ok = kernel32.WriteFile(h, buf, len(data), ctypes.byref(written), None)
        if not ok and ctypes.get_last_error() == 997:   # ERROR_IO_PENDING
            ok = True
        return bool(ok), None if ok else 'WriteFile 失败 (%d)' % ctypes.get_last_error()
    finally:
        kernel32.CloseHandle(h)


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description='helloganss 键盘屏幕时间同步（替代官方驱动）')
    ap.add_argument('-t', '--time', type=str, help='自定义时间 HH:MM:SS')
    ap.add_argument('-d', '--date', type=str, help='自定义日期 YYYY-MM-DD')
    ap.add_argument('--mode', choices=['auto', 'usb', '24g'], default='auto',
                    help='auto=优先有线 (默认)')
    ap.add_argument('--screen', type=int, default=1, help='屏幕序号+1，默认 1')
    ap.add_argument('--list', action='store_true', help='只列出识别到的通道')
    ap.add_argument('--dry-run', action='store_true', help='只打印报文，不发送')
    ap.add_argument('--debug', action='store_true',
                    help='输出详细调试信息（默认静默，成功时不打印任何内容）')
    args = ap.parse_args()

    def info(msg):
        if args.debug:
            print(msg)

    now = datetime.datetime.now()
    if args.date:
        y, m, d = (int(x) for x in args.date.split('-'))
        now = now.replace(year=y, month=m, day=d)
    if args.time:
        hh, mm, ss = (int(x) for x in args.time.split(':'))
        now = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    else:
        now = now.replace(microsecond=0)

    usb, dongle = find_channels()

    # --list 是显式查询，始终输出（不受 --debug 影响）
    if args.list:
        print('有线(USB)通道：')
        for c in usb or []:
            print('   %s  UP=0x%04X U=0x%02X FEAT=%d' %
                  (c.product, c.caps['usage_page'], c.caps['usage'], c.caps['feature_len']))
            print('      %s' % c.path)
        print('2.4G 通道：')
        for c in dongle or []:
            print('   %s  UP=0x%04X U=0x%02X OUT=%d' %
                  (c.product, c.caps['usage_page'], c.caps['usage'], c.caps['output_len']))
            print('      %s' % c.path)
        if not usb and not dongle:
            print('未找到键盘（VID_%04X PID_%04X）。' % (VID, PID))
            return 1
        return 0

    if not usb and not dongle:
        print('未找到键盘（VID_%04X PID_%04X）。' % (VID, PID))
        return 1

    if args.mode == 'auto':
        mode = 'usb' if usb else '24g'
    else:
        mode = args.mode

    if mode == 'usb':
        if not usb:
            print('未找到有线通道（需要键盘本体带 65 字节 Feature Report 的接口）。')
            return 2
        ch = usb[0]
        frame = build_usb_frame(now, args.screen)
        how = 'HidD_SetFeature 65B -> %s' % ch.product
    else:
        if not dongle:
            print('未找到 2.4G 接收器通道。')
            return 2
        ch = dongle[0]
        frame = build_24g_frame(now, args.screen)
        how = 'WriteFile 33B -> %s' % ch.product

    info('模式       : %s' % ('有线 USB' if mode == 'usb' else '2.4G 无线'))
    info('通道       : %s' % ch.path)
    info('目标时间   : %s' % now.strftime('%Y-%m-%d %H:%M:%S'))
    info('传输方式   : %s' % how)
    info('报文(%d字节): %s' % (len(frame), ' '.join('%02X' % b for b in frame)))

    if args.dry_run:
        print('报文(%d字节): %s' % (len(frame), ' '.join('%02X' % b for b in frame)))
        return 0

    if mode == 'usb':
        ok, err = usb_sync(ch.path, now, args.screen)
    else:
        ok, err = send_output(ch.path, frame, 33)
    if ok:
        info('OK 时间同步完成。')
        return 0
    print('FAIL %s' % err)
    return 3


if __name__ == '__main__':
    sys.exit(main())
```

---

## 7. 已知的坑（按踩坑代价排序）

| # | 坑 | 症状 | 正确做法 |
|---|---|---|---|
| 1 | 有线时间报文 `AA 55` 写在索引 62/63 | 冷启动能生效，**模式切换（有线→无线→有线）后失效，且官方驱动也救不回来** | 必须写 `[63]=0xAA, [64]=0x55` |
| 2 | 报文整体错位 1 字节 | 键盘能收但不应用 | 源缓冲布局 + 发送封装右移，要**叠加**计算（见 3.4） |
| 3 | 漏发提交命令 `04 02` | 时间被正确接收、回显也正确，但屏幕不刷新 | 必须发完整 4 条 |
| 4 | Sleep 取 0ms 或 ≥120ms | 提交 `resp[4]=0`（太短）或 `=255`（太长） | 取 10ms，区间 [2, 100]ms |
| 5 | 遍历打开无关接口（MI_00/MI_02） | 键盘固件进入异常状态 | 只探测 MI_03 |
| 6 | 无线用 OVERLAPPED 句柄 + `WriteFile(..., NULL)` | `ERROR_INVALID_PARAMETER (87)` | 无线用非 OVERLAPPED 句柄 |
| 7 | 无线校验写成累加和 | 键盘拒收 | 用 **XOR** `f[5:32]` |
| 8 | 无线索引 14 写成星期 | 键盘拒收 | 固定 `0x01` |
| 9 | 把无线的 33 字节思路套到有线 | 发不进去 | 两套协议完全不同 |
| 10 | 用 `create_unicode_buffer().value` 读设备列表 | 只拿到第一个路径 | 按 `utf-16-le` 解原始字节再 `split('\0')` |
| 11 | 读报文把十六进制当十进制 | 误判时间不对 | `0x27=39`、`0x33=51` |

---

## 8. 排障表

| 现象 | 定位 |
|---|---|
| `--list` 找不到任何通道 | 键盘未连接 / 模式未切换到位；确认 VID/PID `05AC:024F` |
| 找到有线通道但 `CreateFile` 失败 | 官方驱动可能独占；关掉 `DeviceDriver.exe` 再试 |
| 握手 `resp[4] != 1` | 通道选错（拿到 DONGLE 或 MI_02 了） |
| 时间回显与发送不符 | 报文字段错位，逐字节比对第 3.2 节表 |
| 提交 `resp[4] = 0` | Sleep 太短 |
| 提交 `resp[4] = 255` | Sleep 太长（或时间→提交间隔超时） |
| `WriteFile` 返回 87 | 句柄用了 OVERLAPPED（见坑 6） |
| 一切正常但屏幕不变 | 漏了提交命令，或 `AA 55` 位置错（坑 1、3） |

---

## 9. 打包与自动运行

```bash
pyinstaller --onefile --noconsole --name timeupdater timeupdater.py   # 单文件，无窗口
pyinstaller --onedir  --noconsole --name timeupdater timeupdater.py   # 目录版，启动更快
```

- `console=False` → exe 的 PE Subsystem = 2（WINDOWS_GUI），双击无窗口。
- **onefile 启动较慢**（本机 5~7 秒）：每次运行要把整个包解压到 `%TEMP%\_MEI*`，被 Defender 实时扫描。
  用 `--dry-run`（不碰键盘）测也是同样耗时，可证明是纯启动开销。
  缓解：把 exe 加进 Defender 排除项，或改用 `--onedir`（约 1.1 秒）。
- 脚本零第三方依赖，打包不需要 `hiddenimports`。

**开机/登录自动同步**（脚本默认静默、成功退出码 0，适合无人值守）：

```powershell
schtasks /Create /TN "KbdTimeSync" /TR "\"D:\path\to\timeupdater.exe\"" /SC ONLOGON /RL LIMITED /F
```

---

## 附录 A：逆向溯源（DeviceDriver.exe，ImageBase 0x400000）

| 地址 | 作用 |
|---|---|
| `0x00451280` | 动态加载 `hid.dll`，`GetProcAddress` 解析 9 个 `HidD_*` / `HidP_GetCaps` |
| `0x00451390` | 枚举 HID 设备：SetupDi + `SPDRP_CLASS == "HIDClass"`，从路径 `&mi_` 后解析接口号 |
| `0x004506C0` | `GetDev`：按 `device_type` + `mode`（0=USB，1=BT，2=2.4G）选取已打开设备 |
| `0x00451810` | `OpenDev`：`CreateFileW(GENERIC_READ\|WRITE, FILE_FLAG_OVERLAPPED)` + `HidP_GetCaps` |
| `0x00451BD0` | `HidD_SetFeature(h, buf, 0x41)` |
| `0x004519B0` | 输出报告：补齐到 `OutputReportByteLength` 后 `WriteFile` |
| `0x0044F660` | 通用发送（有线）：载荷 ≤64B 走 SetFeature；>64B 分片；flag=1 时 `Sleep` + 读响应校验 `resp[4]==1` |
| `0x0044FD80` | 通用发送（2.4G）：算校验和写入 `buf[32]`，`WriteFile` 33 字节 |
| `0x00423680` | **有线时间同步**：`GetLocalTime` → 4 条 65B SetFeature |
| `0x004237B0` | **2.4G 时间同步**：`GetLocalTime` → 33B WriteFile |
| `0x00434A97` | 模式分支：`[esi+0x784]==0` → 走 `0x423680`（有线）；`==2` → 走 `0x4237B0`（2.4G） |

> `hid.dll` 是**运行时 `LoadLibraryA` 动态加载**的，所以这些 API 不出现在 PE 导入表里——
> 静态分析时搜导入表会一无所获，要先跟 `0x00451280` 的 `GetProcAddress` 调用。

## 附录 B：如何自己抓包复现

本机没有 Wireshark / USBPcap，改用 **Frida** hook `HidD_SetFeature` 抓取官方驱动真实报文：

```python
import frida, time
exe = r'D:\Program Files (x86)\helloganss Driver\DeviceDriver.exe'
pid = frida.spawn([exe]); s = frida.attach(pid)
sc = s.create_script('''
function getExport(mod, name){
  var m = Process.findModuleByName(mod);
  return m ? m.getExportByName(name) : null;
}
function install(){
  var sf = getExport('hid.dll', 'HidD_SetFeature');
  if (!sf) return;
  Interceptor.attach(sf, {
    onEnter: function(a){
      var len = a[2].toInt32();
      var u8 = new Uint8Array(a[1].readByteArray(len));   // ← 必须在 onEnter 读
      var s = '';
      for (var i = 0; i < u8.length; i++) s += ('0'+u8[i].toString(16)).slice(-2) + ' ';
      send({evt:'SF', len:len, data:s});
    }
  });
}
var n = 0;
var t = setInterval(function(){
  n++; if (Process.findModuleByName('hid.dll') || n > 40) { clearInterval(t); install(); }
}, 200);
''')
sc.on('message', lambda m, d: print(m.get('payload') if m['type']=='send' else m.get('description')))
sc.load(); frida.resume(pid); time.sleep(15)
```

**注意事项**：

- Frida 17.x 移除了 `Module.findExportByName` 静态方法，要用
  `Process.findModuleByName(mod).getExportByName(name)`。
- `hid.dll` 是延迟加载的，脚本加载时还没进进程——用 `setInterval` 轮询等它出现再 hook。
- **必须在 `onEnter` 里读 buffer**；`onLeave` 时缓冲区可能已失效。
- 官方驱动**只在启动/重连时同步一次**，已经连稳之后不会再发包。要抓就用 `frida.spawn` 从启动开始挂。

---

## 附录 C：未覆盖范围

- **屏幕序号 > 1**：未实测（默认 1）。
- **蓝牙（BT，mode=1）**：官方驱动自身也未实现 BT 时间同步。
- **LCD 图片通道（MI_02，OUT=4097）**、固件写入：不在本次范围内，未触碰。

---

## 附录 D：逆向方法论（如何自己把这套协议扒出来）

前面所有结论都是**结果**。这一节讲**过程**——面对一个闭源驱动，怎样从零推出协议。
这套流程不依赖特定厂商，对同类「官方驱动 + 自定义 HID 设备」基本通用。

### D.1 总流程

```
① 盘点指纹   →  程序是什么语言/打包/架构？有无现成符号？
② 找通信 API →  它用什么跟设备说话？（静态 + 动态加载都要查）
③ 回溯业务   →  谁在调这些 API？哪个函数在做「时间同步」？
④ 还原报文   →  逐条指令还原缓冲区布局 → 得到字段表（假设）
⑤ 只读探测   →  用 HidP_GetCaps 确认设备真实能力，校验假设是否自洽
⑥ 动态抓包   →  hook 真实调用，拿到官方发出的确切字节（对照假设）
⑦ A/B 实验   →  改写官方报文做对照，把「报文错」和「时序错」一分为二
⑧ 二分调参   →  定位时序等参数型问题
```

**核心原则**：④ 得到的只是**假设**，必须经 ⑥⑦ 验证。本项目静态推理错了 3 次
（报文整体错位、漏握手命令、AA/55 错位），全靠 ⑥⑦ 才纠正。

### D.2 各步骤的手法

#### ① 盘点指纹

```python
# 是不是 PyInstaller 打包？文件尾找 cookie
MAGIC = b'MEI\x0c\x0b\x0a\x0b\x0e'      # 注意是 \x0c\x0b\x0a\x0b\x0e
cookie_pos = data.rfind(MAGIC)
# cookie 后是: lenPackage(I) toc(I) tocLen(I) pyvers(I) pylibname(64s)
# 解析 TOC → 逐项 zlib 解压 → 拿到 .pyc → marshal.loads → dis 反汇编字节码
```

有源码（哪怕字节码）就先看源码：本项目通过解包用户原来的 `timeupdater.exe`，
直接拿到了**无线协议的完整报文构造**，省掉一半工作量。

同时看 PE 头：`Machine` 判断 32/64 位，`DIRECTORY_ENTRY_IMPORT` 看导入表。

#### ② 找通信 API（关键陷阱：动态加载）

先搜导入表。但本项目 **`hid.dll` 的所有函数都不在导入表里**——
官方驱动是运行时 `LoadLibraryA("hid.dll")` + `GetProcAddress` 动态解析的。

识别方法：

1. 字符串表里搜 `hid.dll`、`HidD_SetFeature` 等名字；
2. 反汇编引用这些字符串的函数（本项目是 `0x00451280`），
   看它把 `GetProcAddress` 的返回值存到哪个**全局变量**；
3. 建立「全局地址 → API 名」映射表：

   | 全局变量 | API |
   |---|---|
   | `0x5950A0` | `HidD_SetFeature` |
   | `0x59509C` | `HidD_GetFeature` |
   | `0x5950AC` | `HidP_GetCaps` |
   | `0x5950A8` | `HidD_GetPreparsedData` |

4. 之后凡是 `call [0x5950A0]` 就是 `HidD_SetFeature`。

#### ③ 回溯：谁在调它

> **坑 1（最重要）**：文件偏移换算公式
> ```
> file_offset = (VA - ImageBase) - Section.VirtualAddress + Section.PointerToRawData
> ```
> 漏掉 `PointerToRawData` 会让整段反汇编错位（本项目错了一整个 0x400），
> 表现为「地址对不上、指令全乱」。

> **坑 2**：线性扫描反汇编在 MSVC 代码上会误判 `call` 目标（把数据当指令）。
> 更可靠的做法是**字节扫描 + 逐点校验**：
> ```python
> for j in range(len(data) - 5):
>     if data[j] != 0xE8:            # E8 rel32 = call
>         continue
>     rel = struct.unpack_from('<i', data, j + 1)[0]
>     tgt = va(j) + 5 + rel
>     if tgt in watch_list:
>         ins = next(capstone.disasm(data[j:j+5], va(j), 1), None)
>         if ins and ins.mnemonic == 'call' and ins.size == 5:
>             记录调用点            # 校验过，避免误判
> ```

这样就能从 `HidD_SetFeature` 的封装 → 通用发送函数 → **时间同步函数** 一路回溯上去。
本项目最终定位到 `0x00423680`（有线）与 `0x004237B0`（2.4G），
并通过 `0x00434A97` 处的分支（`[esi+0x784]==0` 走有线、`==2` 走无线）确认了两种模式的对应关系。

#### ④ 还原报文：栈帧逐字节映射

在函数开头找 `memset` 的大小——那就是报文长度：

```asm
push 0x41        ; 65
push 0
push buf
call memset      ; → 报文 65 字节
```

然后逐条记录对 `[ebp-0x??]` 的写入，映射成偏移表：

| 栈偏移 | 相对 buf 的索引 | 值 |
|---|---|---|
| `[ebp-0x50]` | 0 | 0 |
| `[ebp-0x4f]` | 1 | 屏幕序号+1 |
| `[ebp-0x4e]` | 2 | 0x5A |
| `[ebp-0x4d]` | 3 | 年-2000（`div 0x7d0` 的余数） |
| `[ebp-0x4c..-0x48]` | 4..8 | 月/日/时/分/秒 |
| `[ebp-0x46]` | 10 | 星期 |

**三个技巧**：

- **认结构体**：`lea eax,[ebp-0x60]` + `call GetLocalTime` → 那里是 16 字节 `SYSTEMTIME`，
  按 `wYear/wMonth/wDayOfWeek/wDay/wHour/wMinute/wSecond` 对齐，可反推哪些偏移是时间字段。
- **认常量**：`div 0x7d0`（2000）→ 年-2000；`0x55AA` → 结尾标记 AA 55。
- **认封装的二次变换**：本项目 `0x0044F660` 会把源缓冲右移一位再发送（见 3.4）。
  **必须把「源缓冲布局」和「发送封装变换」叠加计算**，只看一层必然错位。

#### ⑤ 只读探测（不写设备）

在动手发命令前，先用 `HidP_GetCaps` 枚举所有接口，拿到 IN/OUT/FEATURE 长度表（见第 1 节）。
作用：验证「有线通道必然是 MI_03」这类假设，同时避免瞎试浪费时间。

### D.3 动态抓包：hook 拿真实字节

代码见附录 B。**三个必须注意的点**：

1. Frida 17.x 移除了 `Module.findExportByName` 静态方法 → 用
   `Process.findModuleByName(mod).getExportByName(name)`。
2. `hid.dll` 延迟加载 → `setInterval` 轮询等它进进程再 hook，否则拿到 null。
3. **必须在 `onEnter` 里读 buffer**，`onLeave` 时缓冲区可能已失效（本项目一开始读全是空）。

抓到的是官方驱动真实发出的字节，与静态假设逐字节比对——本项目就是靠这一步发现了
「报文整体错位一位」和「漏了两条握手命令」。

### D.4 决定性实验：A/B 对照（最有价值的一步）

当「报文逐字节和官方一样，但功能就是不生效」时，问题空间其实只剩两半：

- A：报文内容仍不对（我还原错了）
- B：报文对，但**流程/时序**不对（缺前置步骤、间隔不对）

用 Frida **改写官方驱动自己的报文**就能一分为二：

```javascript
Interceptor.attach(HidD_SetFeature, {
  onEnter: function (a) {
    if (a[2].toInt32() === 65) {
      var u8 = new Uint8Array(a[1].readByteArray(65));
      if (u8[0] === 0 && u8[2] === 1 && u8[3] === 0x5a) {   // 时间报文特征
        u8[4] = 26; u8[5] = 10; u8[6] = 2;
        u8[7] = 12; u8[8] = 34; u8[9] = 56;                 // 改成 12:34:56
        a[1].writeByteArray(u8.buffer);
      }
    }
  }
});
```

- 官方驱动走它自己的完整流程，只是时间值被改成 12:34:56；
- **屏幕变了** → 报文格式正确，问题在 B（流程/时序）；
- **屏幕没变** → 问题在 A（报文内容）。

本项目这一步的结果是「屏幕变了」，于是把排查方向从「继续抠报文」切换到「抠时序」，
很快定位到 `Sleep` 是必需的。

### D.5 参数型问题：二分法 + 逐条读数

时序这类连续参数，用二分 + 逐条读响应定位：

```python
for sleep_ms in [200, 120, 80, 50, 30, 20, 10, 5, 2, 0]:
    跑一次完整序列，每条命令后 Sleep(sleep_ms) 再 GetFeature
    打印每条命令的 resp[4]
```

本项目由此发现一个反直觉的结论：**Sleep 不是越长越好**——
`0ms` 失败（命令没被处理）、`2~100ms` 成功、`≥120ms` 又失败（时间→提交间隔超时）。
如果只按「多等等总没错」的直觉调，永远调不出来。

### D.6 工具清单

| 用途 | 工具 |
|---|---|
| PE 解析 / 导入表 / 节表 | `pefile` |
| 反汇编 | `capstone`（x86 32-bit） |
| 动态 hook 抓包 | `frida` |
| PyInstaller 解包 | 手写脚本（`struct` + `zlib` + `marshal` + `dis`） |
| 设备交互验证 | Python `ctypes` 直接调 `kernel32` / `hid.dll` / `cfgmgr32.dll` |
| 打包 | `pyinstaller` |

全部装进隔离 venv，不污染系统环境。

### D.7 边界与纪律

- **先静态、后动态**；动态部分**只读取、不发未知命令**，确认安全后再回放已验证报文。
- **不做**固件写入、压力测试、破坏性操作——这些不在本次授权范围内。
- **每次只改一个变量**（改报文就别改时序），否则无法归因。
- **静态结论必须动态验证**。本项目静态推理错了 3 次，全靠动态抓包和 A/B 实验纠正。
- 出现设备异常立即停止，由设备所有者决定后续处置。
