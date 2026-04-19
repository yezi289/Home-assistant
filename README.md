# Home Assistant Docker 部署

基于 Docker 的 Home Assistant 部署方案，支持 Zigbee 设备接入（ZHA 集成），适用于 Windows + WSL2 和 Ubuntu 环境。

**按照本教程完成部署后，你将得到：** 一套可快速拉起的本地 Home Assistant 开发环境，专为 ZHA 脚本的开发与调试设计——无需真实主机安装，容器随起随停，配合 Zigbee USB Dongle 即可在本地复现完整的 ZHA 运行环境，方便编写、测试和验证自动化脚本。

## 项目结构

```text
home-assistant/
├── docker-compose.yml       # Docker 服务配置
├── install_docker.sh        # Docker 一键安装脚本（Ubuntu/WSL2）
├── ha.sh                    # Linux/WSL 管理脚本
├── ha.ps1                   # Windows PowerShell 管理脚本
├── config/                  # Home Assistant 配置目录（挂载到容器）
│   ├── configuration.yaml   # 主配置文件
│   ├── automations.yaml     # 自动化配置
│   ├── scripts.yaml         # 脚本配置
│   ├── scenes.yaml          # 场景配置
│   └── zha_quirks/          # ZHA 自定义设备适配
└── docs/                    # 文档
    ├── USBIPD使用指南.md
    └── ZHA本地OTA固件更新教程.md
```

## 前置要求

- Docker & Docker Compose
- Windows 环境需要 WSL2（用于 USB 设备直通）
- [USBIPD-WIN](https://github.com/dorssel/usbipd-win)（Windows 下共享 USB 设备到 WSL2）

## 快速开始

### 1. 克隆项目

```bash
git clone <repo-url>
cd home-assistant
```

**克隆结果：** 本地会生成 `home-assistant/` 目录，包含所有配置文件和管理脚本，可直接进入下一步。

### 2. 安装 Docker

**Ubuntu / WSL2：**

如果尚未安装 Docker，可使用项目内置脚本一键完成安装：

```bash
chmod +x install_docker.sh
./install_docker.sh
```

**安装结果：** 脚本执行完成后，你将得到一个可直接使用的 Docker 环境：

- `docker` 和 `docker compose` 命令均可用
- Docker 服务已设为开机自启，无需每次手动启动
- 当前用户已加入 `docker` 用户组，可不加 `sudo` 直接运行所有 Docker 命令

验证安装是否成功：

```bash
docker --version        # 应输出类似 Docker version 27.x.x
docker compose version  # 应输出类似 Docker Compose version v2.x.x
docker run hello-world  # 拉取并运行测试镜像，输出 "Hello from Docker!" 即代表一切正常
```

脚本执行过程中会自动完成：移除旧版本残留、添加 Docker 官方 APT 仓库、安装 `docker-ce`、`docker-compose-plugin` 等组件。若检测到 Docker 已安装，则跳过安装直接确认服务状态后退出，不会重复操作。

> **注意：** 安装完成后需重新登录（或重启终端）使用户组变更生效，之后才能不加 `sudo` 直接运行 `docker` 命令。

**Windows：**

安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/)，安装时勾选 **Use WSL 2 based engine**。安装完成后启动 Docker Desktop，确认任务栏图标显示为运行中状态即可。

**安装结果：** Docker Desktop 运行后，WSL2 内的 `docker` 和 `docker compose` 命令即可直接使用，无需额外配置。

### 3. 连接 USB Zigbee 设备（Windows 用户）

将 Zigbee USB Dongle 插入电脑，通过 USBIPD 共享给 WSL2：

```powershell
# 查看设备列表（PowerShell）
usbipd list

# 绑定设备（管理员权限，仅首次需要）
usbipd bind --busid <设备ID>

# 附加设备到 WSL2（每次重启后需要，一定要保证 WSL 已经启动）
usbipd attach --wsl --busid <设备ID>
```

**连接结果：** 完成后在 WSL2 中运行以下命令，应能看到 `/dev/ttyUSB0`（或类似设备节点）出现，表示 Zigbee Dongle 已成功映射到 Linux 环境：

```bash
ls /dev/ttyUSB*
```

详细说明见 [USBIPD使用指南](docs/USBIPD使用指南.md)。

### 4. 启动服务

**Linux / WSL2：**

```bash
./ha.sh start
```

**Windows PowerShell：**

```powershell
.\ha.ps1 start
```

**启动结果：** Home Assistant 容器在后台运行，首次启动会拉取镜像，耗时约 1~3 分钟。验证服务是否就绪：

```bash
./ha.sh status   # 容器状态应显示 Up
./ha.sh logs     # 观察日志，出现 "Home Assistant initialized" 表示启动完成
```

### 5. 访问 Web 界面

打开浏览器访问：<http://localhost:8123>

**访问结果：** 看到 Home Assistant 初始化向导页面，按提示创建管理员账号即可完成首次配置。若页面无法打开，说明容器尚未完全启动，稍等片刻后刷新重试。

## 管理脚本

项目提供了 `ha.sh`（Linux/WSL）和 `ha.ps1`（Windows）两个脚本，支持以下命令：

| 命令 | 说明 |
| --- | --- |
| `start` | 启动 Home Assistant |
| `stop` | 停止 Home Assistant |
| `restart` | 重启 Home Assistant |
| `logs` | 查看实时日志 |
| `shell` | 进入容器命令行 |
| `open` | 显示 Web 访问地址 |
| `status` | 查看容器运行状态 |

示例：

```bash
./ha.sh logs     # 查看日志
./ha.sh restart  # 重启
./ha.sh shell    # 进入容器
```

## Zigbee 配置（ZHA）

本项目使用 ZHA（Zigbee Home Automation）集成管理 Zigbee 设备。

`config/configuration.yaml` 中的关键配置：

```yaml
zha:
  database_path: /config/zigbee.db
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

- **自定义 Quirks**：将设备适配文件放入 `config/zha_quirks/` 目录
- **OTA 固件更新**：参考 [ZHA本地OTA固件更新教程](docs/ZHA本地OTA固件更新教程.md)

### ZHA 加载失败排查

如果 Home Assistant 页面提示“集成未能加载，请尝试重启 Home Assistant。zha 加载失败”，优先检查 `config/zha_quirks/` 目录是否存在。

本项目在 `configuration.yaml` 中默认启用了以下配置：

```yaml
zha:
  enable_quirks: true
  custom_quirks_path: /config/zha_quirks
```

如果挂载到容器内的 `/config/zha_quirks` 不存在，Home Assistant 会将 `zha` 判定为无效配置，并在日志中报出类似错误：

```text
Invalid config for 'zha': not a directory for dictionary value 'zha->custom_quirks_path'
```

处理方法：

- 确保仓库中的 `config/zha_quirks/` 目录存在
- 如不需要自定义 quirks，可删除或注释 `custom_quirks_path` 配置
- 修改后执行 `.\ha.ps1 restart` 或 `./ha.sh restart` 重启 Home Assistant

## 常用设备

- Zigbee USB Dongle（如 通用 Zigbee USB Dongle、ConBee II）
  - 设备路径：`/dev/ttyUSB0`

## 镜像版本说明

镜像地址：`ghcr.io/home-assistant/home-assistant`

| Tag | 说明 | 适用场景 |
| --- | --- | --- |
| `stable` | 最新稳定版（当前使用） | 生产环境，推荐 |
| `latest` | 同 `stable` | 同上 |
| `beta` | 公测版，功能较新但可能有 bug | 想尝鲜但求稳 |
| `dev` | 开发/每日构建版，最新特性 | 开发测试，不稳定 |
| `2025.2.0` | 指定具体版本号 | 锁定版本，防止意外升级 |

修改 `docker-compose.yml` 中的 `image` 字段即可切换版本：

```yaml
image: ghcr.io/home-assistant/home-assistant:beta
```

## 重置系统

如需将项目恢复到初始状态（删除所有运行时产生的未追踪文件，如日志、数据库、缓存等），可使用以下命令。

> **警告：此操作不可逆，会永久删除所有未被 git 追踪的文件和目录（包括 `.gitignore` 中忽略的文件）。执行前请确认已备份重要数据。**

### 第一步：先停止容器

```powershell
.\ha.ps1 stop
```

### 第二步：预览将被删除的文件（不实际删除）

```powershell
git clean -xfdn
```

### 第三步：确认无误后执行清理

```powershell
git clean -xfd
```

参数说明：

| 参数 | 含义 |
| --- | --- |
| `-x` | 同时删除 `.gitignore` 中忽略的文件 |
| `-f` | 强制执行（必需） |
| `-d` | 同时删除未追踪的目录 |
| `-n` | 空运行（dry run），仅预览不删除 |

清理完成后重新启动即可得到全新环境：

```powershell
.\ha.ps1 start
```

## 文档

- [USBIPD使用指南](docs/USBIPD使用指南.md) - Windows 下 USB 设备直通 WSL2
- [ZHA本地OTA固件更新教程](docs/ZHA本地OTA固件更新教程.md) - Zigbee 设备固件升级
