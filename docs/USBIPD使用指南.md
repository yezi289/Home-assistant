# USBIPD 使用指南（小白版）

## 什么是 USBIPD？
USBIPD 是一个工具，可以让你把插在 Windows 电脑上的 USB 设备（比如 Zigbee 网关、U盘等）共享给 WSL2（Linux 子系统）使用。

## 常用命令说明

### 1. 查看所有 USB 设备
```bash
usbipd list
```
**作用**：显示所有连接到电脑的 USB 设备列表，包括已绑定和未绑定的。

### 2. 绑定设备（首次使用必须做）
```bash
usbipd bind --busid <设备ID>
```
**作用**：允许设备可以被共享。例如：
```bash
usbipd bind --busid 1-4
```
⚠️ **注意**：需要管理员权限（以管理员身份运行 PowerShell）

### 3. 附加设备到 WSL
```bash
usbipd attach --wsl --busid <设备ID>
```
**作用**：将设备连接到 WSL2，这样 WSL 里的程序就能使用这个 USB 设备了。
```bash
usbipd attach --wsl --busid 1-4
```

### 4. 分离设备
```bash
usbipd detach --busid <设备ID>
```
**作用**：断开 WSL 与设备的连接，设备回到 Windows。

### 5. 解除绑定
```bash
usbipd unbind --busid <设备ID>
```
**作用**：取消设备的共享权限。

## 实际操作步骤（以 Zigbee 网关为例）

### 步骤 1：查看设备
```bash
usbipd list
```
输出示例：
```
BUSID  VID:PID    DEVICE                          STATE
1-4    10c4:ea60  CP210x USB to UART Bridge       Not shared
2-3    0bda:5689  Realtek USB Card Reader         Not shared
```
记住你要用的设备的 `BUSID`（比如 `1-4`）

### 步骤 2：绑定设备（管理员权限）
```bash
# 以管理员身份运行 PowerShell，然后执行：
usbipd bind --busid 1-4
```

### 步骤 3：附加到 WSL
```bash
# 普通 PowerShell 就可以
usbipd attach --wsl --busid 1-4
```

### 步骤 4：在 WSL 中验证
```bash
# 在 WSL 终端中执行
ls /dev/ttyUSB*
# 应该能看到 /dev/ttyUSB0 或类似设备
```

### 步骤 5：在 docker-compose.yml 中使用
```yaml
services:
  homeassistant:
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0  # 将设备映射到容器
```

## 常见问题

**Q: 每次重启都要重新操作吗？**
- `bind` 操作是持久的，只需做一次
- `attach` 操作不持久，每次 WSL 重启或设备重新插拔后需要重新执行

**Q: 提示权限不够怎么办？**
- `bind` 命令需要管理员权限，右键 PowerShell 选择"以管理员身份运行"

**Q: WSL 中看不到设备？**
- 先执行 `usbipd attach`
- 在 WSL 中用 `dmesg | tail` 查看设备是否被识别

**Q: 如何自动附加设备？**
- 可以创建 Windows 计划任务或启动脚本自动执行 `usbipd attach` 命令

**Q: 设备被占用怎么办？**
- 先在设备管理器中禁用该设备
- 或者关闭占用设备的程序后再 attach

## 自动化脚本示例

### PowerShell 自动附加脚本
创建文件 `attach-usb.ps1`：
```powershell
# 自动附加 USB 设备到 WSL
$BUSID = "1-4"  # 修改为你的设备 BUSID

Write-Host "正在附加设备 $BUSID 到 WSL..."
usbipd attach --wsl --busid $BUSID

if ($LASTEXITCODE -eq 0) {
    Write-Host "设备附加成功！" -ForegroundColor Green
} else {
    Write-Host "设备附加失败，请检查设备是否已绑定或被占用。" -ForegroundColor Red
}
```

运行脚本：
```bash
.\attach-usb.ps1
```

## 快速参考卡片

| 操作 | 命令 | 权限 | 频率 |
|------|------|------|------|
| 查看设备 | `usbipd list` | 普通 | 随时 |
| 绑定设备 | `usbipd bind --busid X-X` | 管理员 | 仅一次 |
| 连接到WSL | `usbipd attach --wsl --busid X-X` | 普通 | 每次重启后 |
| 断开连接 | `usbipd detach --busid X-X` | 普通 | 需要时 |
| 解除绑定 | `usbipd unbind --busid X-X` | 管理员 | 需要时 |

## Home Assistant 集成说明

### 1. 常见设备类型
- **Zigbee 网关**：如 ConBee II、通用 Zigbee USB Dongle
- **Z-Wave 控制器**：如 Aeotec Z-Stick
- **蓝牙适配器**：用于蓝牙设备集成

### 2. 在 docker-compose.yml 中配置
```yaml
services:
  homeassistant:
    container_name: homeassistant
    image: homeassistant/home-assistant:latest
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0  # Zigbee/Z-Wave 设备
      - /dev/ttyACM0:/dev/ttyACM0  # 备选设备路径
    # ... 其他配置
```

### 3. 检查设备在容器中是否可用
```bash
# 进入容器
docker exec -it homeassistant bash

# 查看设备
ls -l /dev/tty*

# 退出容器
exit
```

## 故障排除

### 问题：attach 后 WSL 中找不到设备
**解决方案**：
1. 检查 WSL 内核是否支持 USB：`uname -r`（需要 5.10.60.1 或更高版本）
2. 更新 WSL：`wsl --update`
3. 重启 WSL：`wsl --shutdown`，然后重新打开

### 问题：提示 "Access is denied"
**解决方案**：
- 以管理员身份运行 PowerShell
- 检查是否安装了 USBIPD 服务

### 问题：设备一直显示 "Not shared"
**解决方案**：
- 先执行 `usbipd bind --busid X-X`（需要管理员权限）
- 再执行 `usbipd attach --wsl --busid X-X`

---

**提示**：建议把常用的设备 BUSID 记录下来，方便以后使用。
