import hid
import time
import datetime
import sys
import argparse

# =================配置区域=================
# 保持原有的设备识别码不变
VENDOR_ID = 0x05AC      # VID
PRODUCT_ID = 0x024F     # PID
TARGET_INTERFACE = 3    # MI_03

def get_checksum(data_bytes):
    """
    【底层逻辑-保持不变】
    计算校验和：从偏移量 4 到 30 的累积异或 (XOR)
    """
    xor_result = 0
    for byte in data_bytes[4:31]:
        xor_result ^= byte
    return xor_result

def create_packet(target_time):
    """
    【底层逻辑-微调】
    构建 32 字节的同步指令。
    变动：不再内部调用 now()，而是接收传入的 target_time 对象。
    """
    # 使用传入的时间对象
    now = target_time
    
    # 计算年份 (2000年基准)
    year = now.year - 2000
    
    # 1. 指令头 (4 bytes) - 不变
    packet = [0x0C, 0x10, 0x00, 0x00]
    
    # 2. 标志位 (2 bytes) - 不变
    packet += [0x01, 0x5A]
    
    # 3. 时间数据 (6 bytes) - 不变 (标准数值)
    packet.append(year)         # Year
    packet.append(now.month)    # Month
    packet.append(now.day)      # Day
    packet.append(now.hour)     # Hour
    packet.append(now.minute)   # Minute
    packet.append(now.second)   # Second
    
    # 4. 固定填充数据 (19 bytes) - 不变
    padding = [
        0x00, 0x01, 0x00, 0x00, 
        0x00, 0xAA, 0x55, 0x00
    ]
    while len(packet) + len(padding) < 31:
        padding.append(0x00)
    
    packet += padding
    
    # 5. 计算校验和 (1 byte) - 不变
    checksum = get_checksum(packet)
    packet.append(checksum)
    
    return packet

def find_and_sync(target_time):
    """
    【底层逻辑-微调】
    寻找设备并发送指令。
    变动：接收 target_time 参数传递给 create_packet。
    """
    print(f"正在寻找设备 VID: {hex(VENDOR_ID)}, PID: {hex(PRODUCT_ID)}...")
    
    target_device = None
    
    # 枚举设备逻辑 - 不变
    for d in hid.enumerate():
        if d['vendor_id'] == VENDOR_ID and d['product_id'] == PRODUCT_ID:
            if d.get('interface_number') == TARGET_INTERFACE:
                target_device = d
                break
            elif 'mi_03' in d['path'].decode('utf-8', errors='ignore').lower():
                target_device = d
                break
    
    if not target_device:
        print("❌ 未找到指定的键盘接口 (MI_03)。")
        return

    print(f"找到设备，路径: {target_device['path']}")
    
    try:
        h = hid.device()
        h.open_path(target_device['path'])
        
        # 使用传入的时间构建包
        packet = create_packet(target_time)
        
        # 打印信息用于确认
        print(f"目标同步时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}")
        # print("Hex:", " ".join([f"{b:02x}" for b in packet])) # 调试用，平时可注释
        
        # 发送逻辑 - 不变
        data_to_send = [0x00] + packet
        res = h.write(data_to_send)
        
        if res < 0:
             res = h.write(packet)
             
        if res > 0:
            print("✅ 时间同步指令发送成功！")
        else:
            print("❌ 发送失败，返回值:", res)
            
        h.close()
        
    except Exception as e:
        print(f"❌ 发生错误: {e}")

# ================= 主程序入口 (新增参数解析) =================
if __name__ == "__main__":
    # 1. 定义命令行参数
    parser = argparse.ArgumentParser(description="键盘时间同步工具")
    
    parser.add_argument(
        '-t', '--time', 
        type=str, 
        help="自定义时间，格式 HH:MM:SS (例如 18:30:00)"
    )
    
    parser.add_argument(
        '-d', '--date', 
        type=str, 
        help="自定义日期，格式 YYYY-MM-DD (例如 2025-05-20)"
    )
    
    args = parser.parse_args()
    
    # 2. 获取基础时间 (当前系统时间)
    final_time = datetime.datetime.now()
    
    # 3. 如果有自定义参数，覆盖基础时间
    if args.date:
        try:
            y, m, d = map(int, args.date.split('-'))
            final_time = final_time.replace(year=y, month=m, day=d)
        except ValueError:
            print("❌ 日期格式错误！请使用 YYYY-MM-DD")
            sys.exit(1)
            
    if args.time:
        try:
            h, m, s = map(int, args.time.split(':'))
            final_time = final_time.replace(hour=h, minute=m, second=s, microsecond=0)
        except ValueError:
            print("❌ 时间格式错误！请使用 HH:MM:SS")
            sys.exit(1)

    # 4. 执行同步
    find_and_sync(final_time)