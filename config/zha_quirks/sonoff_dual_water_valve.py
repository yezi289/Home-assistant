"""SONOFF SWV dual-channel Zigbee water valve quirk."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

from zigpy.quirks import CustomCluster
from zigpy.quirks.v2 import (
    EntityType,
    NumberDeviceClass,
    QuirkBuilder,
    ReportingConfig,
    SensorDeviceClass,
    SensorStateClass,
)
from zigpy.quirks.v2 import EntityType
from zigpy.quirks.v2.homeassistant import UnitOfTime, UnitOfVolume
from zigpy.quirks.v2.homeassistant.binary_sensor import BinarySensorDeviceClass
import zigpy.types as t
from zigpy.zcl import (
    AttributeReadEvent,
    AttributeReportedEvent,
    AttributeUpdatedEvent,
    AttributeWrittenEvent,
    foundation,
)
from zigpy.zcl.foundation import BaseAttributeDefs, Status, ZCLAttributeDef

from zhaquirks import LocalDataCluster

# 宏定义
SINGLE_IRRIGATION_ARRAY_ITEM_TYPE = foundation.DataTypeId.uint8
SINGLE_IRRIGATION_PAYLOAD_LEN = 12
SINGLE_IRRIGATION_DURATION_MIN_MIN = 0
SINGLE_IRRIGATION_DURATION_MAX_MIN = 65535
SINGLE_IRRIGATION_STEP_MIN = 1
SINGLE_IRRIGATION_AMOUNT_MIN = 0
SINGLE_IRRIGATION_AMOUNT_MAX = 65535
SINGLE_IRRIGATION_FAIL_SAFE_MIN = 0
SINGLE_IRRIGATION_FAIL_SAFE_MAX = 65535
SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN = 10
SINGLE_IRRIGATION_DEFAULT_AMOUNT = 30
SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN = 10
SINGLE_IRRIGATION_ZB_AMOUNT_UNIT_LITER = 0x00
IRRIGATION_PLAN_PAYLOAD_LEN = 28
IRRIGATION_PLAN_MAX_COUNT = 6
IRRIGATION_PLAN_SET_COMMAND_ID = 0x06
IRRIGATION_PLAN_REMOVE_COMMAND_ID = 0x07
ZIGBEE_EPOCH_OFFSET = 946684800

def _u16_be(data: bytes) -> int:
    """Decode an unsigned big-endian 16-bit integer."""

    return int.from_bytes(data[:2], "big")


def _put_u16_be(value: int) -> list[int]:
    """Encode an unsigned big-endian 16-bit integer."""

    return list(int(value).to_bytes(2, "big"))

def _put_u32_be(value: int) -> list[int]:
    """Encode an unsigned big-endian 32-bit integer."""

    return list(int(value).to_bytes(4, "big"))

# 日程计划载荷
class IrrigationPlanPayload(t.FixedList):
    """Raw 28-byte irrigation plan payload."""

    _item_type = t.uint8_t
    _length = IRRIGATION_PLAN_PAYLOAD_LEN


class SingleIrrigationPayload(t.LVList, item_type=t.uint8_t, length_type=t.uint16_t):
    """SONOFF single irrigation uint8 array payload."""

    @staticmethod
    def _coerce_value(value: Any) -> bytes | list[int] | tuple[int, ...]:
        while hasattr(value, "value") and not isinstance(value, (bytes, bytearray, list, tuple)):
            inner_value = getattr(value, "value", None)
            if inner_value is None or inner_value is value:
                break
            value = inner_value

        if isinstance(value, (bytes, bytearray)):
            if len(value) >= 3 and value[0] == SINGLE_IRRIGATION_ARRAY_ITEM_TYPE:
                length = int.from_bytes(value[1:3], "little")
                return bytes(value[3 : 3 + length])
            return bytes(value)

        if isinstance(value, t.LVList):
            return list(value)

        if isinstance(value, (list, tuple)):
            return [int(item) for item in value]

        return value

    def __new__(cls, value=()):
        return super().__new__(cls, cls._coerce_value(value))

    def __init__(self, value=()):
        super().__init__(self._coerce_value(value))

# 单次灌溉模式枚举（时长、水量、时长间隔）
class SingleIrrigationMode(t.enum8):
    """Single irrigation mode."""

    Duration = 0x00
    Volume = 0x01
    Duration_With_Interval = 0x02

# 水量单位枚举（加仑、升）
class IrrigationAmountUnit(t.enum8):
    """Single irrigation amount unit."""

    Liter = 0x00
    Imperial_Gallon = 0x01
    US_Gallon = 0x02

# 数据类（对应单次灌溉数组）
@dataclass
class SingleIrrigationState:
    """Decoded SONOFF single irrigation setting."""

    irrigation_mode: int = SingleIrrigationMode.Duration
    total_duration_min: int = SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN
    duration_min: int = SINGLE_IRRIGATION_DURATION_MIN_MIN
    # interval_duration_min: int = 0
    amount_unit: int = SINGLE_IRRIGATION_ZB_AMOUNT_UNIT_LITER
    amount: int = SINGLE_IRRIGATION_DEFAULT_AMOUNT
    fail_safe_duration_min: int = SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN

class IrrigationLoopType(t.enum8):
    """Irrigation schedule loop type."""

    Even_Day = 0x00
    Odd_Day = 0x01
    Days = 0x02
    Week = 0x03
    Only = 0x04


class IrrigationPlanRepeat(t.enum8):
    """Simplified irrigation schedule repeat mode."""

    Odd_Day = 0x00
    Even_Day = 0x01
    Interval = 0x02
    Custom = 0x03

# 数据类（对应计划）
@dataclass
class IrrigationPlan:
    """Sonoff auto irrigation plan in the Zigbee command payload format."""

    index: int = 0                                                          # 索引
    enabled: int = 1
    enable_datetime: int = 0
    irrigation_mode: int = SingleIrrigationMode.Duration
    start_datetime: int = 0                                                 # 开始时间
    total_duration_min: int = SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN  # 总时长
    duration_min: int = 0
    interval_duration_min: int = 0                                          # 间隔时长
    amount_unit: int = SINGLE_IRRIGATION_ZB_AMOUNT_UNIT_LITER               # 水量单位
    amount: int = SINGLE_IRRIGATION_DEFAULT_AMOUNT                          # 水量
    fail_safe_duration_min: int = SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN
    create_datetime: int = 0
    repeat_mode: int = IrrigationPlanRepeat.Custom                          # 重复模式
    repeat_value: int = 0

# 计划索引校验
def _validate_irrigation_plan_index(index: int) -> None:
    """Validate that a schedule index is supported by the firmware."""

    if not 0 <= int(index) < IRRIGATION_PLAN_MAX_COUNT:
        raise ValueError("Irrigation plan index must be between 0 and 5")

# 日程循环模式校验
def _repeat_to_loop_info(repeat_mode: int, repeat_value: int) -> tuple[int, int]:
    """Convert simplified repeat settings to firmware loop fields."""

    repeat_mode = int(repeat_mode)
    repeat_value = int(repeat_value)
    if repeat_mode == IrrigationPlanRepeat.Odd_Day:     # 奇数日循环    
        return IrrigationLoopType.Odd_Day, 0
    if repeat_mode == IrrigationPlanRepeat.Even_Day:    # 偶数日循环
        return IrrigationLoopType.Even_Day, 0
    if repeat_mode == IrrigationPlanRepeat.Interval:    # 间隔循环，repeat_value为间隔天数，范围1..30
        if not 1 <= repeat_value <= 30:
            raise ValueError("Irrigation plan interval must be between 1 and 30 days")
        return IrrigationLoopType.Days, repeat_value
    if repeat_mode == IrrigationPlanRepeat.Custom:      # 自定义循环，repeat_value为自定义的周掩码，范围0..127（bit0=周一...bit6=周日）
        if not 0 <= repeat_value <= 0x7F:
            raise ValueError("Irrigation plan custom weekday mask must be 0..127")
        return IrrigationLoopType.Week, repeat_value
    raise ValueError("Unsupported irrigation plan repeat mode")

# 当天经过的总秒数（午夜开始计算）
def _seconds_from_midnight(hour: int, minute: int) -> int:
    """Return elapsed seconds from midnight for the current day."""

    return int(hour) * 3600 + int(minute) * 60

# 将年月日转换为Zigbee epoch时间戳（单位秒）
def _zigbee_date_timestamp(year: int, month: int, day: int) -> int:
    """Return the Zigbee epoch timestamp for a date at midnight UTC."""

    return int(
        datetime(int(year), int(month), int(day), tzinfo=timezone.utc).timestamp()
        - ZIGBEE_EPOCH_OFFSET
    )

# 返回当前UTC时间的Zigbee epoch时间戳（单位秒）
def _zigbee_now_timestamp() -> int:
    """Return the current UTC timestamp using the Zigbee epoch."""

    return int(datetime.now(tz=timezone.utc).timestamp() - ZIGBEE_EPOCH_OFFSET)


# Zigbee epoch时间戳转换为年月日元组
def _zigbee_timestamp_to_ymd(value: int) -> tuple[int, int, int]:
    """Convert a Zigbee epoch timestamp to year/month/day."""

    dt = datetime.fromtimestamp(int(value) + ZIGBEE_EPOCH_OFFSET, tz=timezone.utc)
    return dt.year, dt.month, dt.day

# 封装日程计划载荷，适配zigbee协议要求的字节格式
def encode_irrigation_plan_payload(plan: IrrigationPlan) -> bytes:
    """Encode a Zigbee auto irrigation plan command payload."""

    _validate_irrigation_plan_index(plan.index) # 计划索引校验
    loop_type, loop_option = _repeat_to_loop_info(plan.repeat_mode, plan.repeat_value)  # 日程循环模式校验

    payload: list[int] = [
        int(plan.enabled),
        int(loop_type),
        int(loop_option),
        *_put_u32_be(plan.enable_datetime),
        int(plan.irrigation_mode),
        *_put_u32_be(plan.start_datetime),
        *_put_u16_be(plan.total_duration_min),
        *_put_u16_be(plan.duration_min),
        *_put_u16_be(plan.interval_duration_min),
        int(plan.amount_unit),
        *_put_u16_be(plan.amount),
        *_put_u16_be(plan.fail_safe_duration_min),
        *_put_u32_be(plan.create_datetime),
    ]
    if len(payload) != IRRIGATION_PLAN_PAYLOAD_LEN:
        raise ValueError("Irrigation plan payload must be 28 bytes")
    return bytes(payload)

# 将灌溉负载数据封装为ZCL数组
def irrigation_plan_dedupe_key(plan: IrrigationPlan) -> tuple[int, ...]:
    """Return the stable fields that identify a schedule plan."""

    _validate_irrigation_plan_index(plan.index)
    loop_type, loop_option = _repeat_to_loop_info(plan.repeat_mode, plan.repeat_value)
    return (
        int(plan.enabled),
        int(loop_type),
        int(loop_option),
        int(plan.enable_datetime),
        int(plan.irrigation_mode),
        int(plan.start_datetime),
        int(plan.total_duration_min),
        int(plan.duration_min),
        int(plan.interval_duration_min),
        int(plan.amount_unit),
        int(plan.amount),
        int(plan.fail_safe_duration_min),
    )


def single_irrigation_array_from_payload(
    payload: bytes | list[int],
) -> SingleIrrigationPayload:
    """Wrap a single irrigation payload in a ZCL array value."""

    return SingleIrrigationPayload(payload)

def single_irrigation_array_from_payload_test(
    payload: bytes | list[int],
) -> foundation.Array:
    """Wrap a single irrigation payload in a ZCL array value."""

    return foundation.Array(
        type=SINGLE_IRRIGATION_ARRAY_ITEM_TYPE,
        value=t.LVList[t.uint8_t, t.uint16_t](payload),
    )

# 将ZCL数组数据解包为单次灌溉负载
def single_irrigation_payload_from_array(value: Any) -> bytes:
    """Extract the single irrigation payload bytes from a decoded ZCL array."""

    if isinstance(value, foundation.Array):
        if value.value is None:
            raise ValueError("Single irrigation payload is empty")
        if isinstance(value.value, (bytes, bytearray)):
            return single_irrigation_payload_from_array(value.value)
        return bytes(int(item) for item in value.value)
    if isinstance(value, (bytes, bytearray)):
        if len(value) >= 3 and value[0] == SINGLE_IRRIGATION_ARRAY_ITEM_TYPE:
            length = int.from_bytes(value[1:3], "little")
            return bytes(value[3 : 3 + length])
        return bytes(value)
    if isinstance(value, list):
        return bytes(value)
    if isinstance(value, t.LVList):
        return bytes(value)
    raise ValueError("Unsupported single irrigation payload value")

# 解析Sonoff灌溉数据
def decode_single_irrigation_payload(
    payload: bytes | list[int] | foundation.Array,
) -> SingleIrrigationState:
    """Decode the SONOFF single irrigation aggregate payload."""

    data = single_irrigation_payload_from_array(payload)
    if len(data) < SINGLE_IRRIGATION_PAYLOAD_LEN:
        raise ValueError("Single irrigation payload is too short")

    return SingleIrrigationState(
        irrigation_mode=data[0],
        total_duration_min=_u16_be(data[1:3]),
        # duration_min=_u16_be(data[3:5]),
        # interval_duration_min=_u16_be(data[5:7]),
        amount_unit=data[7],
        amount=_u16_be(data[8:10]),
        fail_safe_duration_min=_u16_be(data[10:12]),
    )

# 将灌溉状态对象编码为字节 payload
def encode_single_irrigation_payload(state: SingleIrrigationState) -> bytes:
    """Encode the SONOFF single irrigation aggregate payload."""

    irrigation_mode = int(state.irrigation_mode)
    total_duration_min = state.total_duration_min
    amount = state.amount
    fail_safe_duration_min = state.fail_safe_duration_min

    if irrigation_mode == SingleIrrigationMode.Duration:
        amount = 0
        fail_safe_duration_min = 0
    elif irrigation_mode == SingleIrrigationMode.Volume:
        total_duration_min = 0
    else:
        irrigation_mode = SingleIrrigationMode.Duration
        amount = 0
        fail_safe_duration_min = 0

    payload: list[int] = [
        irrigation_mode,
        *_put_u16_be(total_duration_min),
        *_put_u16_be(0),
        *_put_u16_be(0),
        int(state.amount_unit),
        *_put_u16_be(amount),
        *_put_u16_be(fail_safe_duration_min),
    ]
    return bytes(payload)

# 水位状态枚举
class ValveState(t.enum8):
    """Water valve abnormal state bitmap."""
     # 基础状态（单一位）
    Normal = 0                    # 000 (无任何异常)
    Water_Shortage = 1 << 0       # 001 (bit0: 缺水)
    Water_Leakage = 1 << 1        # 010 (bit1: 漏水)
    Anti_Frost_Alarm = 1 << 2     # 100 (bit2: 防霜冻报警)
    Water_Shortage_Channel_2 = 1 << 4  # bit4: 二通道缺水
    # 组合状态（多位同时触发）
    Water_Shortage_And_Leakage = Water_Shortage | Water_Leakage
    Water_Shortage_And_Frost = Water_Shortage | Anti_Frost_Alarm
    Water_Leakage_And_Frost = Water_Leakage | Anti_Frost_Alarm
    All_Alarms = Water_Shortage | Water_Leakage | Anti_Frost_Alarm


class SonoffWaterValveCluster(CustomCluster):
    """SONOFF private cluster for SWV water valves."""

    cluster_id = 0xFC11
    ep_attribute = "sonoff_cluster"
    class ServerCommandDefs(foundation.BaseCommandDefs):
        """SONOFF private server command definitions."""
        # 日程计划设置命令
        irrigation_plan_set = foundation.ZCLCommandDef(
            id=IRRIGATION_PLAN_SET_COMMAND_ID,
            schema={"payload": IrrigationPlanPayload},
            is_manufacturer_specific=False,
        )
        # 日程计划删除命令
        irrigation_plan_remove = foundation.ZCLCommandDef(
            id=IRRIGATION_PLAN_REMOVE_COMMAND_ID,
            schema={"index": t.uint8_t},
            is_manufacturer_specific=False,
        )
        
    class AttributeDefs(BaseAttributeDefs):
        """SONOFF private attribute definitions."""

        # 童锁状态属性定义
        child_lock = ZCLAttributeDef(
            id=0x0000,
            type=t.Bool,
            manufacturer_code=None,
        )
        # 实时灌溉时长属性定义
        realtime_irrigation_duration = ZCLAttributeDef(
            id=0x5006,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 实时灌溉水量属性定义
        realtime_irrigation_volume = ZCLAttributeDef(
            id=0x5007,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 阀门异常状态属性定义
        valve_abnormal_state = ZCLAttributeDef(
            id=0x500C,
            type=ValveState,
            manufacturer_code=None,
        )
        # 日灌溉水量属性定义
        daily_irrigation_volume = ZCLAttributeDef(
            id=0x500F,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 用户延时结束时间属性定义
        user_delay_end_datetime = ZCLAttributeDef(
            id=0x5014,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 天气延时时长属性定义
        weather_delay_duration = ZCLAttributeDef(
            id=0x5019,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 日灌溉时长属性定义
        daily_irrigation_duration = ZCLAttributeDef(
            id=0x501A,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 小时灌溉水量属性定义
        hour_irrigation_volume = ZCLAttributeDef(
            id=0x501B,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 小时灌溉时长属性定义
        hour_irrigation_duration = ZCLAttributeDef(
            id=0x501C,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # 单次灌溉设置属性定义
        single_irrigation_set = ZCLAttributeDef(
            id=0x501D,
            type=SingleIrrigationPayload,
            manufacturer_code=None,
        )
        # 水流单位属性定义
        unit_of_water_flow = ZCLAttributeDef(
            id=0x5021,
            type=t.uint8_t,
            manufacturer_code=None,
        )

    def __init__(self, *args, **kwargs):
        """Initialize and listen for single irrigation aggregate changes."""

        super().__init__(*args, **kwargs)
        self._single_irrigation_state = SingleIrrigationState()
        self.on_event(
            AttributeReadEvent.event_type, self._handle_single_irrigation_change
        )
        self.on_event(
            AttributeReportedEvent.event_type, self._handle_single_irrigation_change
        )
        self.on_event(
            AttributeUpdatedEvent.event_type, self._handle_single_irrigation_change
        )
        self.on_event(
            AttributeWrittenEvent.event_type, self._handle_single_irrigation_change
        )

    # 单次灌溉属性改变处理
    def _handle_single_irrigation_change(
        self,
        event: AttributeReadEvent
        | AttributeReportedEvent
        | AttributeUpdatedEvent
        | AttributeWrittenEvent,
    ) -> None:
        """Sync decoded single irrigation state to the local config cluster."""

        if isinstance(event, AttributeWrittenEvent) and event.status != Status.SUCCESS:
            return
        
        if event.attribute_id == self.AttributeDefs.unit_of_water_flow.id:
            if hasattr(self.endpoint, "sonoff_single_irrigation_config"):
                self.endpoint.sonoff_single_irrigation_config.update_amount_unit(
                    int(event.value)
                )
            return
        
        if event.attribute_id != self.AttributeDefs.single_irrigation_set.id:
            return

        values = [event.value]
        if isinstance(event, AttributeReadEvent) and event.raw_value is not event.value:
            values.append(event.raw_value)

        for value in values:
            try:
                self._single_irrigation_state = decode_single_irrigation_payload(value) # 解析单次灌溉设置属性值
                break
            except (TypeError, ValueError):
                continue
        else:
            return

        if hasattr(self.endpoint, "sonoff_single_irrigation_config"):
            self.endpoint.sonoff_single_irrigation_config._has_device_single_irrigation_state = True
            self.endpoint.sonoff_single_irrigation_config.update_single_irrigation_state(
                self._single_irrigation_state
            )

    async def apply_custom_configuration(self, *args, **kwargs):
        """Read single irrigation configuration during pairing."""

        await self.read_attributes(
            [
                self.AttributeDefs.unit_of_water_flow.id,
            ]
        )

#****************************** 手动灌溉实体 start *****************************************************

class SonoffSingleIrrigationConfigCluster(LocalDataCluster):
    """Local cluster exposing pieces of the aggregate single irrigation setting."""

    cluster_id = 0xFBFE
    ep_attribute = "sonoff_single_irrigation_config"

    class AttributeDefs(BaseAttributeDefs):
        """Local single irrigation configuration attributes."""

        irrigation_mode: Final = ZCLAttributeDef(
            id=0x0010, type=SingleIrrigationMode
        )
        total_duration_min: Final = ZCLAttributeDef(id=0x0011, type=t.uint16_t)
        amount_unit: Final = ZCLAttributeDef(id=0x0012, type=t.uint8_t)
        # duration_min: Final = ZCLAttributeDef(id=0x0012, type=t.uint16_t)
        # interval_duration_min: Final = ZCLAttributeDef(id=0x0013, type=t.uint16_t)
        amount: Final = ZCLAttributeDef(id=0x0013, type=t.uint16_t)
        fail_safe_duration_min: Final = ZCLAttributeDef(id=0x0014, type=t.uint16_t)

    def __init__(self, *args, **kwargs):
        """Initialize with conservative single irrigation defaults."""

        super().__init__(*args, **kwargs)
        self._single_irrigation_state = SingleIrrigationState()
        self._amount_unit = IrrigationAmountUnit.Liter
        self._has_device_single_irrigation_state = False
        self._update_attribute(
            self.AttributeDefs.irrigation_mode.id,
            self._single_irrigation_state.irrigation_mode,
        )
        self._update_attribute(
            self.AttributeDefs.total_duration_min.id,
            self._single_irrigation_state.total_duration_min,
        )
        self._update_attribute(
            self.AttributeDefs.amount_unit.id,
            self._amount_unit,
        )
        self._update_attribute(
            self.AttributeDefs.amount.id,
            self._single_irrigation_state.amount,
        )
        self._update_attribute(
            self.AttributeDefs.fail_safe_duration_min.id,
            self._single_irrigation_state.fail_safe_duration_min,
        )

# 供解析函数调用，来自设备端，更新至实体用于HA
    def update_single_irrigation_state(self, state: SingleIrrigationState) -> None:
        """Update local attributes from decoded single irrigation state."""

        self._single_irrigation_state = SingleIrrigationState(
            irrigation_mode=state.irrigation_mode,
            total_duration_min=state.total_duration_min,
            amount_unit=state.amount_unit,
            amount=self._single_irrigation_state.amount,
            fail_safe_duration_min=self._single_irrigation_state.fail_safe_duration_min,
        )
        self._amount_unit = int(state.amount_unit)
        if state.irrigation_mode == SingleIrrigationMode.Volume:
            if state.amount != 0:
                self._single_irrigation_state.amount = state.amount
            if state.fail_safe_duration_min != 0:
                self._single_irrigation_state.fail_safe_duration_min = (
                    state.fail_safe_duration_min
                )

        updates = {
            self.AttributeDefs.irrigation_mode.id: self._single_irrigation_state.irrigation_mode,
            self.AttributeDefs.total_duration_min.id: self._single_irrigation_state.total_duration_min,
            # self.AttributeDefs.duration_min.id: self._single_irrigation_state.duration_min,
            # self.AttributeDefs.interval_duration_min.id: self._single_irrigation_state.interval_duration_min,
            self.AttributeDefs.amount_unit.id: self._amount_unit,
            self.AttributeDefs.amount.id: self._single_irrigation_state.amount,
            self.AttributeDefs.fail_safe_duration_min.id: self._single_irrigation_state.fail_safe_duration_min,
        }
        for attr_id, value in updates.items():
            self._update_attribute(attr_id, value)

# 更新单位属性值
    def update_amount_unit(self, unit: int) -> None:
        """Update local amount unit from the real 0x5021 attribute."""
        self._amount_unit = int(unit)
        self._update_attribute(self.AttributeDefs.amount_unit.id, self._amount_unit)
        
         
    
    # 更新手动灌溉相关属性值至zigbee
    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """Merge local config writes into the real aggregate attribute."""

        state = SingleIrrigationState(
            irrigation_mode=self._single_irrigation_state.irrigation_mode,
            total_duration_min=self._single_irrigation_state.total_duration_min,
            amount_unit=self._single_irrigation_state.amount_unit,
            amount=self._single_irrigation_state.amount,
            fail_safe_duration_min=self._single_irrigation_state.fail_safe_duration_min,
        )
        pending_mode = state.irrigation_mode
        pending_amount_unit = self._amount_unit

        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                pending_mode = int(value)
            elif attr_id == self.AttributeDefs.amount_unit.id:
                pending_amount_unit = int(value)

        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            # 若为时长模式，禁止设置水量和安全时长
            if (
                pending_mode == SingleIrrigationMode.Duration
                and attr_id
                in (
                    self.AttributeDefs.amount.id,
                    self.AttributeDefs.fail_safe_duration_min.id,
                )
            ):
                raise ValueError(
                    "Single irrigation amount and fail safe duration are only "
                    "configurable in volume mode"
                )
            # 若为水量模式，禁止设置总时长
            if (
                pending_mode == SingleIrrigationMode.Volume
                and attr_id == self.AttributeDefs.total_duration_min.id
            ):
                raise ValueError(
                    "Single irrigation total duration is only configurable in "
                    "duration mode"
                )
            
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                state.irrigation_mode = int(value)

            elif attr_id == self.AttributeDefs.total_duration_min.id:
                state.total_duration_min = int(value)

            elif attr_id == self.AttributeDefs.amount_unit.id:
                self._amount_unit = int(value)
                state.amount_unit = int(value)

            elif attr_id == self.AttributeDefs.amount.id:
                state.amount = int(value)

            elif attr_id == self.AttributeDefs.fail_safe_duration_min.id:
                state.fail_safe_duration_min = int(value)

        attr_ids = {self.find_attribute(attr).id for attr in attributes}
        config_result = None
        if self.AttributeDefs.amount_unit.id in attr_ids:
            state.amount_unit = pending_amount_unit

        if attr_ids.intersection(
            {
                self.AttributeDefs.irrigation_mode.id,
                self.AttributeDefs.total_duration_min.id,
                self.AttributeDefs.amount_unit.id,
                self.AttributeDefs.amount.id,
                self.AttributeDefs.fail_safe_duration_min.id,
            }
        ):
            payload = encode_single_irrigation_payload(state)
            zcl_array = single_irrigation_array_from_payload_test(payload)
            raw_result = await self.endpoint.sonoff_cluster.write_attributes_raw(
                [
                    foundation.Attribute(
                        attrid=SonoffWaterValveCluster.AttributeDefs.single_irrigation_set.id,
                        value=foundation.TypeValue(
                            type=foundation.DataTypeId.array,
                            value=zcl_array,
                        ),
                    )
                ]
            )
            config_result = raw_result
            # 只有当写入成功时才更新本地状态，否则保持原状态以免与设备端不一致
        if config_result is not None and self._write_succeeded(config_result):
            self._has_device_single_irrigation_state = False
            self._single_irrigation_state = state
            self._update_attribute(
                self.AttributeDefs.irrigation_mode.id,
                self._single_irrigation_state.irrigation_mode,
            )
            self._update_attribute(
                self.AttributeDefs.total_duration_min.id,
                self._single_irrigation_state.total_duration_min,
            )
            self._update_attribute(
                self.AttributeDefs.amount_unit.id,
                self._single_irrigation_state.amount_unit,
            )
            self._update_attribute(
                self.AttributeDefs.amount.id,
                self._single_irrigation_state.amount,
            )
            self._update_attribute(
                self.AttributeDefs.fail_safe_duration_min.id,
                self._single_irrigation_state.fail_safe_duration_min,
            )
            self._update_attribute(
                self.AttributeDefs.amount_unit.id,
                self._single_irrigation_state.amount_unit,
            )
        return config_result if config_result is not None else [foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]

    @staticmethod
    def _write_succeeded(result: list) -> bool:
        """Return whether a Zigpy write_attributes response succeeded."""

        try:
            records = result[0]
        except (IndexError, TypeError):
            return False

        # zigpy may return either:
        # 1) WriteAttributesResponse (iterable of status records)
        # 2) list[WriteAttributesStatusRecord]
        # 3) list[list[WriteAttributesStatusRecord]]
        # Normalize to an iterable of status records.
        if hasattr(records, "status"):
            records = result
        elif hasattr(records, "status_records"):
            records = records.status_records

        try:
            return all(getattr(record, "status", None) == Status.SUCCESS for record in records)
        except TypeError:
            return False

#****************************** 日程相关 start *****************************************************
class SonoffIrrigationPlanConfigCluster(LocalDataCluster):
    """Local cluster for auto irrigation plan configuration entities."""

    cluster_id = 0xFBFD
    ep_attribute = "sonoff_irrigation_plan_config"

    class AttributeDefs(BaseAttributeDefs):
        """Attribute definitions."""

        plan_index: Final = ZCLAttributeDef(id=0x0020, type=t.uint8_t)
        effective_year: Final = ZCLAttributeDef(id=0x0021, type=t.uint16_t)
        effective_month: Final = ZCLAttributeDef(id=0x0022, type=t.uint8_t)
        effective_day: Final = ZCLAttributeDef(id=0x0023, type=t.uint8_t)
        repeat_mode: Final = ZCLAttributeDef(id=0x0024, type=IrrigationPlanRepeat)
        repeat_value: Final = ZCLAttributeDef(id=0x0025, type=t.uint8_t)
        amount_unit: Final = ZCLAttributeDef(id=0x0026, type=t.uint8_t)
        weekday_monday: Final = ZCLAttributeDef(id=0x0027, type=t.uint8_t)
        weekday_tuesday: Final = ZCLAttributeDef(id=0x0028, type=t.uint8_t)
        weekday_wednesday: Final = ZCLAttributeDef(id=0x0029, type=t.uint8_t)
        weekday_thursday: Final = ZCLAttributeDef(id=0x002A, type=t.uint8_t)
        weekday_friday: Final = ZCLAttributeDef(id=0x002B, type=t.uint8_t)
        weekday_saturday: Final = ZCLAttributeDef(id=0x002C, type=t.uint8_t)
        weekday_sunday: Final = ZCLAttributeDef(id=0x002D, type=t.uint8_t)
        start_hour: Final = ZCLAttributeDef(id=0x002E, type=t.uint8_t)
        start_minute: Final = ZCLAttributeDef(id=0x002F, type=t.uint8_t)
        apply_plan: Final = ZCLAttributeDef(id=0x0030, type=t.uint8_t)
        remove_plan: Final = ZCLAttributeDef(id=0x0031, type=t.uint8_t)

    def __init__(self, *args, **kwargs):
        """Initialize local schedule state."""
        super().__init__(*args, **kwargs)
        now = datetime.now()
        self._plan_index = 0
        self._effective_year = now.year
        self._effective_month = now.month
        self._effective_day = now.day
        self._repeat_mode = IrrigationPlanRepeat.Custom
        self._repeat_value = 0
        self._amount_unit = IrrigationAmountUnit.Liter
        self._weekday_mask = 0
        self._start_hour = 8
        self._start_minute = 0
        self._applied_plan_signatures_by_index: dict[int, tuple[int, ...]] = {}
        self._update_all_attributes()
        self._ui_date_year, self._ui_date_month, self._ui_date_day = now.year, now.month, now.day

# 将本地灌溉计划数据同步至实体属性
    def _update_all_attributes(self) -> None:
        """Mirror the local plan into entity attributes."""
        updates = {
            self.AttributeDefs.plan_index.id: self._plan_index,
            self.AttributeDefs.effective_year.id: self._effective_year,
            self.AttributeDefs.effective_month.id: self._effective_month,
            self.AttributeDefs.effective_day.id: self._effective_day,
            self.AttributeDefs.repeat_mode.id: self._repeat_mode,
            self.AttributeDefs.repeat_value.id: self._repeat_value,
            self.AttributeDefs.amount_unit.id: self._amount_unit,
            self.AttributeDefs.weekday_monday.id: int(bool(self._weekday_mask & 0x01)),
            self.AttributeDefs.weekday_tuesday.id: int(bool(self._weekday_mask & 0x02)),
            self.AttributeDefs.weekday_wednesday.id: int(bool(self._weekday_mask & 0x04)),
            self.AttributeDefs.weekday_thursday.id: int(bool(self._weekday_mask & 0x08)),
            self.AttributeDefs.weekday_friday.id: int(bool(self._weekday_mask & 0x10)),
            self.AttributeDefs.weekday_saturday.id: int(bool(self._weekday_mask & 0x20)),
            self.AttributeDefs.weekday_sunday.id: int(bool(self._weekday_mask & 0x40)),
            self.AttributeDefs.start_hour.id: self._start_hour,
            self.AttributeDefs.start_minute.id: self._start_minute,
        }
        for attr_id, value in updates.items():
            self._update_attribute(attr_id, value)

# 构建灌溉计划（供创建日程时使用、删除时不需用到）
    def _plan_from_current_config(self) -> IrrigationPlan:
        """Build a firmware plan from simple schedule fields and irrigation config."""
        _validate_irrigation_plan_index(self._plan_index)
        # 启用日期
        enable_datetime = _zigbee_date_timestamp(
            self._effective_year, self._effective_month, self._effective_day
        )
        # 启动时间
        start_datetime = _seconds_from_midnight(self._start_hour, self._start_minute)

        # 灌溉状态
        irrigation_state = SingleIrrigationState()
        if hasattr(self.endpoint, "sonoff_single_irrigation_config"):
            config = self.endpoint.sonoff_single_irrigation_config
            irrigation_state = config._single_irrigation_state

        # 重复策略
        repeat_value = self._repeat_value
        if self._repeat_mode == IrrigationPlanRepeat.Custom:
            repeat_value = self._weekday_mask

        return IrrigationPlan(
            index=self._plan_index,
            enabled=1,
            enable_datetime=enable_datetime,
            irrigation_mode=irrigation_state.irrigation_mode,
            start_datetime=start_datetime,
            total_duration_min=irrigation_state.total_duration_min,
            amount_unit=self._amount_unit,
            amount=irrigation_state.amount,
            fail_safe_duration_min=irrigation_state.fail_safe_duration_min,
            create_datetime=_zigbee_now_timestamp(),
            repeat_mode=self._repeat_mode,
            repeat_value=repeat_value,
        )

# 先本地缓存修改，最后统一提交
# 在HA修改实体时，会缓存起来。当点击应用、删除按钮时，才会更新至Zigbee设备端
    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """Update local plan fields or trigger set/remove actions."""
        result = []
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.plan_index.id:
                _validate_irrigation_plan_index(value)
                self._plan_index = int(value)
            elif attr_id == self.AttributeDefs.effective_year.id:
                self._effective_year = int(value)
            elif attr_id == self.AttributeDefs.effective_month.id:
                self._effective_month = int(value)
            elif attr_id == self.AttributeDefs.effective_day.id:
                self._effective_day = int(value)
            elif attr_id == self.AttributeDefs.repeat_mode.id:
                self._repeat_mode = int(value)
            elif attr_id == self.AttributeDefs.repeat_value.id:
                self._repeat_value = int(value)
            elif attr_id == self.AttributeDefs.amount_unit.id:
                self._amount_unit = int(value)
            elif attr_id == self.AttributeDefs.weekday_monday.id:
                self._weekday_mask = (self._weekday_mask & ~0x01) | int(bool(value))
            elif attr_id == self.AttributeDefs.weekday_tuesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x02) | (int(bool(value)) << 1)
            elif attr_id == self.AttributeDefs.weekday_wednesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x04) | (int(bool(value)) << 2)
            elif attr_id == self.AttributeDefs.weekday_thursday.id:
                self._weekday_mask = (self._weekday_mask & ~0x08) | (int(bool(value)) << 3)
            elif attr_id == self.AttributeDefs.weekday_friday.id:
                self._weekday_mask = (self._weekday_mask & ~0x10) | (int(bool(value)) << 4)
            elif attr_id == self.AttributeDefs.weekday_saturday.id:
                self._weekday_mask = (self._weekday_mask & ~0x20) | (int(bool(value)) << 5)
            elif attr_id == self.AttributeDefs.weekday_sunday.id:
                self._weekday_mask = (self._weekday_mask & ~0x40) | (int(bool(value)) << 6)
            elif attr_id == self.AttributeDefs.start_hour.id:
                self._start_hour = int(value)
            elif attr_id == self.AttributeDefs.start_minute.id:
                self._start_minute = int(value)
            elif attr_id == self.AttributeDefs.apply_plan.id:   # 设置计划
                plan = self._plan_from_current_config()
                plan_signature = irrigation_plan_dedupe_key(plan)
                if plan_signature in self._applied_plan_signatures_by_index.values():
                    continue
                payload = encode_irrigation_plan_payload(plan)
                # 将payload封装在ZCL命令中发送给Zigbee设备
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_SET_COMMAND_ID,
                    payload=IrrigationPlanPayload(payload),
                    manufacturer=None,
                    expect_reply=False,
                )
                self._applied_plan_signatures_by_index[int(plan.index)] = plan_signature
            elif attr_id == self.AttributeDefs.remove_plan.id:  # 移除计划
                _validate_irrigation_plan_index(self._plan_index)
                # 将payload封装在ZCL命令中发送给Zigbee设备
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_REMOVE_COMMAND_ID,
                    index=t.uint8_t(self._plan_index),
                    manufacturer=None,
                    expect_reply=False,
                )
                self._applied_plan_signatures_by_index.pop(int(self._plan_index), None)

        self._update_all_attributes()
        if result:
            return result
        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]

#****************************** 通道2 日程相关 start *****************************************************
class SonoffIrrigationPlanConfigClusterCh2(LocalDataCluster):
    """Local cluster for channel 2 auto irrigation plan configuration entities.

    Fully independent from the channel 1 cluster.  Registered on endpoint 2
    via ``adds(..., endpoint_id=2)`` so that zigpy automatically binds it to
    the correct endpoint, and ``self.endpoint.sonoff_cluster.command(...)``
    naturally reaches the firmware channel-2 handler.
    """

    cluster_id = 0xFBFB
    ep_attribute = "sonoff_irrigation_plan_config_ch2"

    class AttributeDefs(BaseAttributeDefs):
        """Attribute definitions (shifted IDs vs channel 1)."""

        plan_index: Final = ZCLAttributeDef(id=0x0040, type=t.uint8_t)
        effective_year: Final = ZCLAttributeDef(id=0x0041, type=t.uint16_t)
        effective_month: Final = ZCLAttributeDef(id=0x0042, type=t.uint8_t)
        effective_day: Final = ZCLAttributeDef(id=0x0043, type=t.uint8_t)
        repeat_mode: Final = ZCLAttributeDef(id=0x0044, type=IrrigationPlanRepeat)
        repeat_value: Final = ZCLAttributeDef(id=0x0045, type=t.uint8_t)
        amount_unit: Final = ZCLAttributeDef(id=0x0046, type=t.uint8_t)
        weekday_monday: Final = ZCLAttributeDef(id=0x0047, type=t.uint8_t)
        weekday_tuesday: Final = ZCLAttributeDef(id=0x0048, type=t.uint8_t)
        weekday_wednesday: Final = ZCLAttributeDef(id=0x0049, type=t.uint8_t)
        weekday_thursday: Final = ZCLAttributeDef(id=0x004A, type=t.uint8_t)
        weekday_friday: Final = ZCLAttributeDef(id=0x004B, type=t.uint8_t)
        weekday_saturday: Final = ZCLAttributeDef(id=0x004C, type=t.uint8_t)
        weekday_sunday: Final = ZCLAttributeDef(id=0x004D, type=t.uint8_t)
        start_hour: Final = ZCLAttributeDef(id=0x004E, type=t.uint8_t)
        start_minute: Final = ZCLAttributeDef(id=0x004F, type=t.uint8_t)
        apply_plan: Final = ZCLAttributeDef(id=0x0050, type=t.uint8_t)
        remove_plan: Final = ZCLAttributeDef(id=0x0051, type=t.uint8_t)

    def __init__(self, *args, **kwargs):
        """Initialize local schedule state."""
        super().__init__(*args, **kwargs)
        now = datetime.now()
        self._plan_index = 0
        self._effective_year = now.year
        self._effective_month = now.month
        self._effective_day = now.day
        self._repeat_mode = IrrigationPlanRepeat.Custom
        self._repeat_value = 0
        self._amount_unit = IrrigationAmountUnit.Liter
        self._weekday_mask = 0
        self._start_hour = 8
        self._start_minute = 0
        self._applied_plan_signatures_by_index: dict[int, tuple[int, ...]] = {}
        self._update_all_attributes()

    def _update_all_attributes(self) -> None:
        """Mirror the local plan into entity attributes."""
        updates = {
            self.AttributeDefs.plan_index.id: self._plan_index,
            self.AttributeDefs.effective_year.id: self._effective_year,
            self.AttributeDefs.effective_month.id: self._effective_month,
            self.AttributeDefs.effective_day.id: self._effective_day,
            self.AttributeDefs.repeat_mode.id: self._repeat_mode,
            self.AttributeDefs.repeat_value.id: self._repeat_value,
            self.AttributeDefs.amount_unit.id: self._amount_unit,
            self.AttributeDefs.weekday_monday.id: int(bool(self._weekday_mask & 0x01)),
            self.AttributeDefs.weekday_tuesday.id: int(bool(self._weekday_mask & 0x02)),
            self.AttributeDefs.weekday_wednesday.id: int(bool(self._weekday_mask & 0x04)),
            self.AttributeDefs.weekday_thursday.id: int(bool(self._weekday_mask & 0x08)),
            self.AttributeDefs.weekday_friday.id: int(bool(self._weekday_mask & 0x10)),
            self.AttributeDefs.weekday_saturday.id: int(bool(self._weekday_mask & 0x20)),
            self.AttributeDefs.weekday_sunday.id: int(bool(self._weekday_mask & 0x40)),
            self.AttributeDefs.start_hour.id: self._start_hour,
            self.AttributeDefs.start_minute.id: self._start_minute,
        }
        for attr_id, value in updates.items():
            self._update_attribute(attr_id, value)

    def _plan_from_current_config(self) -> IrrigationPlan:
        """Build a firmware plan from simple schedule fields and irrigation config."""
        _validate_irrigation_plan_index(self._plan_index)
        enable_datetime = _zigbee_date_timestamp(
            self._effective_year, self._effective_month, self._effective_day
        )
        start_datetime = _seconds_from_midnight(self._start_hour, self._start_minute)

        irrigation_state = SingleIrrigationState()
        if hasattr(self.endpoint, "sonoff_single_irrigation_config"):
            config = self.endpoint.sonoff_single_irrigation_config
            irrigation_state = config._single_irrigation_state

        repeat_value = self._repeat_value
        if self._repeat_mode == IrrigationPlanRepeat.Custom:
            repeat_value = self._weekday_mask

        return IrrigationPlan(
            index=self._plan_index,
            enabled=1,
            enable_datetime=enable_datetime,
            irrigation_mode=irrigation_state.irrigation_mode,
            start_datetime=start_datetime,
            total_duration_min=irrigation_state.total_duration_min,
            amount_unit=self._amount_unit,
            amount=irrigation_state.amount,
            fail_safe_duration_min=irrigation_state.fail_safe_duration_min,
            create_datetime=_zigbee_now_timestamp(),
            repeat_mode=self._repeat_mode,
            repeat_value=repeat_value,
        )

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """Update local plan fields or trigger set/remove actions (channel 2)."""
        result = []
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.plan_index.id:
                _validate_irrigation_plan_index(value)
                self._plan_index = int(value)
            elif attr_id == self.AttributeDefs.effective_year.id:
                self._effective_year = int(value)
            elif attr_id == self.AttributeDefs.effective_month.id:
                self._effective_month = int(value)
            elif attr_id == self.AttributeDefs.effective_day.id:
                self._effective_day = int(value)
            elif attr_id == self.AttributeDefs.repeat_mode.id:
                self._repeat_mode = int(value)
            elif attr_id == self.AttributeDefs.repeat_value.id:
                self._repeat_value = int(value)
            elif attr_id == self.AttributeDefs.amount_unit.id:
                self._amount_unit = int(value)
            elif attr_id == self.AttributeDefs.weekday_monday.id:
                self._weekday_mask = (self._weekday_mask & ~0x01) | int(bool(value))
            elif attr_id == self.AttributeDefs.weekday_tuesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x02) | (int(bool(value)) << 1)
            elif attr_id == self.AttributeDefs.weekday_wednesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x04) | (int(bool(value)) << 2)
            elif attr_id == self.AttributeDefs.weekday_thursday.id:
                self._weekday_mask = (self._weekday_mask & ~0x08) | (int(bool(value)) << 3)
            elif attr_id == self.AttributeDefs.weekday_friday.id:
                self._weekday_mask = (self._weekday_mask & ~0x10) | (int(bool(value)) << 4)
            elif attr_id == self.AttributeDefs.weekday_saturday.id:
                self._weekday_mask = (self._weekday_mask & ~0x20) | (int(bool(value)) << 5)
            elif attr_id == self.AttributeDefs.weekday_sunday.id:
                self._weekday_mask = (self._weekday_mask & ~0x40) | (int(bool(value)) << 6)
            elif attr_id == self.AttributeDefs.start_hour.id:
                self._start_hour = int(value)
            elif attr_id == self.AttributeDefs.start_minute.id:
                self._start_minute = int(value)
            elif attr_id == self.AttributeDefs.apply_plan.id:
                plan = self._plan_from_current_config()
                plan_signature = irrigation_plan_dedupe_key(plan)
                if plan_signature in self._applied_plan_signatures_by_index.values():
                    continue
                payload = encode_irrigation_plan_payload(plan)
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_SET_COMMAND_ID,
                    payload=IrrigationPlanPayload(payload),
                    manufacturer=None,
                    expect_reply=False,
                )
                self._applied_plan_signatures_by_index[int(plan.index)] = plan_signature
            elif attr_id == self.AttributeDefs.remove_plan.id:
                _validate_irrigation_plan_index(self._plan_index)
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_REMOVE_COMMAND_ID,
                    index=t.uint8_t(self._plan_index),
                    manufacturer=None,
                    expect_reply=False,
                )
                self._applied_plan_signatures_by_index.pop(int(self._plan_index), None)

        self._update_all_attributes()
        if result:
            return result
        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]

###################### 以下为实体创建（手动灌溉、日程、通用） ###############################
# 把手动默认模式的参数单独创建实体，方便数组数据解析后用于实体(数组数据无法直接用于实体)
def add_single_irrigation_config_entities(builder: QuirkBuilder) -> QuirkBuilder:
    """Add config entities for the aggregate single irrigation attribute 0x501D."""

    return (
        builder.adds(SonoffSingleIrrigationConfigCluster)
        .enum(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.irrigation_mode.name,
            SingleIrrigationMode,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            entity_type=EntityType.CONFIG,
            translation_key="manual_single_irrigation_mode",
            fallback_name="手动 single irrigation mode",
        )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.total_duration_min.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            # entity_type=EntityType.CONFIG,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="manual_single_irrigation_total_duration",
            fallback_name="手动 single irrigation total duration",
        )
        # .number(
        #     SonoffSingleIrrigationConfigCluster.AttributeDefs.duration_min.name,
        #     SonoffSingleIrrigationConfigCluster.cluster_id,
        #     min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
        #     max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
        #     step=SINGLE_IRRIGATION_STEP_MIN,
        #     entity_type=EntityType.CONFIG,
        #     device_class=NumberDeviceClass.DURATION,
        #     unit=UnitOfTime.MINUTES,
        #     translation_key="single_irrigation_duration",
        #     fallback_name="Single irrigation duration",
        # )
        # .number(
        #     SonoffSingleIrrigationConfigCluster.AttributeDefs.interval_duration_min.name,
        #     SonoffSingleIrrigationConfigCluster.cluster_id,
        #     min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
        #     max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
        #     step=SINGLE_IRRIGATION_STEP_MIN,
        #     entity_type=EntityType.CONFIG,
        #     device_class=NumberDeviceClass.DURATION,
        #     unit=UnitOfTime.MINUTES,
        #     translation_key="single_irrigation_interval_duration",
        #     fallback_name="Single irrigation interval duration",
        # )
        # .enum(
        #     SonoffSingleIrrigationConfigCluster.AttributeDefs.amount_unit.name,
        #     IrrigationAmountUnit,
        #     SonoffSingleIrrigationConfigCluster.cluster_id,
        #     entity_type=EntityType.CONFIG,
        #     translation_key="single_irrigation_amount_unit",
        #     fallback_name="Single irrigation amount unit",
        # )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.amount.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_AMOUNT_MIN,
            max_value=SINGLE_IRRIGATION_AMOUNT_MAX,
            step=1,
            mode="box",
            # entity_type=EntityType.CONFIG,
            translation_key="manual_single_irrigation_amount",
            fallback_name="手动 single irrigation amount",
        )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.fail_safe_duration_min.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_FAIL_SAFE_MIN,
            max_value=SINGLE_IRRIGATION_FAIL_SAFE_MAX,
            step=SINGLE_IRRIGATION_STEP_MIN,
            # entity_type=EntityType.CONFIG,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="manual_single_irrigation_fail_safe_duration",
            fallback_name="手动 single irrigation fail safe duration",
        )
    )

# 通道2 手动灌溉相关实体
def add_single_irrigation_config_entities_ch2(builder: QuirkBuilder) -> QuirkBuilder:
    """Add config entities for channel 2 aggregate single irrigation settings."""

    return (
        builder.adds(SonoffSingleIrrigationConfigCluster, endpoint_id=2)
        .enum(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.irrigation_mode.name,
            SingleIrrigationMode,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            endpoint_id=2,
            entity_type=EntityType.CONFIG,
            translation_key="manual_ch2_single_irrigation_mode",
            fallback_name="通道2 手动 single irrigation mode",
        )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.total_duration_min.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="manual_ch2_single_irrigation_total_duration",
            fallback_name="通道2 手动 single irrigation total duration",
        )
        .enum(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.amount_unit.name,
            IrrigationAmountUnit,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            endpoint_id=2,
            entity_type=EntityType.CONFIG,
            translation_key="manual_ch2_irrigation_amount_unit",
            fallback_name="通道2 手动 irrigation amount unit",
        )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.amount.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_AMOUNT_MIN,
            max_value=SINGLE_IRRIGATION_AMOUNT_MAX,
            step=1,
            mode="box",
            translation_key="manual_ch2_single_irrigation_amount",
            fallback_name="通道2 手动 single irrigation amount",
        )
        .number(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.fail_safe_duration_min.name,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_FAIL_SAFE_MIN,
            max_value=SINGLE_IRRIGATION_FAIL_SAFE_MAX,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="manual_ch2_single_irrigation_fail_safe_duration",
            fallback_name="通道2 手动 single irrigation fail safe duration",
        )
    )

# 通道1 日程相关实体
def add_irrigation_plan_config_entities(builder: QuirkBuilder) -> QuirkBuilder:
    """Add config entities for channel 1 irrigation plan attribute."""

    return (
        builder.adds(SonoffIrrigationPlanConfigCluster)
         .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.plan_index.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=5,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_index",
            fallback_name="通道1 日程 irrigation plan index",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_year.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=2000,
            max_value=2099,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_year",
            fallback_name="通道1 日程 effective year",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_month.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=1,
            max_value=12,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_month",
            fallback_name="通道1 日程 effective month",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_day.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=1,
            max_value=31,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_day",
            fallback_name="通道1 日程 effective day",
        )
        .enum(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.amount_unit.name,
            IrrigationAmountUnit,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_amount_unit",
            fallback_name="通道1 日程 irrigation amount unit",
        )
        # 循环模式
        .enum(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.repeat_mode.name,
            IrrigationPlanRepeat,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_repeat_mode",
            fallback_name="通道1 日程 repeat mode",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.repeat_value.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=30,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_repeat_value",
            fallback_name="通道1 日程 repeat value",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_monday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_monday",
            fallback_name="通道1 日程 Monday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_tuesday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_tuesday",
            fallback_name="通道1 日程 Tuesday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_wednesday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_wednesday",
            fallback_name="通道1 日程 Wednesday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_thursday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_thursday",
            fallback_name="通道1 日程 Thursday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_friday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_friday",
            fallback_name="通道1 日程 Friday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_saturday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_saturday",
            fallback_name="通道1 日程 Saturday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_sunday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_sunday",
            fallback_name="通道1 日程 Sunday",
        )
        # 开始时间：时
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.start_hour.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=23,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_start_hour",
            fallback_name="通道1 日程 start hour",
        )
        # 开始时间：分
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.start_minute.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=59,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_start_minute",
            fallback_name="通道1 日程 start minute",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.apply_plan.name,
            SonoffIrrigationPlanConfigCluster.AttributeDefs.apply_plan.id,
            cluster_id=SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_set",
            fallback_name="通道1 日程设置",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.remove_plan.name,
            SonoffIrrigationPlanConfigCluster.AttributeDefs.remove_plan.id,
            cluster_id=SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_remove",
            fallback_name="通道1 日程删除",
        )
    )

# 通道2 日程相关实体
def add_irrigation_plan_config_entities_ch2(builder: QuirkBuilder) -> QuirkBuilder:
    """Add config entities for channel 2 irrigation plan attribute."""

    return (
        builder.adds(SonoffIrrigationPlanConfigClusterCh2, endpoint_id=2)
         .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.plan_index.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=5,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_index",
            fallback_name="通道2 日程 irrigation plan index",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.effective_year.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=2000,
            max_value=2099,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_effective_year",
            fallback_name="通道2 日程 effective year",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.effective_month.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=1,
            max_value=12,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_effective_month",
            fallback_name="通道2 日程 effective month",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.effective_day.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=1,
            max_value=31,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_effective_day",
            fallback_name="通道2 日程 effective day",
        )
        .enum(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.amount_unit.name,
            IrrigationAmountUnit,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,

            translation_key="schedule_ch2_irrigation_amount_unit",
            fallback_name="通道2 日程 irrigation amount unit",
        )
        # 循环模式
        .enum(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.repeat_mode.name,
            IrrigationPlanRepeat,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            translation_key="schedule_ch2_irrigation_plan_repeat_mode",
            fallback_name="通道2 日程 repeat mode",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.repeat_value.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=30,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_repeat_value",
            fallback_name="通道2 日程 repeat value",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_monday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_monday",
            fallback_name="通道2 日程 Monday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_tuesday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_tuesday",
            fallback_name="通道2 日程 Tuesday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_wednesday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_wednesday",
            fallback_name="通道2 日程 Wednesday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_thursday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_thursday",
            fallback_name="通道2 日程 Thursday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_friday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_friday",
            fallback_name="通道2 日程 Friday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_saturday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_saturday",
            fallback_name="通道2 日程 Saturday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_sunday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_sunday",
            fallback_name="通道2 日程 Sunday",
        )
        # 开始时间：时
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.start_hour.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=23,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_start_hour",
            fallback_name="通道2 日程 start hour",
        )
        # 开始时间：分
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.start_minute.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=59,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_start_minute",
            fallback_name="通道2 日程 start minute",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.apply_plan.name,
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.apply_plan.id,
            endpoint_id=2,
            cluster_id=SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            translation_key="schedule_ch2_irrigation_plan_set",
            fallback_name="通道2 日程设置",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.remove_plan.name,
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.remove_plan.id,
            endpoint_id=2,
            cluster_id=SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            translation_key="schedule_ch2_irrigation_plan_remove",
            fallback_name="通道2 日程删除",
        )
    )

# 添加通用实体
def add_common_entities(builder: QuirkBuilder) -> QuirkBuilder:
    """Add shared SWV private-cluster entities."""

    builder = builder.replaces(SonoffWaterValveCluster).replaces(
        SonoffWaterValveCluster, endpoint_id=2
    )
    builder = add_single_irrigation_config_entities(builder)
    builder = add_single_irrigation_config_entities_ch2(builder)
    builder = add_irrigation_plan_config_entities(builder)
    builder = add_irrigation_plan_config_entities_ch2(builder)

    return (
        builder
        .enum(
            SonoffSingleIrrigationConfigCluster.AttributeDefs.amount_unit.name,
            IrrigationAmountUnit,
            SonoffSingleIrrigationConfigCluster.cluster_id,
            translation_key="manual_irrigation_amount_unit",
            fallback_name="手动 irrigation amount unit",
        )
        # 童锁
        .switch(
            SonoffWaterValveCluster.AttributeDefs.child_lock.name,
            SonoffWaterValveCluster.cluster_id,
            translation_key="child_lock",
            fallback_name="Child lock",
        )
        # 1. 漏水传感器（bit1）
        .binary_sensor(
            SonoffWaterValveCluster.AttributeDefs.valve_abnormal_state.name,
            SonoffWaterValveCluster.cluster_id,
            device_class=BinarySensorDeviceClass.MOISTURE,
            attribute_converter=lambda value: value & ValveState.Water_Leakage,
            unique_id_suffix="water_leak_status",
            reporting_config=ReportingConfig(
                min_interval=30, max_interval=900, reportable_change=1
            ),
            translation_key="water_leak",
            fallback_name="Water leak",
        )
        # 2. 缺水传感器（bit0）
        .binary_sensor(
            SonoffWaterValveCluster.AttributeDefs.valve_abnormal_state.name,
            SonoffWaterValveCluster.cluster_id,
            device_class=BinarySensorDeviceClass.PROBLEM,
            attribute_converter=lambda value: value
            & (ValveState.Water_Shortage | ValveState.Water_Shortage_Channel_2),
            unique_id_suffix="water_shortage_status",
            reporting_config=ReportingConfig(
                min_interval=30, max_interval=900, reportable_change=1
            ),
            translation_key="water_shortage",
            fallback_name="Water shortage",
        )
        # 实时灌溉时长
        .sensor(
            attribute_name=SonoffWaterValveCluster.AttributeDefs.realtime_irrigation_duration.name,
            cluster_id=SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTime.SECONDS,
            unique_id_suffix="realtime_irrigation_duration",
            translation_key="realtime_irrigation_duration",
            fallback_name="Realtime irrigation duration",
            initially_disabled=True,
        )
        # 小时内灌溉时长
        .sensor(
            attribute_name=SonoffWaterValveCluster.AttributeDefs.hour_irrigation_duration.name,
            cluster_id=SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTime.MINUTES,
            unique_id_suffix="hour_irrigation_duration",
            reporting_config=ReportingConfig(
                min_interval=30, max_interval=900, reportable_change=1
            ),
            translation_key="hour_irrigation_duration",
            fallback_name="Hour irrigation duration",
        )
        # 小时灌溉量
        .sensor(
            attribute_name=SonoffWaterValveCluster.AttributeDefs.hour_irrigation_volume.name,
            cluster_id=SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.VOLUME,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfVolume.LITERS,
            unique_id_suffix="hour_irrigation_volume",
            reporting_config=ReportingConfig(
                min_interval=30, max_interval=900, reportable_change=1
            ),
            translation_key="hour_irrigation_volume",
            fallback_name="Hour irrigation volume",
        )
        # 日灌溉时长
        .sensor(
            attribute_name=SonoffWaterValveCluster.AttributeDefs.daily_irrigation_duration.name,
            cluster_id=SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            unit=UnitOfTime.MINUTES,
            unique_id_suffix="daily_irrigation_duration",
            translation_key="daily_irrigation_duration",
            fallback_name="Daily irrigation duration",
            initially_disabled=True,
        )
        # 日灌溉量
        .sensor(
            attribute_name=SonoffWaterValveCluster.AttributeDefs.daily_irrigation_volume.name,
            cluster_id=SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.VOLUME,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfVolume.LITERS,
            unique_id_suffix="daily_irrigation_volume",
            translation_key="daily_irrigation_volume",
            fallback_name="Daily irrigation volume",
            initially_disabled=True,
        )
    )

# 型号相关
add_common_entities(
    QuirkBuilder("SONOFF", "SWV-ZF2E")
    .also_applies_to("SONOFF", "SWV-ZF2U")
    .also_applies_to("SONOFF", "SWV-ZN2E")
    .also_applies_to("SONOFF", "SWV-ZN2U")
    .also_applies_to("SONOFF", "SWV-ZF2")
    .also_applies_to("SONOFF", "SWV-ZFE")
    .also_applies_to("SONOFF", "SWV-ZFU")
    .also_applies_to("SONOFF", "SWV-ZNE")
    .also_applies_to("SONOFF", "SWV-ZNU")
).add_to_registry()



