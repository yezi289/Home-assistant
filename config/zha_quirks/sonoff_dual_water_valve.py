"""SONOFF SWV dual-channel Zigbee water valve quirk."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

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

# Constants
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
MANUAL_RAIN_DELAY_CONTROL = 0x08
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




class DelayTimestampPayload(t.FixedList):
    """Raw 4-byte big-endian delay-end Zigbee epoch timestamp."""

    _item_type = t.uint8_t
    _length = 4

# Irrigation plan payload
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

# Single irrigation mode enum (duration, volume, duration with interval)
class SingleIrrigationMode(t.enum8):
    """Single irrigation mode."""

    Duration = 0x00
    Volume = 0x01
    Duration_With_Interval = 0x02

# Amount unit enum (gallon, liter)
class IrrigationAmountUnit(t.enum8):
    """Single irrigation amount unit."""

    Liter = 0x00
    Imperial_Gallon = 0x01
    US_Gallon = 0x02

# Data class (corresponds to single irrigation array)
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

    Odd_Day  = 0x00
    Even_Day = 0x01
    Days     = 0x02
    Week     = 0x03
    Only     = 0x04


class IrrigationPlanRepeat(t.enum8):
    """Simplified irrigation schedule repeat mode."""

    Odd_Day  = 0x00
    Even_Day = 0x01
    Interval = 0x02
    Custom   = 0x03

# Data class (corresponds to plan)
@dataclass
class IrrigationPlan:
    """Sonoff auto irrigation plan in the Zigbee command payload format."""

    index: int = 0                                                          # Index
    enabled: int = 1
    enable_datetime: int = 0
    irrigation_mode: int = SingleIrrigationMode.Duration
    start_datetime: int = 0                                                 # Start time
    total_duration_min: int = SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN  # Total duration
    duration_min: int = 0
    interval_duration_min: int = 0                                          # Interval duration
    amount_unit: int = SINGLE_IRRIGATION_ZB_AMOUNT_UNIT_LITER               # Amount unit
    amount: int = SINGLE_IRRIGATION_DEFAULT_AMOUNT                          # Amount
    fail_safe_duration_min: int = SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN
    create_datetime: int = 0
    repeat_mode: int = IrrigationPlanRepeat.Custom                          # Repeat mode
    repeat_value: int = 0

# Validate irrigation plan index
def _validate_irrigation_plan_index(index: int) -> None:
    """Validate that a schedule index is supported by the firmware."""

    if not 0 <= int(index) < IRRIGATION_PLAN_MAX_COUNT:
        raise ValueError("Irrigation plan index must be between 0 and 5")

# Validate schedule repeat mode
def _repeat_to_loop_info(repeat_mode: int, repeat_value: int) -> tuple[int, int]:
    """Convert simplified repeat settings to firmware loop fields."""

    repeat_mode = int(repeat_mode)
    repeat_value = int(repeat_value)
    if repeat_mode == IrrigationPlanRepeat.Odd_Day:     # Odd day cycle
        return IrrigationLoopType.Odd_Day, 0
    if repeat_mode == IrrigationPlanRepeat.Even_Day:    # Even day cycle
        return IrrigationLoopType.Even_Day, 0
    if repeat_mode == IrrigationPlanRepeat.Interval:    # Interval cycle, repeat_value is interval days (1..30)
        if not 1 <= repeat_value <= 30:
            raise ValueError("Irrigation plan interval must be between 1 and 30 days")
        return IrrigationLoopType.Days, repeat_value
    if repeat_mode == IrrigationPlanRepeat.Custom:      # Custom cycle, repeat_value is weekday mask (0..127, bit0=Sun, bit1=Mon..bit6=Sat)
        if not 0 <= repeat_value <= 0x7F:
            raise ValueError("Irrigation plan custom weekday mask must be 0..127")
        return IrrigationLoopType.Week, repeat_value
    raise ValueError("Unsupported irrigation plan repeat mode")

# Seconds elapsed since midnight
def _seconds_from_midnight(hour: int, minute: int) -> int:
    """Return elapsed seconds from midnight for the current day."""

    return int(hour) * 3600 + int(minute) * 60

# Convert YMD to Zigbee epoch timestamp (seconds)
def _zigbee_date_timestamp(year: int, month: int, day: int) -> int:
    """Return the Zigbee epoch timestamp for a date at midnight UTC."""

    return int(
        datetime(int(year), int(month), int(day), tzinfo=timezone.utc).timestamp()
        - ZIGBEE_EPOCH_OFFSET
    )

# Return current UTC time as Zigbee epoch timestamp (seconds)
def _zigbee_now_timestamp() -> int:
    """Return the current UTC timestamp using the Zigbee epoch."""

    return int(datetime.now(tz=timezone.utc).timestamp() - ZIGBEE_EPOCH_OFFSET)


# Convert Zigbee epoch timestamp to year/month/day tuple
def _zigbee_timestamp_to_ymd(value: int) -> tuple[int, int, int]:
    """Convert a Zigbee epoch timestamp to year/month/day."""

    dt = datetime.fromtimestamp(int(value) + ZIGBEE_EPOCH_OFFSET, tz=timezone.utc)
    return dt.year, dt.month, dt.day

# Encode irrigation plan payload to Zigbee protocol byte format
def encode_irrigation_plan_payload(plan: IrrigationPlan) -> bytes:
    """Encode a Zigbee auto irrigation plan command payload."""

    _validate_irrigation_plan_index(plan.index)  # Validate plan index
    loop_type, loop_option = _repeat_to_loop_info(plan.repeat_mode, plan.repeat_value)  # Validate schedule repeat mode

    payload: list[int] = [
        int(plan.index),
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

# Unpack ZCL array to single irrigation payload
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

# Decode Sonoff irrigation data
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

# Encode irrigation state object to byte payload
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

# Valve abnormal state bitmap
class ValveState(t.enum8):
    """Water valve abnormal state bitmap."""
    # Basic states (single bit)
    Normal = 0                    # 000 (no abnormal state)
    Water_Shortage = 1 << 0       # 001 (bit0: water shortage)
    Water_Leakage = 1 << 1        # 010 (bit1: water leakage)
    Anti_Frost_Alarm = 1 << 2     # 100 (bit2: anti-frost alarm)
    Water_Shortage_Channel_2 = 1 << 4  # bit4: channel 2 water shortage
    # Combined states (multi-bit)
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
        # Irrigation plan set command
        irrigation_plan_set = foundation.ZCLCommandDef(
            id=IRRIGATION_PLAN_SET_COMMAND_ID,
            schema={"payload": IrrigationPlanPayload},
            is_manufacturer_specific=False,
        )
        # Irrigation plan remove command
        irrigation_plan_remove = foundation.ZCLCommandDef(
            id=IRRIGATION_PLAN_REMOVE_COMMAND_ID,
            schema={"index": t.uint8_t},
            is_manufacturer_specific=False,
        )
        # User delay set command (rain delay) — 4-byte big-endian Zigbee epoch
        user_delay_set = foundation.ZCLCommandDef(
            id=MANUAL_RAIN_DELAY_CONTROL,
            schema={"delay_end_timestamp": DelayTimestampPayload},
            is_manufacturer_specific=False,
        )

    class AttributeDefs(BaseAttributeDefs):
        """SONOFF private attribute definitions."""

        # Child lock state attribute definition
        child_lock = ZCLAttributeDef(
            id=0x0000,
            type=t.Bool,
            manufacturer_code=None,
        )
        # Realtime irrigation duration attribute definition
        realtime_irrigation_duration = ZCLAttributeDef(
            id=0x5006,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Realtime irrigation volume attribute definition
        realtime_irrigation_volume = ZCLAttributeDef(
            id=0x5007,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Valve abnormal state attribute definition
        valve_abnormal_state = ZCLAttributeDef(
            id=0x500C,
            type=ValveState,
            manufacturer_code=None,
        )
        # Daily irrigation volume attribute definition
        daily_irrigation_volume = ZCLAttributeDef(
            id=0x500F,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # User delay end datetime attribute definition
        user_delay_end_datetime = ZCLAttributeDef(
            id=0x5014,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Weather delay duration attribute definition
        weather_delay_duration = ZCLAttributeDef(
            id=0x5019,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Daily irrigation duration attribute definition
        daily_irrigation_duration = ZCLAttributeDef(
            id=0x501A,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Hourly irrigation volume attribute definition
        hour_irrigation_volume = ZCLAttributeDef(
            id=0x501B,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Hourly irrigation duration attribute definition
        hour_irrigation_duration = ZCLAttributeDef(
            id=0x501C,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Single irrigation setting attribute definition
        single_irrigation_set = ZCLAttributeDef(
            id=0x501D,
            type=SingleIrrigationPayload,
            manufacturer_code=None,
        )
        # Water flow unit attribute definition
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

    # Handle single irrigation attribute changes
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
            # Sync amount unit to the standalone global cluster (endpoint 1)
            target = self.endpoint
            if not hasattr(target, "sonoff_amount_unit_config"):
                target = self.endpoint.device.endpoints.get(1)
            if target is not None and hasattr(target, "sonoff_amount_unit_config"):
                target.sonoff_amount_unit_config.update_amount_unit(
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
                self._single_irrigation_state = decode_single_irrigation_payload(value)  # Parse single irrigation setting attribute value
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
                self.AttributeDefs.user_delay_end_datetime.id,
            ]
        )

#****************************** Amount unit (global) start *****************************************************

class SonoffAmountUnitConfigCluster(LocalDataCluster):
    """Global cluster for irrigation amount unit, shared by manual irrigation and schedules."""

    cluster_id = 0xFBF9
    ep_attribute = "sonoff_amount_unit_config"

    class AttributeDefs(BaseAttributeDefs):
        """Amount unit attribute."""

        amount_unit: Final = ZCLAttributeDef(id=0x0070, type=IrrigationAmountUnit)

    def __init__(self, *args, **kwargs):
        """Initialize with default unit and sync from device."""
        super().__init__(*args, **kwargs)
        self._amount_unit = IrrigationAmountUnit.Liter
        self._update_attribute(self.AttributeDefs.amount_unit.id, self._amount_unit)

    def update_amount_unit(self, unit: int) -> None:
        """Update local amount unit from the real 0x5021 attribute."""
        self._amount_unit = int(unit)
        self._update_attribute(self.AttributeDefs.amount_unit.id, self._amount_unit)

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """When user changes the amount unit, write the aggregate 0x501D."""
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.amount_unit.id:
                self._amount_unit = int(value)
                self._update_attribute(attr_id, self._amount_unit)

        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]


#****************************** Manual irrigation entities start *****************************************************

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
        amount: Final = ZCLAttributeDef(id=0x0013, type=t.uint16_t)
        fail_safe_duration_min: Final = ZCLAttributeDef(id=0x0014, type=t.uint16_t)

    def __init__(self, *args, **kwargs):
        """Initialize with conservative single irrigation defaults."""

        super().__init__(*args, **kwargs)
        self._single_irrigation_state = SingleIrrigationState()
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
            self.AttributeDefs.amount.id,
            self._single_irrigation_state.amount,
        )
        self._update_attribute(
            self.AttributeDefs.fail_safe_duration_min.id,
            self._single_irrigation_state.fail_safe_duration_min,
        )

    # Called by parser when device reports, updates entities for HA
    def update_single_irrigation_state(self, state: SingleIrrigationState) -> None:
        """Update local attributes from decoded single irrigation state."""

        self._single_irrigation_state = SingleIrrigationState(
            irrigation_mode=state.irrigation_mode,
            total_duration_min=state.total_duration_min,
            amount_unit=state.amount_unit,
            amount=self._single_irrigation_state.amount,
            fail_safe_duration_min=self._single_irrigation_state.fail_safe_duration_min,
        )
        # Sync amount unit to the standalone global cluster
        if hasattr(self.endpoint, "sonoff_amount_unit_config"):
            self.endpoint.sonoff_amount_unit_config.update_amount_unit(
                int(state.amount_unit)
            )
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
            self.AttributeDefs.amount.id: self._single_irrigation_state.amount,
            self.AttributeDefs.fail_safe_duration_min.id: self._single_irrigation_state.fail_safe_duration_min,
        }
        for attr_id, value in updates.items():
            self._update_attribute(attr_id, value)

    # Update amount unit (delegate to global amount unit cluster)
    def update_amount_unit(self, unit: int) -> None:
        """Delegate to the global amount unit cluster."""
        if hasattr(self.endpoint, "sonoff_amount_unit_config"):
            self.endpoint.sonoff_amount_unit_config.update_amount_unit(unit)



    # Write manual irrigation attributes to zigbee
    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """Merge local config writes into the real aggregate attribute."""

        # Read amount_unit from the shared global cluster
        global_unit = IrrigationAmountUnit.Liter
        if hasattr(self.endpoint, "sonoff_amount_unit_config"):
            global_unit = int(self.endpoint.sonoff_amount_unit_config._amount_unit)

        state = SingleIrrigationState(
            irrigation_mode=self._single_irrigation_state.irrigation_mode,
            total_duration_min=self._single_irrigation_state.total_duration_min,
            amount_unit=global_unit,
            amount=self._single_irrigation_state.amount,
            fail_safe_duration_min=self._single_irrigation_state.fail_safe_duration_min,
        )
        pending_mode = state.irrigation_mode

        # Filter out attributes not writable in current mode (filter rather than raise to avoid breaking HA call chain)
        filtered_attributes: dict[str | int | ZCLAttributeDef, Any] = {}
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                pending_mode = int(value)
            if (
                pending_mode == SingleIrrigationMode.Duration
                and attr_id
                in (
                    self.AttributeDefs.amount.id,
                    self.AttributeDefs.fail_safe_duration_min.id,
                )
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in volume mode "
                    "(device is in duration mode)",
                    attr_def.name, value,
                )
                continue
            if (
                pending_mode == SingleIrrigationMode.Volume
                and attr_id == self.AttributeDefs.total_duration_min.id
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in duration mode "
                    "(device is in volume mode)",
                    attr_def.name, value,
                )
                continue
            filtered_attributes[attr] = value
        # All filtered, return success to avoid empty write
        if not filtered_attributes:
            return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]
        attributes = filtered_attributes

        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                state.irrigation_mode = int(value)
            elif attr_id == self.AttributeDefs.total_duration_min.id:
                state.total_duration_min = int(value)
            elif attr_id == self.AttributeDefs.amount.id:
                state.amount = int(value)
            elif attr_id == self.AttributeDefs.fail_safe_duration_min.id:
                state.fail_safe_duration_min = int(value)

        attr_ids = {self.find_attribute(attr).id for attr in attributes}
        config_result = None

        if attr_ids.intersection(
            {
                self.AttributeDefs.irrigation_mode.id,
                self.AttributeDefs.total_duration_min.id,
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
            # Only update local state on successful write, otherwise keep original to stay consistent with device
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
                self.AttributeDefs.amount.id,
                self._single_irrigation_state.amount,
            )
            self._update_attribute(
                self.AttributeDefs.fail_safe_duration_min.id,
                self._single_irrigation_state.fail_safe_duration_min,
            )
        return config_result if config_result is not None else [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]

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

#****************************** Schedule plan start *****************************************************
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
        irrigation_mode: Final = ZCLAttributeDef(id=0x0032, type=SingleIrrigationMode)
        total_duration_min: Final = ZCLAttributeDef(id=0x0033, type=t.uint16_t)
        amount: Final = ZCLAttributeDef(id=0x0034, type=t.uint16_t)
        fail_safe_duration_min: Final = ZCLAttributeDef(id=0x0035, type=t.uint16_t)
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
        duration_min: Final = ZCLAttributeDef(id=0x0036, type=t.uint16_t)
        interval_duration_min: Final = ZCLAttributeDef(id=0x0037, type=t.uint16_t)

    # Initialize attribute values
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
        self._weekday_mask = 0
        self._start_hour = 8
        self._start_minute = 0
        self._irrigation_mode = SingleIrrigationMode.Duration
        self._total_duration_min = SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN
        self._duration_min = 0
        self._interval_duration_min = 0
        self._amount = SINGLE_IRRIGATION_DEFAULT_AMOUNT
        self._fail_safe_duration_min = SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN
        self._update_all_attributes()
        self._ui_date_year, self._ui_date_month, self._ui_date_day = now.year, now.month, now.day

    # Sync local irrigation plan data to entity attributes
    def _update_all_attributes(self) -> None:
        """Mirror the local plan into entity attributes."""
        updates = {
            self.AttributeDefs.plan_index.id: self._plan_index,
            self.AttributeDefs.effective_year.id: self._effective_year,
            self.AttributeDefs.effective_month.id: self._effective_month,
            self.AttributeDefs.effective_day.id: self._effective_day,
            self.AttributeDefs.repeat_mode.id: self._repeat_mode,
            self.AttributeDefs.repeat_value.id: self._repeat_value,
            self.AttributeDefs.irrigation_mode.id: self._irrigation_mode,
            self.AttributeDefs.total_duration_min.id: self._total_duration_min,
            self.AttributeDefs.duration_min.id: self._duration_min,
            self.AttributeDefs.interval_duration_min.id: self._interval_duration_min,
            self.AttributeDefs.amount.id: self._amount,
            self.AttributeDefs.fail_safe_duration_min.id: self._fail_safe_duration_min,
            self.AttributeDefs.weekday_monday.id: int(bool(self._weekday_mask & 0x02)),
            self.AttributeDefs.weekday_tuesday.id: int(bool(self._weekday_mask & 0x04)),
            self.AttributeDefs.weekday_wednesday.id: int(bool(self._weekday_mask & 0x08)),
            self.AttributeDefs.weekday_thursday.id: int(bool(self._weekday_mask & 0x10)),
            self.AttributeDefs.weekday_friday.id: int(bool(self._weekday_mask & 0x20)),
            self.AttributeDefs.weekday_saturday.id: int(bool(self._weekday_mask & 0x40)),
            self.AttributeDefs.weekday_sunday.id: int(bool(self._weekday_mask & 0x01)),
            self.AttributeDefs.start_hour.id: self._start_hour,
            self.AttributeDefs.start_minute.id: self._start_minute,
        }
        for attr_id, value in updates.items():
            self._update_attribute(attr_id, value)

    # Build irrigation plan (used when creating schedule, not needed for removal)
    def _plan_from_current_config(self) -> IrrigationPlan:
        """Build a firmware plan from simple schedule fields and irrigation config."""
        _validate_irrigation_plan_index(self._plan_index)
        # Enable date
        enable_datetime = _zigbee_date_timestamp(
            self._effective_year, self._effective_month, self._effective_day
        )
        # Start time
        start_datetime = _seconds_from_midnight(self._start_hour, self._start_minute)

        # Repeat strategy
        repeat_value = self._repeat_value
        if self._repeat_mode == IrrigationPlanRepeat.Custom:
            repeat_value = self._weekday_mask

        # Get unit from global amount unit cluster
        amount_unit = IrrigationAmountUnit.Liter
        if hasattr(self.endpoint, "sonoff_amount_unit_config"):
            amount_unit = int(self.endpoint.sonoff_amount_unit_config._amount_unit)

        # Zero-out fields not applicable in the current irrigation mode
        plan_total_duration = self._total_duration_min
        plan_duration = self._duration_min
        plan_interval_duration = self._interval_duration_min
        plan_amount = self._amount
        plan_fail_safe = self._fail_safe_duration_min
        if self._irrigation_mode == SingleIrrigationMode.Volume:
            plan_total_duration = 0
            plan_duration = 0
            plan_interval_duration = 0
        elif self._irrigation_mode in (SingleIrrigationMode.Duration, SingleIrrigationMode.Duration_With_Interval):
            plan_amount = 0
            plan_fail_safe = 0
            if self._irrigation_mode == SingleIrrigationMode.Duration:
                plan_duration = 0
                plan_interval_duration = 0

        return IrrigationPlan(
            index=self._plan_index,
            enabled=1,
            enable_datetime=enable_datetime,
            irrigation_mode=self._irrigation_mode,
            start_datetime=start_datetime,
            total_duration_min=plan_total_duration,
            duration_min=plan_duration,
            interval_duration_min=plan_interval_duration,
            amount_unit=amount_unit,
            amount=plan_amount,
            fail_safe_duration_min=plan_fail_safe,
            create_datetime=_zigbee_now_timestamp(),
            repeat_mode=self._repeat_mode,
            repeat_value=repeat_value,
        )

    # HA entity modifications are cached locally; only sent to Zigbee device when apply/remove button is clicked
    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs,
    ) -> list:
        """Update local plan fields or trigger set/remove actions."""
        # Filter out attributes not writable in current mode (consistent with single irrigation config)
        pending_mode = self._irrigation_mode
        filtered_attributes: dict[str | int | ZCLAttributeDef, Any] = {}
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                pending_mode = int(value)
            if (
                pending_mode
                in (SingleIrrigationMode.Duration, SingleIrrigationMode.Duration_With_Interval)
                and attr_id
                in (
                    self.AttributeDefs.amount.id,
                    self.AttributeDefs.fail_safe_duration_min.id,
                )
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in volume mode "
                    "(device is in %s mode)",
                    attr_def.name, value, pending_mode.name,
                )
                continue
            if (
                pending_mode == SingleIrrigationMode.Volume
                and attr_id == self.AttributeDefs.total_duration_min.id
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in duration mode "
                    "(device is in volume mode)",
                    attr_def.name, value,
                )
                continue
            if (
                pending_mode != SingleIrrigationMode.Duration_With_Interval
                and attr_id
                in (
                    self.AttributeDefs.duration_min.id,
                    self.AttributeDefs.interval_duration_min.id,
                )
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in duration-with-interval mode",
                    attr_def.name, value,
                )
                continue
            filtered_attributes[attr] = value
        if not filtered_attributes:
            return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]
        attributes = filtered_attributes

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
            elif attr_id == self.AttributeDefs.irrigation_mode.id:
                self._irrigation_mode = int(value)
            elif attr_id == self.AttributeDefs.total_duration_min.id:
                self._total_duration_min = int(value)
            elif attr_id == self.AttributeDefs.duration_min.id:
                self._duration_min = int(value)
            elif attr_id == self.AttributeDefs.interval_duration_min.id:
                self._interval_duration_min = int(value)
            elif attr_id == self.AttributeDefs.amount.id:
                self._amount = int(value)
            elif attr_id == self.AttributeDefs.fail_safe_duration_min.id:
                self._fail_safe_duration_min = int(value)
            elif attr_id == self.AttributeDefs.weekday_monday.id:
                self._weekday_mask = (self._weekday_mask & ~0x02) | (int(bool(value)) << 1)
            elif attr_id == self.AttributeDefs.weekday_tuesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x04) | (int(bool(value)) << 2)
            elif attr_id == self.AttributeDefs.weekday_wednesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x08) | (int(bool(value)) << 3)
            elif attr_id == self.AttributeDefs.weekday_thursday.id:
                self._weekday_mask = (self._weekday_mask & ~0x10) | (int(bool(value)) << 4)
            elif attr_id == self.AttributeDefs.weekday_friday.id:
                self._weekday_mask = (self._weekday_mask & ~0x20) | (int(bool(value)) << 5)
            elif attr_id == self.AttributeDefs.weekday_saturday.id:
                self._weekday_mask = (self._weekday_mask & ~0x40) | (int(bool(value)) << 6)
            elif attr_id == self.AttributeDefs.weekday_sunday.id:
                self._weekday_mask = (self._weekday_mask & ~0x01) | int(bool(value))
            elif attr_id == self.AttributeDefs.start_hour.id:
                self._start_hour = int(value)
            elif attr_id == self.AttributeDefs.start_minute.id:
                self._start_minute = int(value)
            elif attr_id == self.AttributeDefs.apply_plan.id:   # Set plan
                plan = self._plan_from_current_config()
                payload = encode_irrigation_plan_payload(plan)
                # Wrap payload in ZCL command and send to Zigbee device
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_SET_COMMAND_ID,
                    payload=IrrigationPlanPayload(payload),
                    manufacturer=None,
                    expect_reply=False,
                )
            elif attr_id == self.AttributeDefs.remove_plan.id:  # Remove plan
                _validate_irrigation_plan_index(self._plan_index)
                # Wrap payload in ZCL command and send to Zigbee device
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_REMOVE_COMMAND_ID,
                    index=t.uint8_t(self._plan_index),
                    manufacturer=None,
                    expect_reply=False,
                )

        self._update_all_attributes()
        if result:
            return result
        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]

#****************************** Channel 2 schedule plan start *****************************************************
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
        irrigation_mode: Final = ZCLAttributeDef(id=0x0052, type=SingleIrrigationMode)
        total_duration_min: Final = ZCLAttributeDef(id=0x0053, type=t.uint16_t)
        amount: Final = ZCLAttributeDef(id=0x0054, type=t.uint16_t)
        fail_safe_duration_min: Final = ZCLAttributeDef(id=0x0055, type=t.uint16_t)
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
        duration_min: Final = ZCLAttributeDef(id=0x0056, type=t.uint16_t)
        interval_duration_min: Final = ZCLAttributeDef(id=0x0057, type=t.uint16_t)

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
        self._weekday_mask = 0
        self._start_hour = 8
        self._start_minute = 0
        self._irrigation_mode = SingleIrrigationMode.Duration
        self._total_duration_min = SINGLE_IRRIGATION_DEFAULT_TOTAL_DURATION_MIN
        self._duration_min = 0
        self._interval_duration_min = 0
        self._amount = SINGLE_IRRIGATION_DEFAULT_AMOUNT
        self._fail_safe_duration_min = SINGLE_IRRIGATION_DEFAULT_FAIL_SAFE_DURATION_MIN
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
            self.AttributeDefs.irrigation_mode.id: self._irrigation_mode,
            self.AttributeDefs.total_duration_min.id: self._total_duration_min,
            self.AttributeDefs.duration_min.id: self._duration_min,
            self.AttributeDefs.interval_duration_min.id: self._interval_duration_min,
            self.AttributeDefs.amount.id: self._amount,
            self.AttributeDefs.fail_safe_duration_min.id: self._fail_safe_duration_min,
            self.AttributeDefs.weekday_monday.id: int(bool(self._weekday_mask & 0x02)),
            self.AttributeDefs.weekday_tuesday.id: int(bool(self._weekday_mask & 0x04)),
            self.AttributeDefs.weekday_wednesday.id: int(bool(self._weekday_mask & 0x08)),
            self.AttributeDefs.weekday_thursday.id: int(bool(self._weekday_mask & 0x10)),
            self.AttributeDefs.weekday_friday.id: int(bool(self._weekday_mask & 0x20)),
            self.AttributeDefs.weekday_saturday.id: int(bool(self._weekday_mask & 0x40)),
            self.AttributeDefs.weekday_sunday.id: int(bool(self._weekday_mask & 0x01)),
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

        repeat_value = self._repeat_value
        if self._repeat_mode == IrrigationPlanRepeat.Custom:
            repeat_value = self._weekday_mask

        # Get unit from global amount unit cluster (endpoint 1)
        amount_unit = IrrigationAmountUnit.Liter
        endpoint_1 = self.endpoint.device.endpoints.get(1)
        if endpoint_1 is not None and hasattr(endpoint_1, "sonoff_amount_unit_config"):
            amount_unit = int(endpoint_1.sonoff_amount_unit_config._amount_unit)

        # Zero-out fields not applicable in the current irrigation mode
        plan_total_duration = self._total_duration_min
        plan_duration = self._duration_min
        plan_interval_duration = self._interval_duration_min
        plan_amount = self._amount
        plan_fail_safe = self._fail_safe_duration_min
        if self._irrigation_mode == SingleIrrigationMode.Volume:
            plan_total_duration = 0
            plan_duration = 0
            plan_interval_duration = 0
        elif self._irrigation_mode in (SingleIrrigationMode.Duration, SingleIrrigationMode.Duration_With_Interval):
            plan_amount = 0
            plan_fail_safe = 0
            if self._irrigation_mode == SingleIrrigationMode.Duration:
                plan_duration = 0
                plan_interval_duration = 0

        return IrrigationPlan(
            index=self._plan_index,
            enabled=1,
            enable_datetime=enable_datetime,
            irrigation_mode=self._irrigation_mode,
            start_datetime=start_datetime,
            total_duration_min=plan_total_duration,
            duration_min=plan_duration,
            interval_duration_min=plan_interval_duration,
            amount_unit=amount_unit,
            amount=plan_amount,
            fail_safe_duration_min=plan_fail_safe,
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
        # Filter out attributes not writable in current mode (consistent with single irrigation config)
        pending_mode = self._irrigation_mode
        filtered_attributes: dict[str | int | ZCLAttributeDef, Any] = {}
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.irrigation_mode.id:
                pending_mode = int(value)
            if (
                pending_mode
                in (SingleIrrigationMode.Duration, SingleIrrigationMode.Duration_With_Interval)
                and attr_id
                in (
                    self.AttributeDefs.amount.id,
                    self.AttributeDefs.fail_safe_duration_min.id,
                )
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in volume mode "
                    "(device is in %s mode)",
                    attr_def.name, value, pending_mode.name,
                )
                continue
            if (
                pending_mode == SingleIrrigationMode.Volume
                and attr_id == self.AttributeDefs.total_duration_min.id
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in duration mode "
                    "(device is in volume mode)",
                    attr_def.name, value,
                )
                continue
            if (
                pending_mode != SingleIrrigationMode.Duration_With_Interval
                and attr_id
                in (
                    self.AttributeDefs.duration_min.id,
                    self.AttributeDefs.interval_duration_min.id,
                )
            ):
                _LOGGER.warning(
                    "Ignoring attribute %s=%s: only configurable in duration-with-interval mode",
                    attr_def.name, value,
                )
                continue
            filtered_attributes[attr] = value
        if not filtered_attributes:
            return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]
        attributes = filtered_attributes

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
            elif attr_id == self.AttributeDefs.irrigation_mode.id:
                self._irrigation_mode = int(value)
            elif attr_id == self.AttributeDefs.total_duration_min.id:
                self._total_duration_min = int(value)
            elif attr_id == self.AttributeDefs.duration_min.id:
                self._duration_min = int(value)
            elif attr_id == self.AttributeDefs.interval_duration_min.id:
                self._interval_duration_min = int(value)
            elif attr_id == self.AttributeDefs.amount.id:
                self._amount = int(value)
            elif attr_id == self.AttributeDefs.fail_safe_duration_min.id:
                self._fail_safe_duration_min = int(value)
            elif attr_id == self.AttributeDefs.weekday_monday.id:
                self._weekday_mask = (self._weekday_mask & ~0x02) | (int(bool(value)) << 1)
            elif attr_id == self.AttributeDefs.weekday_tuesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x04) | (int(bool(value)) << 2)
            elif attr_id == self.AttributeDefs.weekday_wednesday.id:
                self._weekday_mask = (self._weekday_mask & ~0x08) | (int(bool(value)) << 3)
            elif attr_id == self.AttributeDefs.weekday_thursday.id:
                self._weekday_mask = (self._weekday_mask & ~0x10) | (int(bool(value)) << 4)
            elif attr_id == self.AttributeDefs.weekday_friday.id:
                self._weekday_mask = (self._weekday_mask & ~0x20) | (int(bool(value)) << 5)
            elif attr_id == self.AttributeDefs.weekday_saturday.id:
                self._weekday_mask = (self._weekday_mask & ~0x40) | (int(bool(value)) << 6)
            elif attr_id == self.AttributeDefs.weekday_sunday.id:
                self._weekday_mask = (self._weekday_mask & ~0x01) | int(bool(value))
            elif attr_id == self.AttributeDefs.start_hour.id:
                self._start_hour = int(value)
            elif attr_id == self.AttributeDefs.start_minute.id:
                self._start_minute = int(value)
            elif attr_id == self.AttributeDefs.apply_plan.id:
                plan = self._plan_from_current_config()
                payload = encode_irrigation_plan_payload(plan)
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_SET_COMMAND_ID,
                    payload=IrrigationPlanPayload(payload),
                    manufacturer=None,
                    expect_reply=False,
                )
            elif attr_id == self.AttributeDefs.remove_plan.id:
                _validate_irrigation_plan_index(self._plan_index)
                result = await self.endpoint.sonoff_cluster.command(
                    IRRIGATION_PLAN_REMOVE_COMMAND_ID,
                    index=t.uint8_t(self._plan_index),
                    manufacturer=None,
                    expect_reply=False,
                )

        self._update_all_attributes()
        if result:
            return result
        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]

#****************************** User delay (rain delay) start *****************************************************

class SonoffUserDelayConfigCluster(LocalDataCluster):
    """Local cluster for manual rain / user delay configuration.

    Sends command 0x08 on the SONOFF private cluster (0xFC11) which triggers
    ``rtcScheduleIrrigationTaskSkip()`` in the firmware — marking all irrigation
    tasks whose start_time falls between now and the delay end timestamp as
    skipped.  A value of 0 clears the delay.
    """

    cluster_id = 0xFBFC
    ep_attribute = "sonoff_user_delay_config"

    class AttributeDefs(BaseAttributeDefs):
        """Local user-delay configuration attributes."""

        delay_hours: Final = ZCLAttributeDef(id=0x0060, type=t.uint8_t)
        apply_delay: Final = ZCLAttributeDef(id=0x0061, type=t.uint8_t)
        clear_delay: Final = ZCLAttributeDef(id=0x0062, type=t.uint8_t)

    def __init__(self, *args, **kwargs):
        """Initialise with sensible defaults and listen for firmware reports."""
        super().__init__(*args, **kwargs)
        self._delay_hours: int = 24
        self._update_attribute(self.AttributeDefs.delay_hours.id, self._delay_hours)

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **_kwargs: Any,
    ) -> list:
        """Handle local writes: cache delay_hours, trigger apply/clear via
        command 0x08 on the real SONOFF cluster."""

        result = []
        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            attr_id = attr_def.id
            if attr_id == self.AttributeDefs.delay_hours.id:
                self._delay_hours = int(value)
                self._update_attribute(attr_id, self._delay_hours)
            elif attr_id == self.AttributeDefs.apply_delay.id:
                now_zigbee = _zigbee_now_timestamp()
                end_timestamp = now_zigbee + self._delay_hours * 3600
                result = await self.endpoint.sonoff_cluster.command(
                    MANUAL_RAIN_DELAY_CONTROL,
                    delay_end_timestamp=DelayTimestampPayload(_put_u32_be(end_timestamp)),
                    manufacturer=None,
                    expect_reply=False,
                )
            elif attr_id == self.AttributeDefs.clear_delay.id:
                # Send 0 to clear the delay — firmware treats this as
                # "no tasks in range" which clears all is_delay flags.
                result = await self.endpoint.sonoff_cluster.command(
                    MANUAL_RAIN_DELAY_CONTROL,
                    delay_end_timestamp=DelayTimestampPayload(_put_u32_be(0)),
                    manufacturer=None,
                    expect_reply=False,
                )

        if result:
            return result
        return [[foundation.WriteAttributesStatusRecord(status=Status.SUCCESS)]]


#****************************** Amount unit entity (global) start *****************************************************

def add_amount_unit_config_entity(builder: QuirkBuilder) -> QuirkBuilder:
    """Add the global amount unit config entity, shared by manual and schedules."""

    return (
        builder.adds(SonoffAmountUnitConfigCluster)
        .enum(
            SonoffAmountUnitConfigCluster.AttributeDefs.amount_unit.name,
            IrrigationAmountUnit,
            SonoffAmountUnitConfigCluster.cluster_id,
            translation_key="irrigation_amount_unit",
            fallback_name="Global amount unit",
        )
    )


#****************************** Manual irrigation entities start *****************************************************
# Split aggregate array into individual entities so HA can display decoded values
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
            fallback_name="Manual 01 irrigation mode",
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
            fallback_name="Manual 02 irrigation total duration",
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
            fallback_name="Manual 03 irrigation amount",
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
            fallback_name="Manual 04 fail safe duration",
        )
    )

# Channel 1 schedule entities
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
            fallback_name="CH1 Schedule 02 plan index",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_year.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=2000,
            max_value=2099,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_year",
            fallback_name="CH1 Schedule 03 effective year",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_month.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=1,
            max_value=12,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_month",
            fallback_name="CH1 Schedule 04 effective month",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.effective_day.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=1,
            max_value=31,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_effective_day",
            fallback_name="CH1 Schedule 05 effective day",
        )
        # Start time: hour
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.start_hour.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=23,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_start_hour",
            fallback_name="CH1 Schedule 06 start hour",
        )
        # Start time: minute
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.start_minute.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=59,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_start_minute",
            fallback_name="CH1 Schedule 07 start minute",
        )
        # Repeat mode
        .enum(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.repeat_mode.name,
            IrrigationPlanRepeat,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_repeat_mode",
            fallback_name="CH1 Schedule 01 repeat mode",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.repeat_value.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=0,
            max_value=30,
            step=1,
            mode="box",
            translation_key="schedule_ch1_irrigation_plan_repeat_value",
            fallback_name="CH1 Schedule 08 repeat value",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_monday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_monday",
            fallback_name="CH1 Schedule 09 Monday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_tuesday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_tuesday",
            fallback_name="CH1 Schedule 10 Tuesday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_wednesday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_wednesday",
            fallback_name="CH1 Schedule 11 Wednesday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_thursday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_thursday",
            fallback_name="CH1 Schedule 12 Thursday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_friday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_friday",
            fallback_name="CH1 Schedule 13 Friday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_saturday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_saturday",
            fallback_name="CH1 Schedule 14 Saturday",
        )
        .switch(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.weekday_sunday.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch1_irrigation_plan_sunday",
            fallback_name="CH1 Schedule 15 Sunday",
        )
        .enum(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.irrigation_mode.name,
            SingleIrrigationMode,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_mode",
            fallback_name="CH1 Schedule 16 irrigation mode",
            unique_id_suffix="schedule_ch1_irrigation_mode",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.total_duration_min.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch1_total_duration",
            fallback_name="CH1 Schedule 17 total duration",
            unique_id_suffix="schedule_ch1_total_duration",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.amount.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_AMOUNT_MIN,
            max_value=SINGLE_IRRIGATION_AMOUNT_MAX,
            step=1,
            mode="box",
            translation_key="schedule_ch1_amount",
            fallback_name="CH1 Schedule 18 amount",
            unique_id_suffix="schedule_ch1_amount",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.fail_safe_duration_min.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_FAIL_SAFE_MIN,
            max_value=SINGLE_IRRIGATION_FAIL_SAFE_MAX,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch1_fail_safe_duration",
            fallback_name="CH1 Schedule 19 fail safe duration",
            unique_id_suffix="schedule_ch1_fail_safe_duration",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.duration_min.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch1_irrigation_duration",
            fallback_name="CH1 Schedule 20 irrigation duration",
            unique_id_suffix="schedule_ch1_irrigation_duration",
        )
        .number(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.interval_duration_min.name,
            SonoffIrrigationPlanConfigCluster.cluster_id,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch1_irrigation_interval_duration",
            fallback_name="CH1 Schedule 21 irrigation interval duration",
            unique_id_suffix="schedule_ch1_irrigation_interval_duration",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.apply_plan.name,
            SonoffIrrigationPlanConfigCluster.AttributeDefs.apply_plan.id,
            cluster_id=SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_set",
            fallback_name="CH1 Schedule 22 apply plan",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigCluster.AttributeDefs.remove_plan.name,
            SonoffIrrigationPlanConfigCluster.AttributeDefs.remove_plan.id,
            cluster_id=SonoffIrrigationPlanConfigCluster.cluster_id,
            translation_key="schedule_ch1_irrigation_plan_remove",
            fallback_name="CH1 Schedule 23 remove plan",
        )
    )

# Channel 2 schedule entities
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
            fallback_name="CH2 Schedule 02 plan index",
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
            fallback_name="CH2 Schedule 03 effective year",
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
            fallback_name="CH2 Schedule 04 effective month",
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
            fallback_name="CH2 Schedule 05 effective day",
        )

        # Repeat mode
        .enum(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.repeat_mode.name,
            IrrigationPlanRepeat,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            translation_key="schedule_ch2_irrigation_plan_repeat_mode",
            fallback_name="CH2 Schedule 01 repeat mode",
        )
        # Start time: hour
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.start_hour.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=23,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_start_hour",
            fallback_name="CH2 Schedule 06 start hour",
        )
        # Start time: minute
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.start_minute.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=0,
            max_value=59,
            step=1,
            mode="box",
            translation_key="schedule_ch2_irrigation_plan_start_minute",
            fallback_name="CH2 Schedule 07 start minute",
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
            fallback_name="CH2 Schedule 08 repeat value",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_monday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_monday",
            fallback_name="CH2 Schedule 09 Monday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_tuesday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_tuesday",
            fallback_name="CH2 Schedule 10 Tuesday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_wednesday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_wednesday",
            fallback_name="CH2 Schedule 11 Wednesday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_thursday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_thursday",
            fallback_name="CH2 Schedule 12 Thursday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_friday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_friday",
            fallback_name="CH2 Schedule 13 Friday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_saturday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_saturday",
            fallback_name="CH2 Schedule 14 Saturday",
        )
        .switch(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.weekday_sunday.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            off_value=0,
            on_value=1,
            translation_key="schedule_ch2_irrigation_plan_sunday",
            fallback_name="CH2 Schedule 15 Sunday",
        )
        # Schedule 2 independent irrigation config
        .enum(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.irrigation_mode.name,
            SingleIrrigationMode,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            translation_key="schedule_ch2_irrigation_mode",
            fallback_name="CH2 Schedule 16 irrigation mode",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.total_duration_min.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch2_total_duration",
            fallback_name="CH2 Schedule 17 total duration",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.duration_min.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch2_irrigation_duration",
            fallback_name="CH2 Schedule 20 irrigation duration",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.interval_duration_min.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_DURATION_MIN_MIN,
            max_value=SINGLE_IRRIGATION_DURATION_MAX_MIN,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch2_irrigation_interval_duration",
            fallback_name="CH2 Schedule 21 irrigation interval duration",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.amount.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_AMOUNT_MIN,
            max_value=SINGLE_IRRIGATION_AMOUNT_MAX,
            step=1,
            mode="box",
            translation_key="schedule_ch2_amount",
            fallback_name="CH2 Schedule 18 amount",
        )
        .number(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.fail_safe_duration_min.name,
            SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            endpoint_id=2,
            min_value=SINGLE_IRRIGATION_FAIL_SAFE_MIN,
            max_value=SINGLE_IRRIGATION_FAIL_SAFE_MAX,
            step=SINGLE_IRRIGATION_STEP_MIN,
            device_class=NumberDeviceClass.DURATION,
            unit=UnitOfTime.MINUTES,
            mode="box",
            translation_key="schedule_ch2_fail_safe_duration",
            fallback_name="CH2 Schedule 19 fail safe duration",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.apply_plan.name,
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.apply_plan.id,
            endpoint_id=2,
            cluster_id=SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            translation_key="schedule_ch2_irrigation_plan_set",
            fallback_name="CH2 Schedule 22 apply plan",
        )
        .write_attr_button(
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.remove_plan.name,
            SonoffIrrigationPlanConfigClusterCh2.AttributeDefs.remove_plan.id,
            endpoint_id=2,
            cluster_id=SonoffIrrigationPlanConfigClusterCh2.cluster_id,
            translation_key="schedule_ch2_irrigation_plan_remove",
            fallback_name="CH2 Schedule 23 remove plan",
        )
    )

# Add user delay (rain delay) entities
def add_user_delay_config_entities(builder: QuirkBuilder) -> QuirkBuilder:
    """Add config entities for manual rain / user delay."""

    return (
        builder.adds(SonoffUserDelayConfigCluster)
        .number(
            SonoffUserDelayConfigCluster.AttributeDefs.delay_hours.name,
            SonoffUserDelayConfigCluster.cluster_id,
            min_value=1,
            max_value=720,
            step=1,
            mode="box",
            translation_key="manual_user_delay_hours",
            fallback_name="Delay 01 delay hours",
        )
        .write_attr_button(
            SonoffUserDelayConfigCluster.AttributeDefs.apply_delay.name,
            SonoffUserDelayConfigCluster.AttributeDefs.apply_delay.id,
            cluster_id=SonoffUserDelayConfigCluster.cluster_id,
            translation_key="manual_user_delay_apply",
            fallback_name="Delay 02 apply delay",
        )
        .write_attr_button(
            SonoffUserDelayConfigCluster.AttributeDefs.clear_delay.name,
            SonoffUserDelayConfigCluster.AttributeDefs.clear_delay.id,
            cluster_id=SonoffUserDelayConfigCluster.cluster_id,
            translation_key="manual_user_delay_clear",
            fallback_name="Delay 03 clear delay",
        )
    )

# Add common entities
def add_common_entities(builder: QuirkBuilder) -> QuirkBuilder:
    """Add shared SWV private-cluster entities."""

    builder = builder.replaces(SonoffWaterValveCluster).replaces(
        SonoffWaterValveCluster, endpoint_id=2
    )
    builder = add_single_irrigation_config_entities(builder)
    builder = add_irrigation_plan_config_entities(builder)
    builder = add_irrigation_plan_config_entities_ch2(builder)
    builder = add_user_delay_config_entities(builder)
    builder = add_amount_unit_config_entity(builder)

    return (
        builder
        # Child lock
        .switch(
            SonoffWaterValveCluster.AttributeDefs.child_lock.name,
            SonoffWaterValveCluster.cluster_id,
            translation_key="child_lock",
            fallback_name="Child lock",
        )
        # User delay end time (0x5014) → converted to Unix epoch for HA display
        .sensor(
            SonoffWaterValveCluster.AttributeDefs.user_delay_end_datetime.name,
            SonoffWaterValveCluster.cluster_id,
            device_class=SensorDeviceClass.TIMESTAMP,
            state_class=SensorStateClass.MEASUREMENT,
            reporting_config=ReportingConfig(
                min_interval=30, max_interval=900, reportable_change=1
            ),
            attribute_converter=lambda v: datetime.fromtimestamp(v + ZIGBEE_EPOCH_OFFSET, tz=timezone.utc) if v != 0 else None,
            translation_key="user_delay_end_datetime",
            fallback_name="User delay end time",
        )
        # 1. Water leakage sensor (bit1)
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
        # 2. Water shortage sensor (bit0)
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
        # Realtime irrigation duration
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
        # Hourly irrigation duration
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
            fallback_name="Hourly irrigation duration",
        )
        # Hourly irrigation volume
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
            fallback_name="Hourly irrigation volume",
        )
        # Daily irrigation duration
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
        # Daily irrigation volume
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

# Model definitions
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
