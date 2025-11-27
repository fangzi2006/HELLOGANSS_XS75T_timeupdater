
# ⌨️ 键盘时间同步工具

这是一个用于在2.4g模式同步 **xs75t 键盘**（VID: `05AC`, PID: `024F`）在屏幕时间的工具。其实就是有点精神洁癖，不想一直挂着驱动，但是这破键盘几天不同步时间就会有误差，所以<del>让AI写了这个程序</del>在AI的辅助下写了个程序

> **注意**：由于有线模式（USB）与无线模式（2.4G 接收器）使用了不同的通信协议<del>加上我懒</del>，**本工具仅支持通过 2.4G 接收器连接时的同步操作**。

---

## ⚙️ 开发者与打包说明 (Python)

如果您想从源代码运行或自行打包，请参考以下步骤。

### 1. 依赖安装

```bash
pip install hidapi
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

## 📦 使用方法

请从 [Release 页面](https://github.com/fangzi2006/HELLOGANSS_XS75T_timeupdater/releases) 下载 `timeupdater.exe` 文件。

### 默认行为

如果不带任何参数运行，程序将自动获取当前电脑的系统时间并同步到键盘。

### 支持的参数

| 参数 | 全称 | 格式 | 示例 |
|------|------|------|------|
| `-t` | `--time` | `HH:MM:SS` | `20:30:00` |
| `-d` | `--date` | `YYYY-MM-DD` | `2025-11-24` |

### 示例：设置指定日期和时间

将键盘时间设置为 **2025年11月24日 20点30分00秒**：

```bash
timeupdate.exe -t 20:30:00 -d 2025-11-24

```
