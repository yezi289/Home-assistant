# SONOFF SWV ZHA Quirk 说明

本文整理 `D:\project\6-HA\单通道\swv1c(手动默认模式).py` 的功能和代码逻辑，并记录这些功能如何合入当前 Home Assistant 自定义 quirk：

`D:\project\6-HA\home-assistant\config\zha_quirks\sonoff_dual_water_valve.py`

## 1. 文件定位

`swv1c(手动默认模式).py` 是一个 ZHA v2 `QuirkBuilder` 风格的 SONOFF SWV 水阀适配脚本。

它的目标不是改固件，而是在 Home Assistant / ZHA 侧把 SONOFF 私有 Zigbee cluster `0xFC11` 里的属性转换成可用实体。

核心能力：

- 注册 SONOFF 私有 cluster `0xFC11`
- 暴露阀门异常状态，比如漏水、缺水
- 暴露用水时长、用水量
- 解析和写入 `0x501D` 单次浇水设置
- 把一个私有 array 属性拆成多个 Home Assistant 配置实体

## 2. SONOFF 私有 Cluster

脚本定义了 `CustomSonoffCluster`：

```python
class CustomSonoffCluster(CustomCluster):
    cluster_id = 0xFC11
    ep_attribute = "sonoff_cluster"
```

这个 cluster 对应固件里的：

```c
#define ZDF_ZCL_SONOFF_PRIVATE_CLUSTER_ID 0xFC11
```

脚本中使用到的关键属性：

| 属性 | ID | 类型 | 作用 |
|---|---:|---|---|
| `water_valve_state` | `0x500C` | `ValveState` | 阀门异常状态，按 bit 表示 |
| `water_usage_volume` | `0x501B` | `uint32` | 用水量 |
| `water_usage_duration` | `0x501C` | `uint32` | 用水时长 |
| `single_irrigation_set` | `0x501D` | `Array` | 单次浇水设置，聚合 payload |

## 3. 阀门异常状态

`ValveState` 是一个 bit map：

```python
class ValveState(t.enum8):
    Normal = 0
    Water_Shortage = 1 << 0
    Water_Leakage = 1 << 1
    Anti_Frost_Alarm = 1 << 2
    Water_Shortage_Channel_2 = 1 << 4
```

含义：

| bit | 值 | 含义 |
|---|---:|---|
| bit0 | `0x01` | 缺水 |
| bit1 | `0x02` | 漏水 |
| bit2 | `0x04` | 防霜冻报警 |
| bit4 | `0x10` | 二通道缺水 |

Home Assistant 实体映射：

- `water_leak_status`：从 `0x500C` 取 bit1，设备类是 `MOISTURE`
- `water_depletion_status` / `water_shortage_status`：从 `0x500C` 取 bit0 或 bit4，设备类是 `PROBLEM`

## 4. 用水统计

脚本将私有属性映射为传感器：

| HA 实体 | 属性 | 单位 | 说明 |
|---|---:|---|---|
| `water_usage_duration` | `0x501C` | minutes | 用水时长 |
| `water_usage_volume` | `0x501B` | liters | 用水量 |

`water_usage_volume` 使用 `SensorStateClass.TOTAL_INCREASING`，表示累计递增量，适合 Home Assistant 统计图表。

## 5. 单次浇水设置 `0x501D`

这是该文件最重要的逻辑。

固件把“手动默认模式 / 单次浇水设置”打包成一个 array 属性 `0x501D`。脚本不直接把它作为 raw array 暴露，而是拆成多个配置项。

### 5.1 Payload 结构

脚本认为 `0x501D` 的有效 payload 长度是 12 字节：

| 偏移 | 长度 | 字段 | 编码 |
|---:|---:|---|---|
| 0 | 1 | `irrigation_mode` | enum8 |
| 1 | 2 | `total_duration_min` | uint16 big-endian |
| 3 | 2 | `duration_min` | uint16 big-endian |
| 5 | 2 | `interval_duration_min` | uint16 big-endian |
| 7 | 1 | `amount_unit` | enum8 |
| 8 | 2 | `amount` | uint16 big-endian |
| 10 | 2 | `fail_safe_duration_min` | uint16 big-endian |

### 5.2 模式枚举

```python
class SingleIrrigationMode(t.enum8):
    Duration = 0x00
    Volume = 0x01
    Duration_With_Interval = 0x02
```

含义：

| 值 | 模式 |
|---:|---|
| `0x00` | 按时长浇水 |
| `0x01` | 按水量浇水 |
| `0x02` | 按时长并带间隔浇水 |

### 5.3 水量单位枚举

```python
class IrrigationAmountUnit(t.enum8):
    Gallon = 0x00
    Liter = 0x01
```

含义：

| 值 | 单位 |
|---:|---|
| `0x00` | gallon |
| `0x01` | liter |

### 5.4 默认值

脚本用 `SingleIrrigationState` 保存当前配置：

```python
@dataclass
class SingleIrrigationState:
    irrigation_mode: int = SingleIrrigationMode.Duration
    total_duration_min: int = 30
    duration_min: int = 0
    interval_duration_min: int = 0
    amount_unit: int = IrrigationAmountUnit.Liter
    amount: int = 0
    fail_safe_duration_min: int = 60
```

也就是说默认配置是：

- 模式：按时长
- 总时长：30 分钟
- 单段浇水时长：0
- 间隔时长：0
- 单位：升
- 水量：0
- 失效保护时长：60 分钟

## 6. 解码和编码逻辑

### 6.1 解码

`decode_single_irrigation_payload()` 做三件事：

1. 从 ZCL array / bytes / list / LVList 中取出真实 payload
2. 检查 payload 长度至少 12 字节
3. 按固定偏移解析成 `SingleIrrigationState`

关键点：多字节字段使用 big-endian：

```python
def _u16_be(data: bytes) -> int:
    return int.from_bytes(data[:2], "big")
```

### 6.2 编码

`encode_single_irrigation_payload()` 把 `SingleIrrigationState` 重新组装为 12 字节 payload。

多字节字段同样使用 big-endian：

```python
def _put_u16_be(value: int) -> list[int]:
    return list(int(value).to_bytes(2, "big"))
```

### 6.3 包装成 ZCL Array

写回设备前，脚本用 `single_irrigation_array_from_payload()` 将 payload 包成 ZCL array：

```python
foundation.Array(
    type=foundation.DataTypeId.uint8,
    value=t.LVList[t.uint8_t, t.uint16_t](payload),
)
```

这对应 Zigbee array 的格式：

- item type：`uint8`
- item list：12 字节 payload

## 7. LocalDataCluster 的作用

脚本定义了一个本地 cluster：

```python
class SonoffSingleIrrigationConfigCluster(LocalDataCluster):
    cluster_id = 0xFBFE
    ep_attribute = "sonoff_single_irrigation_config"
```

它不是真实设备上的 cluster，而是 ZHA 本地虚拟 cluster，用来把一个聚合属性 `0x501D` 拆成多个 Home Assistant 配置实体。

本地属性：

| 本地属性 | ID | 类型 | 对应 payload 字段 |
|---|---:|---|---|
| `irrigation_mode` | `0x0010` | enum | `irrigation_mode` |
| `total_duration_min` | `0x0011` | uint16 | `total_duration_min` |
| `duration_min` | `0x0012` | uint16 | `duration_min` |
| `interval_duration_min` | `0x0013` | uint16 | `interval_duration_min` |
| `amount_unit` | `0x0014` | enum | `amount_unit` |
| `amount` | `0x0015` | uint16 | `amount` |
| `fail_safe_duration_min` | `0x0016` | uint16 | `fail_safe_duration_min` |

## 8. 数据流

### 8.1 设备上报或读取 `0x501D`

数据流：

1. 设备上报或 ZHA 读取 `0x501D`
2. `CustomSonoffCluster` 捕获 attribute event
3. 调用 `decode_single_irrigation_payload()`
4. 更新 `_single_irrigation_state`
5. 同步到 `SonoffSingleIrrigationConfigCluster`
6. Home Assistant 里的配置实体更新

对应代码：

```python
self.on_event(AttributeReadEvent.event_type, self._handle_single_irrigation_change)
self.on_event(AttributeReportedEvent.event_type, self._handle_single_irrigation_change)
self.on_event(AttributeUpdatedEvent.event_type, self._handle_single_irrigation_change)
self.on_event(AttributeWrittenEvent.event_type, self._handle_single_irrigation_change)
```

### 8.2 Home Assistant 修改配置实体

数据流：

1. 用户在 Home Assistant 修改某个配置项
2. ZHA 写入本地 cluster 属性
3. `SonoffSingleIrrigationConfigCluster.write_attributes()` 合并修改到当前 `SingleIrrigationState`
4. 调用 `encode_single_irrigation_payload()`
5. 包装为 ZCL array
6. 写回真实设备 cluster `0xFC11` 的 attribute `0x501D`
7. 写入成功后更新本地状态

这就是该文件的核心设计：用本地 cluster 做“配置拆分层”。

## 9. QuirkBuilder 注册内容

单通道脚本主要注册了这些 Home Assistant 实体：

### 配置实体

| 实体 | 类型 | 说明 |
|---|---|---|
| `single_irrigation_mode` | enum config | 单次浇水模式 |
| `single_irrigation_total_duration` | number config | 总时长 |
| `single_irrigation_duration` | number config | 单段浇水时长 |
| `single_irrigation_interval_duration` | number config | 间隔时长 |
| `single_irrigation_amount_unit` | enum config | 水量单位 |
| `single_irrigation_amount` | number config | 水量 |
| `single_irrigation_fail_safe_duration` | number config | 失效保护时长 |

### 状态实体

| 实体 | 类型 | 来源 |
|---|---|---|
| `water_leak_status` | binary sensor | `0x500C` bit1 |
| `water_depletion_status` | binary sensor | `0x500C` bit0/bit4 |
| `water_usage_duration` | sensor | `0x501C` |
| `water_usage_volume` | sensor | `0x501B` |

## 10. 已合入当前双通道脚本的功能

当前文件：

`D:\project\6-HA\home-assistant\config\zha_quirks\sonoff_dual_water_valve.py`

已经合入：

- `0xFC11` 私有 cluster 定义
- `0x500C` 阀门异常状态解析
- 漏水 binary sensor
- 缺水 binary sensor
- `0x501B` 小时浇水量 sensor
- `0x501C` 小时浇水时长 sensor
- `0x501D` 单次浇水设置解析
- `0x501D` 单次浇水设置写回
- `SonoffSingleIrrigationConfigCluster` 本地配置 cluster
- 单次浇水模式、时长、间隔、水量、单位、失效保护等配置实体

当前双通道脚本还额外包含：

- 童锁 `0x0000`
- 实时浇水时长 `0x5006`
- 实时浇水量 `0x5007`
- 日浇水量 `0x500F`
- 日浇水时长 `0x501A`
- 更多 SWV model 匹配：
  - `SWV-ZF2E`
  - `SWV-ZF2U`
  - `SWV-ZN2E`
  - `SWV-ZN2U`
  - `SWV-ZF2`
  - `SWV-ZFE`
  - `SWV-ZFU`
  - `SWV-ZNE`
  - `SWV-ZNU`
  - `SWV`

## 11. 验证建议

重启 Home Assistant 后，重点看日志：

```powershell
Select-String -Path D:\project\6-HA\home-assistant\config\home-assistant.log -Pattern "sonoff_dual_water_valve","zha_quirks","quirk","Traceback","ImportError","AttributeError"
```

如果 quirk 正常加载，再检查设备页：

- 是否匹配为 SONOFF SWV quirk
- 是否出现两个阀门开关实体
- 是否出现漏水、缺水实体
- 是否出现用水量、用水时长实体
- 是否出现单次浇水配置实体

如果出现实体但写入失败，优先抓日志里 `0x501D` 写属性失败的返回状态。

## 12. 风险点

- `0x501D` payload 使用 big-endian 解析，这是按当前脚本逻辑整理的，需要用真实设备写入验证。
- `0x501D` 是 array 聚合属性，ZHA / zigpy 版本差异可能影响 `foundation.Array` 的包装方式。
- 双通道设备 endpoint `2` 的私有 cluster 是否真实暴露，需要以设备 discovery 结果为准；固件代码里 endpoint `2` 的私有属性表比 endpoint `1` 少。
- 体积单位当前按 liters 暴露，如果固件实际单位是 ml，需要在 quirk 中增加 multiplier 或调整单位。
