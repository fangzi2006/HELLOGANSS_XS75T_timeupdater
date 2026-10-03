#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
helloganss XS75T / XS98T 键盘屏幕时间同步（无需官方驱动）
========================================================

完整的协议规范、字段定义、时序要求与踩坑清单见同目录 / 上级目录的 **SPEC.md**。
这里只做一句话速览：设备 VID=0x05AC / PID=0x024F，官方驱动有两条完全不同的通道。

1) USB / 有线模式（键盘本体，Manufacturer = SONiX）
   - 通道：键盘本体 HID 接口 MI_03（UsagePage 0xFF13 / Usage 0x0001）
   - 传输：HidD_SetFeature，65 字节 Feature Report
   - 命令：握手1(04 18) -> 握手2(04 28) -> 时间报文(00 01 5A ...) -> 提交(04 02)，共 4 条
   - 关键：每条命令后必须 Sleep 约 10ms 再 GetFeature；末尾 AA/55 在索引 63/64
   - 无校验和

2) 2.4G 无线模式（接收器，Product = "2.4G Dongle"）
   - 通道：接收器 HID 接口 MI_03（UsagePage 0xFF60 / Usage 0x0061）
   - 传输：WriteFile，33 字节输出报告，仅 1 条，无需握手
   - 校验和 = XOR(buf[5:32])；索引 14 是固定 0x01（不是星期）
   - 句柄不可用 FILE_FLAG_OVERLAPPED 打开（否则 WriteFile 报错 87）

用法
----
    timeupdater.exe                      # 自动识别模式，同步为当前系统时间（静默）
    python timeupdater.py --debug        # 输出详细调试信息
    python timeupdater.py --list         # 只列出识别到的通道，不发送
    python timeupdater.py --dry-run      # 打印将要发送的报文，不发送
    timeupdater.exe -t 18:30:00 -d 2026-10-02
    timeupdater.exe --mode usb           # 强制有线
    timeupdater.exe --mode 24g           # 强制 2.4G

依赖：仅 Python 标准库 + Windows 自带 hid.dll / cfgmgr32.dll / kernel32.dll（无需 pip 装任何包）。
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
    access = 0
    if write:
        access = GENERIC_READ | GENERIC_WRITE
    flags = FILE_FLAG_OVERLAPPED if overlapped else 0
    h = kernel32.CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, flags, None)
    return None if h == INVALID_HANDLE else h


def caps_of(h):
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
    if hid.HidD_GetProductString(h, buf, 128):
        return buf.value
    return ''


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
    """返回 (usb_channels, dongle_channels)。只读取，不写入。"""
    usb, dongle = [], []
    for path in enumerate_hid_paths():
        up = path.upper()
        if 'VID_%04X&PID_%04X' % (VID, PID) not in up:
            continue
        # 只探测 MI_03（时间同步通道）。官方驱动只碰它需要的接口，
        # 遍历打开键盘输入接口(MI_00)、LCD 图片接口(MI_02)等无关接口，
        # 可能把键盘固件带入异常状态（切换模式后尤其明显）。
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
    """65 字节 Feature Report（有线 USB）。

    布局来自官方驱动抓包（Frida hook HidD_SetFeature）逐字节比对确认：
        [0]  0x00        report id
        [1]  0x00        （固定 0，发送封装插入的额外字节）
        [2]  屏幕序号+1（默认 1）
        [3]  0x5A
        [4]  年 - 2000
        [5]  月
        [6]  日
        [7]  时
        [8]  分
        [9]  秒
        [10] 0x00
        [11] 星期（Windows: 0=周日）
        [12..62] 0x00
        [63] 0xAA
        [64] 0x55
    """
    f = bytearray(65)
    f[0] = 0x00                       # report id
    f[1] = 0x00                       # 额外字节（发送封装右移所致）
    f[2] = screen & 0xFF              # 屏幕序号 + 1
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
    """有线握手命令 1：65 字节 = 00 04 18 00...00。

    来自官方抓包：word[0]=0x1804（小端 -> 04 18），其余清零。
    """
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x18
    return bytes(f)


def build_usb_handshake2():
    """有线握手命令 2：65 字节 = 00 04 28 00 00 00 00 00 00 01 00...00。

    来自官方抓包：word[0]=0x2804（-> 04 28），byte[9]=0x01（注意在索引 9），其余清零。
    """
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x28
    f[9] = 0x01
    return bytes(f)


def build_usb_commit():
    """有线提交命令：65 字节 = 00 04 02 00...00（触发键盘应用刚写入的时间）。"""
    f = bytearray(65)
    f[1] = 0x04
    f[2] = 0x02
    return bytes(f)


def build_24g_frame(dt, screen=1):
    """33 字节输出报告（2.4G 无线）。与用户原 timeupdater.py 完全一致。

    布局（索引 0 起）：
        0   报告 ID（0x00）
        1..4  0C 10 00 00
        5     0x01（屏幕序号+1）
        6     0x5A
        7..12 年/月/日/时/分/秒
        13    0x00
        14    0x01（固定）
        15..17 0x00
        18    0xAA
        19    0x55
        20..31 0x00
        32    校验 = XOR(f[5:32])
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
    for b in f[5:32]:     # 校验 = XOR(f[5:32])，与原脚本 data_bytes[4:31] 一致
        acc ^= b
    f[32] = acc
    return bytes(f)


def send_feature(h, frame):
    """发送一条 65 字节 Feature Report，返回 (ok, err)。"""
    buf = ctypes.create_string_buffer(bytes(frame), 65)
    ok = hid.HidD_SetFeature(h, buf, 65)
    return bool(ok), None if ok else 'HidD_SetFeature 失败 (%d)' % ctypes.get_last_error()


def usb_sync(path, dt, screen=1, sleep_ms=10):
    """有线时间同步：握手1 -> 握手2 -> 时间报文 -> 提交命令。

    关键时序（实测确定）：
    - 每条命令 HidD_SetFeature 之后必须 Sleep 一小段时间再 GetFeature；
    - Sleep 过短（0ms）提交命令不被处理（resp[4]=0），
      Sleep 过长（≥约120ms）会让时间报文与提交之间的间隔超时（resp[4]=255）；
    - 实测安全区间约 [2ms, 100ms]，取 10ms 留足余量。
      总耗时主要由 4 次 GetFeature 的 USB 同步 I/O 决定（约 60~70ms）。
    """
    # 与官方驱动一致：以 FILE_FLAG_OVERLAPPED 打开
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
            hid.HidD_GetFeature(h, rbuf, 65)   # 同步等待键盘处理（不强制判结果）
        return True, None
    finally:
        kernel32.CloseHandle(h)


def send_output(path, frame, report_len=33):
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
            written = wt.DWORD(len(data))
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
        """详细/调试信息：仅在 --debug 时输出。"""
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

    # --list 总是输出通道列表（显式查询，不受 --debug 影响）
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

    mode = args.mode
    if mode == 'auto':
        mode = 'usb' if usb else '24g'

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
        # dry-run 的核心用途是预览报文，因此报文总是输出
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
