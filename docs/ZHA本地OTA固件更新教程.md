# ZHA 本地 OTA 固件更新教程（开发者版）

## 什么是 ZHA OTA？

**OTA（Over-The-Air）** 是一种无线固件更新技术，可以让你通过 Zigbee 网络为智能设备升级固件，无需拆卸设备或连接数据线。

**ZHA（Zigbee Home Automation）** 是 Home Assistant 内置的 Zigbee 集成，支持通过 OTA 方式更新 Zigbee 设备固件。

### 为什么需要本地 OTA？

- ✅ **开发测试**：你正在开发自己的 Zigbee 固件，需要快速测试新版本
- ✅ **版本控制**：精确控制固件版本，不依赖官方更新
- ✅ **离线更新**：无需联网即可更新设备固件
- ✅ **自定义功能**：测试添加自定义功能的固件

### ZHA vs Zigbee2MQTT

| 特性 | ZHA | Zigbee2MQTT |
|------|-----|-------------|
| 集成方式 | Home Assistant 内置 | 独立服务（MQTT） |
| 配置复杂度 | 简单 | 较复杂 |
| OTA 支持 | 支持本地和远程 | 支持本地和远程 |
| 设备支持 | 广泛 | 更广泛 |

本教程针对 **ZHA** 用户。

---

## 前置条件

在开始之前，请确保：

- ✅ Home Assistant 已安装并运行（本教程基于 Docker 部署）
- ✅ ZHA 集成已配置完成
- ✅ Zigbee 网关已连接（如 通用 Zigbee USB Dongle）
- ✅ Zigbee 设备已成功配对到 Home Assistant
- ✅ 你有需要更新的 `.ota` 固件文件

---

## 配置方式选择

ZHA 支持两种本地 OTA 固件配置方式：

### 方式一：z2m_local（推荐用于多设备管理）

**特点：**
- 📋 需要维护 `index.json` 索引文件
- ✅ 能精确控制固件版本信息和元数据
- ✅ 支持多个设备、多个版本
- ✅ 可以添加发布说明和校验和

**适合场景：**
- 管理多个设备的固件
- 需要版本说明和元数据
- 生产环境或正式发布

### 方式二：advanced（推荐开发者快速迭代）

**特点：**
- ⚡ 无需维护 `index.json`
- ⚡ 直接从文件夹读取所有 `.ota` 文件
- ⚠️ 需要添加警告声明（防止误刷固件）
- ⚡ 最适合快速开发测试

**适合场景：**
- 开发测试阶段
- 频繁更新固件
- 快速迭代验证

---

## 方式一：z2m_local 配置（推荐新手）

### 步骤 1：修改 configuration.yaml

打开你的 Home Assistant 配置文件：

```bash
# 在项目根目录执行
code config/configuration.yaml
```

找到 `zha:` 部分，**完全替换**为以下配置：

```yaml
zha:
  database_path: /config/zigbee.db
  zigpy_config:
    ota:
      extra_providers:
        - type: z2m_local
          index_file: /config/zigpy_ota/index.json
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

**⚠️ 重要说明：**
- 路径必须使用 Docker 容器内的路径（`/config/...`），不是 Windows 路径
- `extra_providers` 表示在默认 OTA 源基础上添加本地源
- 如果只想使用本地固件，将 `extra_providers` 改为 `providers`

### 步骤 2：准备目录结构

创建以下目录结构：

```
config/
├── zigpy_ota/
│   ├── index.json          # 固件索引文件（下一步创建）
│   └── DEVICE-03_v1.0.5.ota  # 你的固件文件
```

在项目根目录执行：

```bash
# 确保目录存在
mkdir -p config/zigpy_ota

# 将你的 .ota 固件文件复制到这个目录
# 例如：cp /path/to/your/firmware.ota config/zigpy_ota/
```

### 步骤 3：获取设备信息（关键！）

在编写 `index.json` 之前，你需要获取设备的关键信息：

#### 方法 1：从 Home Assistant UI 查看

1. 打开 Home Assistant
2. 进入 **设置 → 设备与服务 → Zigbee Home Automation**
3. 点击你的设备（如 VENDOR_A MODEL-03）
4. 查看设备信息：

```
设备信息：
- IEEE 地址: xx:xx:xx:xx:xx:xx:xx:xx
- 制造商: VENDOR_A
- 型号: MODEL-03
```

5. 点击 **管理 Zigbee 设备** → 查看 **签名(Signature)**

```
签名：
- manufacturer_id: 4742  ← 这是 manufacturerCode (十进制)
- device_type: 0x0107
```

#### 方法 2：使用 Python 脚本解析 OTA 文件

如果你已经有 `.ota` 文件，可以直接解析出所需信息。

创建文件 `parse_ota.py`：

```python
#!/usr/bin/env python3
"""解析 OTA 文件头信息"""
import struct
import sys

def parse_ota_header(ota_file):
    try:
        with open(ota_file, 'rb') as f:
            # 读取 OTA 文件头（前 69 字节）
            header = f.read(69)

            if len(header) < 69:
                print(f"错误：文件太小，不是有效的 OTA 文件")
                return None

            # 解析关键字段（小端序）
            magic = struct.unpack('<I', header[0:4])[0]
            file_version = struct.unpack('<I', header[18:22])[0]
            image_type = struct.unpack('<H', header[22:24])[0]
            manufacturer_code = struct.unpack('<H', header[24:26])[0]

            if magic != 0x0BEEF11E:
                print(f"警告：Magic Number 不正确，可能不是有效的 OTA 文件")

            print("=" * 60)
            print(f"OTA 文件信息：{ota_file}")
            print("=" * 60)
            print(f"Magic Number:        0x{magic:08X}")
            print(f"Manufacturer Code:   {manufacturer_code} (0x{manufacturer_code:04X})")
            print(f"Image Type:          {image_type} (0x{image_type:04X})")
            print(f"File Version:        {file_version}")
            print("=" * 60)

            # 读取文件大小
            import os
            file_size = os.path.getsize(ota_file)
            print(f"File Size:           {file_size} bytes")
            print("=" * 60)

            return {
                'manufacturerCode': manufacturer_code,
                'imageType': image_type,
                'fileVersion': file_version,
                'fileSize': file_size
            }
    except FileNotFoundError:
        print(f"错误：文件不存在：{ota_file}")
        return None
    except Exception as e:
        print(f"错误：{e}")
        return None

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("用法: python parse_ota.py <固件文件.ota>")
        print("示例: python parse_ota.py DEVICE-03_v1.0.5.ota")
        sys.exit(1)

    parse_ota_header(sys.argv[1])
```

运行脚本：

```bash
python parse_ota.py config/zigpy_ota/DEVICE-03_v1.0.5.ota
```

**输出示例：**

```
============================================================
OTA 文件信息：config/zigpy_ota/DEVICE-03_v1.0.5.ota
============================================================
Magic Number:        0x0BEEF11E
Manufacturer Code:   4742 (0x1286)
Image Type:          8209 (0x2011)
File Version:        5
============================================================
File Size:           204288 bytes
============================================================
```

### 步骤 4：编写 index.json

使用上一步获取的信息，创建 `config/zigpy_ota/index.json`：

#### 示例 1：单个 VENDOR_A MODEL-03 设备

```json
[
  {
    "fileName": "DEVICE-03_v1.0.5.ota",
    "fileVersion": 5,
    "fileSize": 204288,
    "manufacturerCode": 4742,
    "imageType": 8209,
    "url": "/config/zigpy_ota/DEVICE-03_v1.0.5.ota",
    "modelId": "MODEL-03",
    "manufacturerName": ["VENDOR_A"],
    "releaseNotes": "修复运动检测灵敏度问题，优化电池续航"
  }
]
```

#### 示例 2：多个 VENDOR_A 设备

```json
[
  {
    "fileName": "DEVICE-03_v1.0.5.ota",
    "fileVersion": 5,
    "fileSize": 204288,
    "manufacturerCode": 4742,
    "imageType": 8209,
    "url": "/config/zigpy_ota/DEVICE-03_v1.0.5.ota",
    "modelId": "MODEL-03",
    "manufacturerName": ["VENDOR_A"],
    "releaseNotes": "修复运动检测灵敏度，优化电池续航"
  },
  {
    "fileName": "DEVICE-02_v2.0.3.ota",
    "fileVersion": 203,
    "fileSize": 189440,
    "manufacturerCode": 4742,
    "imageType": 8208,
    "url": "/config/zigpy_ota/DEVICE-02_v2.0.3.ota",
    "modelId": "MODEL-02",
    "manufacturerName": ["VENDOR_A"],
    "releaseNotes": "新增温湿度校准功能"
  },
  {
    "fileName": "DEVICE-04_v3.1.0.ota",
    "fileVersion": 310,
    "fileSize": 195600,
    "manufacturerCode": 4742,
    "imageType": 8210,
    "url": "/config/zigpy_ota/DEVICE-04_v3.1.0.ota",
    "modelId": "MODEL-04",
    "manufacturerName": ["VENDOR_A"],
    "releaseNotes": "优化门窗传感器响应速度"
  }
]
```

**字段说明：**

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `fileName` | 字符串 | 可选 | 固件文件名（便于识别） |
| `fileVersion` | 整数 | **必需** | 固件版本号，必须大于设备当前版本 |
| `fileSize` | 整数 | 推荐 | 文件大小（字节），用于验证 |
| `manufacturerCode` | 整数 | **必需** | 制造商代码（十进制），VENDOR_A = 4742 |
| `imageType` | 整数 | **必需** | 镜像类型（十进制），每个设备型号不同 |
| `url` | 字符串 | **必需** | 固件文件路径（容器内绝对路径） |
| `modelId` | 字符串 | 推荐 | 设备型号 ID，用于精确匹配 |
| `manufacturerName` | 数组 | 可选 | 制造商名称数组 |
| `releaseNotes` | 字符串 | 可选 | 发布说明，便于记录 |

**⚠️ 重要提示：**
- `fileVersion` 必须是整数，不能是字符串
- `manufacturerCode` 和 `imageType` 使用十进制，不是十六进制
- `url` 必须使用容器内的绝对路径（`/config/...`）
- JSON 格式必须正确，建议使用在线 JSON 验证工具检查

### 步骤 5：放置固件文件

确保你的 `.ota` 固件文件已经放在正确位置：

```bash
# 检查文件是否存在
ls -lh config/zigpy_ota/

# 输出示例：
# -rw-r--r-- 1 user user 199K Feb  4 10:30 DEVICE-03_v1.0.5.ota
# -rw-r--r-- 1 user user 1.2K Feb  4 10:35 index.json
```

### 步骤 6：重启 Home Assistant

配置完成后，重启 Home Assistant 以加载新配置：

```bash
# 方法 1：使用 docker-compose
docker-compose restart homeassistant

# 方法 2：使用项目提供的脚本（如果有）
.\ha.ps1 restart

# 方法 3：在 Home Assistant UI 中重启
# 设置 → 系统 → 重启
```

### 步骤 7：在 Home Assistant 中触发更新

1. 重启完成后，打开 Home Assistant
2. 进入 **设置 → 设备与服务 → Zigbee Home Automation**
3. 点击你的设备（如 VENDOR_A MODEL-03）
4. 如果有固件更新，会显示 **"固件更新可用"** 通知
5. 点击 **更新固件** 按钮
6. 等待更新完成（通常 5-10 分钟）

**更新过程中：**
- 📶 设备会保持连接
- ⏳ 进度条显示更新进度
- ✅ 更新完成后设备会自动重启

---

## 方式二：advanced 配置（开发者快速迭代）

### 步骤 1：修改 configuration.yaml

打开配置文件并**完全替换** `zha:` 部分：

```yaml
zha:
  database_path: /config/zigbee.db
  zigpy_config:
    ota:
      extra_providers:
        - type: advanced
          warning: I understand I can *destroy* my devices by enabling OTA updates from files. Some OTA updates can be mistakenly applied to the wrong device, breaking it. I am consciously using this at my own risk.
          path: /config/zigpy_ota
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

**⚠️ 警告声明说明：**
- `advanced` 模式需要明确的警告声明
- 这是 zigpy 的安全机制，防止误刷固件损坏设备
- 必须完整复制这段警告文本，不能修改

### 步骤 2：准备固件文件

创建目录并放置固件文件：

```bash
# 创建目录
mkdir -p config/zigpy_ota

# 将所有 .ota 文件放到这个目录
# 例如：
cp DEVICE-03_v1.0.5.ota config/zigpy_ota/
cp DEVICE-02_v2.0.3.ota config/zigpy_ota/
```

**文件结构示例：**

```
config/zigpy_ota/
├── DEVICE-03_v1.0.5.ota
├── DEVICE-02_v2.0.3.ota
└── DEVICE-04_v3.1.0.ota
```

**就这么简单！** zigpy 会自动：
1. 扫描目录中的所有 `.ota` 文件
2. 解析每个文件的头信息
3. 根据设备的制造商代码和镜像类型自动匹配
4. 推送给对应的设备

### 步骤 3：重启 Home Assistant

```bash
docker-compose restart homeassistant
```

### 步骤 4：触发更新

与方式一相同，进入设备页面即可看到更新通知。

### 快速迭代开发流程

**advanced 模式特别适合开发者快速测试：**

1. 修改固件代码
2. 编译生成新的 `.ota` 文件（**记得更新版本号！**）
3. 直接替换 `config/zigpy_ota/` 中的文件
4. 重启 Home Assistant
5. 在设备页面触发更新

**不需要修改任何配置文件！** ⚡

---

## 进阶技巧

### 1. 混合使用官方源和本地固件

你可以同时使用官方固件源和本地固件：

```yaml
zha:
  zigpy_config:
    ota:
      # 禁用默认的 VENDOR_A 官方源（因为要用自己的固件）
      disable_default_providers: [vendor_a]
      extra_providers:
        # 保留其他厂商的官方更新
        - type: ikea
        - type: inovelli
        # 使用本地 VENDOR_A 固件
        - type: z2m_local
          index_file: /config/zigpy_ota/index.json
```

### 2. 固件版本号管理

**版本号必须递增：**
- 设备只会接受比当前版本**更高**的固件
- 如果 `fileVersion` 小于或等于当前版本，设备会拒绝更新

**版本号示例：**
```
当前版本：4
可以更新到：5, 6, 10, 100 ✅
无法更新到：4, 3, 2, 1 ❌
```

**如何强制降级（高级）：**
1. 通常需要使用厂商工具直接连接设备
2. 或者修改 ZHA 数据库中的设备版本信息（不推荐）

### 3. 同一设备的多个固件版本

你可以在 `index.json` 中为同一设备添加多个版本：

```json
[
  {
    "fileName": "DEVICE-03_v1.0.5_stable.ota",
    "fileVersion": 5,
    "manufacturerCode": 4742,
    "imageType": 8209,
    "url": "/config/zigpy_ota/DEVICE-03_v1.0.5_stable.ota",
    "modelId": "MODEL-03",
    "releaseNotes": "稳定版"
  },
  {
    "fileName": "DEVICE-03_v1.0.6_beta.ota",
    "fileVersion": 6,
    "manufacturerCode": 4742,
    "imageType": 8209,
    "url": "/config/zigpy_ota/DEVICE-03_v1.0.6_beta.ota",
    "modelId": "MODEL-03",
    "releaseNotes": "测试版 - 新增功能 XYZ"
  }
]
```

**ZHA 会自动选择最高版本（v1.0.6）推送给设备。**

### 4. 启用 ZHA 调试日志

如果更新遇到问题，可以启用调试日志：

编辑 `configuration.yaml`，添加：

```yaml
logger:
  default: info
  logs:
    zigpy: debug
    homeassistant.components.zha: debug
```

重启后，在 **设置 → 系统 → 日志** 中查看详细信息。

---

## 故障排除

### 问题 1：修改配置后不显示更新

**可能原因：**
- ❌ `index.json` 格式错误
- ❌ `fileVersion` 不大于当前版本
- ❌ `manufacturerCode` 或 `imageType` 不匹配
- ❌ 文件路径错误

**解决方法：**

1. **验证 JSON 格式**

```bash
# 使用 Python 验证 JSON 格式
python -m json.tool config/zigpy_ota/index.json

# 或使用在线工具：https://jsonlint.com/
```

2. **检查版本号**

```bash
# 使用 parse_ota.py 脚本查看固件版本
python parse_ota.py config/zigpy_ota/your_firmware.ota
```

3. **查看 Home Assistant 日志**

```bash
# 实时查看日志
docker logs -f homeassistant

# 搜索 OTA 相关错误
docker logs homeassistant 2>&1 | grep -i "ota"
```

4. **重启 Home Assistant**

```bash
docker-compose restart homeassistant
```

### 问题 2：更新失败或卡住

**可能原因：**
- 📶 设备信号不好
- 🔋 设备电量不足
- 💾 固件文件损坏

**解决方法：**

1. **确保设备在线且信号良好**
   - 查看设备的 LQI（Link Quality Indicator）
   - 如果信号弱，尝试将设备移近网关

2. **更换电池（电池供电设备）**
   - 低电量会导致更新失败
   - 建议使用全新电池

3. **重新尝试更新**
   - 在设备页面点击"重试"
   - 或重新触发更新

4. **检查固件文件**
   - 使用 `parse_ota.py` 验证文件头
   - 确认文件完整性

### 问题 3：Docker 路径问题

**错误示例：**

```
错误：找不到文件 /config/zigpy_ota/index.json
```

**解决方法：**

1. **确认卷映射正确**

查看 `docker-compose.yml`：

```yaml
services:
  homeassistant:
    volumes:
      - ./config:/config  # 确保有这一行
```

2. **使用容器内路径**

❌ 错误：`C:\Users\...\config\zigpy_ota\index.json`
✅ 正确：`/config/zigpy_ota/index.json`

3. **检查文件是否存在**

```bash
# 在宿主机检查
ls -lh config/zigpy_ota/

# 在容器内检查
docker exec homeassistant ls -lh /config/zigpy_ota/
```

### 问题 4：设备拒绝更新

**可能原因：**
- 🔒 `manufacturerCode` 不匹配
- 🔒 `imageType` 不匹配
- 📉 版本号不大于当前版本

**解决方法：**

1. **验证设备信息**

进入 Home Assistant，查看设备签名：

```
签名：
- manufacturer_id: 4742  ← 必须与 index.json 中的 manufacturerCode 一致
```

2. **使用 parse_ota.py 验证固件信息**

```bash
python parse_ota.py your_firmware.ota
```

确认输出的 `Manufacturer Code` 和 `Image Type` 与设备签名一致。

---

## 常见示例设备参考

| 设备型号 | manufacturerCode | 可能的 imageType | 说明 |
|----------|------------------|------------------|------|
| MODEL-03 (运动传感器) | 4742 (0x1286) | 8209 (0x2011) | 需从实际固件获取 |
| MODEL-02 (温湿度传感器) | 4742 (0x1286) | 8208 (0x2010) | 需从实际固件获取 |
| MODEL-04 (门窗传感器) | 4742 (0x1286) | 8210 (0x2012) | 需从实际固件获取 |

**⚠️ 注意：** `imageType` 每个设备型号都不同，上表仅供参考，**请务必从实际固件文件中提取或从设备签名中查看**。

---

## 快速参考卡片

| 操作 | 命令/路径 | 说明 |
|------|----------|------|
| 配置文件 | `config/configuration.yaml` | ZHA 主配置 |
| 固件目录 | `config/zigpy_ota/` | 放置 .ota 文件 |
| 索引文件 | `config/zigpy_ota/index.json` | z2m_local 需要 |
| 重启 HA | `docker-compose restart homeassistant` | 应用新配置 |
| 查看日志 | `docker logs -f homeassistant` | 调试问题 |
| 解析固件 | `python parse_ota.py xxx.ota` | 获取固件信息 |
| 设备管理 | 设置 → 设备与服务 → ZHA | 触发更新 |

---

## 配置模板

### z2m_local 完整配置

```yaml
zha:
  database_path: /config/zigbee.db
  zigpy_config:
    ota:
      extra_providers:
        - type: z2m_local
          index_file: /config/zigpy_ota/index.json
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

### advanced 完整配置

```yaml
zha:
  database_path: /config/zigbee.db
  zigpy_config:
    ota:
      extra_providers:
        - type: advanced
          warning: I understand I can *destroy* my devices by enabling OTA updates from files. Some OTA updates can be mistakenly applied to the wrong device, breaking it. I am consciously using this at my own risk.
          path: /config/zigpy_ota
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

### index.json 模板

```json
[
  {
    "fileName": "device_v1.0.0.ota",
    "fileVersion": 100,
    "fileSize": 200000,
    "manufacturerCode": 4742,
    "imageType": 8209,
    "url": "/config/zigpy_ota/device_v1.0.0.ota",
    "modelId": "YOUR-MODEL",
    "manufacturerName": ["VENDOR_A"],
    "releaseNotes": "你的发布说明"
  }
]
```

---

## 相关资源

### 官方文档
- [Zigbee2MQTT OTA 文档](https://www.zigbee2mqtt.io/guide/usage/ota_updates.html)
- [zigpy OTA 配置 Wiki](https://github.com/zigpy/zigpy/wiki/OTA-Configuration)
- [ZHA Integration 文档](https://www.home-assistant.io/integrations/zha/)

### 固件仓库
- [Koenkk/zigbee-OTA](https://github.com/Koenkk/zigbee-OTA) - Zigbee2MQTT 官方固件仓库
- [zigpy/zha-device-handlers](https://github.com/zigpy/zha-device-handlers) - ZHA 设备处理器

### 工具
- [JSON 格式验证](https://jsonlint.com/) - 验证 index.json 格式
- [parse_ota.py](#步骤-3获取设备信息关键) - 本教程提供的 OTA 解析脚本

---

## 最佳实践

### ✅ 推荐做法

1. **先在测试设备上验证** - 避免影响生产环境设备
2. **保留固件备份** - 出问题时可以恢复
3. **记录固件版本号** - 便于追溯和管理
4. **更新前确保设备电量充足** - 电池设备建议更换新电池
5. **使用 Git 管理配置文件** - 方便回滚和追踪变更
6. **开发阶段优先使用 advanced 模式** - 快速迭代测试
7. **生产环境使用 z2m_local 模式** - 精确控制版本

### ❌ 避免做法

1. **不要在生产设备上测试未验证的固件** - 可能导致设备变砖
2. **不要在低电量时更新电池设备** - 容易更新失败
3. **不要频繁刷同一版本号** - 设备会拒绝相同版本
4. **不要使用错误的 manufacturerCode** - 可能刷入不兼容固件
5. **不要忽略警告声明（advanced 模式）** - 理解风险再使用

---

## 总结

恭喜！🎉 你已经学会了如何在 Home Assistant ZHA 中配置本地 OTA 固件更新。

**两种方式对比：**

| 特性 | z2m_local | advanced |
|------|-----------|----------|
| 配置复杂度 | 中等（需维护 index.json） | 简单（无需 index.json） |
| 适合场景 | 生产环境、多版本管理 | 开发测试、快速迭代 |
| 元数据支持 | ✅ 支持发布说明、校验和 | ❌ 仅读取固件头 |
| 多设备管理 | ✅ 优秀 | ✅ 良好 |
| 开发效率 | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

**如果遇到问题：**
1. 查看本教程的 [故障排除](#故障排除) 部分
2. 启用调试日志查看详细信息
3. 使用 `parse_ota.py` 验证固件文件
4. 检查 `index.json` 格式（如使用 z2m_local）

**下一步：**
- 🔧 开始开发和测试你的 Zigbee 固件
- 📚 深入学习 [zigpy 官方文档](https://github.com/zigpy/zigpy/wiki)
- 🤝 加入 Home Assistant 社区交流经验

---

**提示**：建议把本教程和配置文件一起保存在项目的 `docs/` 目录中，方便随时查阅。
