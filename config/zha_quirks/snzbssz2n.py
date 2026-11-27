"""Sonoff SNZB-SSZ2N Zigbee soil temperature and moisture sensor."""

from __future__ import annotations

from typing import Any, Final

from zigpy.quirks import CustomCluster
from zigpy.quirks.v2 import NumberDeviceClass, QuirkBuilder, SensorDeviceClass
from zigpy.quirks.v2.homeassistant import PERCENTAGE, UnitOfTemperature
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.measurement import (
    RelativeHumidity,
    SoilMoisture,
    TemperatureMeasurement,
)
from zigpy.zcl.foundation import (
    BaseAttributeDefs,
    BaseCommandDefs,
    DataTypeId,
    ZCLAttributeDef,
    ZCLCommandDef,
)


class TemperatureUnit(t.enum16):
    """Display temperature unit."""

    Celsius = 0
    Fahrenheit = 1


class MeasureInterval(t.enum16):
    """Soil measurement interval."""

    Two_minutes = 0
    Five_minutes = 1
    Fifteen_minutes = 2
    Thirty_minutes = 3
    One_hour = 4
    Two_hours = 5


class SonoffSoilCluster(CustomCluster):
    """SONOFF private cluster for SNZB-SSZ2N."""

    cluster_id = 0xFC11
    ep_attribute = "sonoff_soil_cluster"

    class ServerCommandDefs(BaseCommandDefs):
        """Commands accepted by the sensor."""

        refresh_measurements: Final = ZCLCommandDef(
            id=0x00, schema={}, manufacturer_code=None
        )

    async def refresh_measurements(self) -> Any:
        """Send the FC11 refresh command when the HA button is pressed."""
        return await self.command(
            self.ServerCommandDefs.refresh_measurements.id, expect_reply=False
        )

    class AttributeDefs(BaseAttributeDefs):
        """SONOFF private attributes."""

        temperature_unit: Final = ZCLAttributeDef(
            id=0x0007,
            type=TemperatureUnit,
            zcl_type=DataTypeId.uint16,
            manufacturer_code=None,
        )
        temperature_offset: Final = ZCLAttributeDef(
            id=0x2003,
            type=t.int16s,
            manufacturer_code=None,
        )
        humidity_offset: Final = ZCLAttributeDef(
            id=0x2004,
            type=t.int16s,
            manufacturer_code=None,
        )
        measure_interval: Final = ZCLAttributeDef(
            id=0x202D,
            type=MeasureInterval,
            zcl_type=DataTypeId.uint16,
            manufacturer_code=None,
        )
        temperature_threshold_enable: Final = ZCLAttributeDef(
            id=0x202A,
            type=t.uint8_t,
            manufacturer_code=None,
        )
        temperature_threshold_high: Final = ZCLAttributeDef(
            id=0x0003,
            type=t.int16s,
            manufacturer_code=None,
        )
        temperature_threshold_low: Final = ZCLAttributeDef(
            id=0x0004,
            type=t.int16s,
            manufacturer_code=None,
        )
        humidity_threshold_enable: Final = ZCLAttributeDef(
            id=0x202B,
            type=t.uint8_t,
            manufacturer_code=None,
        )
        humidity_threshold_dry: Final = ZCLAttributeDef(
            id=0x0005,
            type=t.uint16_t,
            manufacturer_code=None,
        )
        humidity_threshold_damp: Final = ZCLAttributeDef(
            id=0x0006,
            type=t.uint16_t,
            manufacturer_code=None,
        )
        frost_threshold_enable: Final = ZCLAttributeDef(
            id=0x202C,
            type=t.uint8_t,
            manufacturer_code=None,
        )
        frost_threshold_temperature: Final = ZCLAttributeDef(
            id=0x6002,
            type=t.int16s,
            manufacturer_code=None,
        )
        temperature_threshold_status: Final = ZCLAttributeDef(
            id=0xF001, type=t.CharacterString, access="r", manufacturer_code=None
        )
        humidity_threshold_status: Final = ZCLAttributeDef(
            id=0xF002, type=t.CharacterString, access="r", manufacturer_code=None
        )
        frost_warning_status: Final = ZCLAttributeDef(
            id=0xF003, type=t.CharacterString, access="r", manufacturer_code=None
        )

    def _set_status(self, attr_def: ZCLAttributeDef, value: str | None) -> None:
        """Update a local status only when its value changes."""
        if self.get(attr_def.id) != value:
            super()._update_attribute(attr_def.id, value)

    def _refresh_temperature_status(self) -> None:
        temperature = self.endpoint.temperature.get(
            TemperatureMeasurement.AttributeDefs.measured_value.id
        )
        low = self.get(self.AttributeDefs.temperature_threshold_low.id)
        high = self.get(self.AttributeDefs.temperature_threshold_high.id)
        frost = self.get(self.AttributeDefs.frost_threshold_temperature.id)

        if temperature is None or temperature == foundation.DataType.int16.non_value:
            temperature_status = frost_status = None
        else:
            temperature_status = None
            if low is not None and high is not None:
                if temperature < low:
                    temperature_status = "冷"
                elif temperature > high:
                    temperature_status = "热"
                else:
                    temperature_status = "正常"
            frost_status = None
            if frost is not None:
                frost_status = "告警" if temperature < frost else "正常"

        self._set_status(
            self.AttributeDefs.temperature_threshold_status, temperature_status
        )
        self._set_status(self.AttributeDefs.frost_warning_status, frost_status)

    def _refresh_humidity_status(self) -> None:
        humidity = self.endpoint.soil_moisture.get(
            SoilMoisture.AttributeDefs.measured_value.id
        )
        dry = self.get(self.AttributeDefs.humidity_threshold_dry.id)
        damp = self.get(self.AttributeDefs.humidity_threshold_damp.id)

        if (
            humidity is None
            or humidity == foundation.DataType.uint16.non_value
            or dry is None
            or damp is None
        ):
            status = None
        else:
            status = "干" if humidity < dry else "湿" if humidity > damp else "正常"

        self._set_status(self.AttributeDefs.humidity_threshold_status, status)

    def _update_attribute(
        self, attrid: int | t.uint16_t | ZCLAttributeDef, value: Any
    ) -> None:
        """Recalculate statuses when a threshold is reported or changed."""
        super()._update_attribute(attrid, value)
        attr_id = attrid.id if isinstance(attrid, ZCLAttributeDef) else attrid
        if attr_id in (
            self.AttributeDefs.temperature_threshold_low.id,
            self.AttributeDefs.temperature_threshold_high.id,
            self.AttributeDefs.frost_threshold_temperature.id,
        ):
            self._refresh_temperature_status()
        elif attr_id in (
            self.AttributeDefs.humidity_threshold_dry.id,
            self.AttributeDefs.humidity_threshold_damp.id,
        ):
            self._refresh_humidity_status()

    def _value_for_write(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        attr_def: ZCLAttributeDef,
        default: int,
    ) -> int:
        """Return a pending value, or the cached/device default value."""

        for attr, value in attributes.items():
            if self.find_attribute(attr).id == attr_def.id:
                return int(value)
        return int(self._attr_cache.get(attr_def.id, default))

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        **kwargs: Any,
    ) -> list[list[foundation.WriteAttributesStatusRecord]]:
        """Reject threshold ranges where the lower limit exceeds the upper limit."""

        attributes = dict(attributes)
        for attr, value in attributes.items():
            attr_id = self.find_attribute(attr).id
            if attr_id == self.AttributeDefs.temperature_offset.id:
                # The device stores hundredths of a degree; align 0.1 C input
                # to a multiple of ten to avoid float conversion truncation.
                attributes[attr] = int(round(int(value) / 10.0) * 10)
            elif attr_id == self.AttributeDefs.humidity_offset.id:
                # The device stores hundredths of a percent; keep one decimal
                # place even if HA's float-to-int conversion truncates a unit.
                attributes[attr] = int(round(int(value) / 10.0) * 10)

        temperature_low = self._value_for_write(
            attributes, self.AttributeDefs.temperature_threshold_low, 800
        )
        temperature_high = self._value_for_write(
            attributes, self.AttributeDefs.temperature_threshold_high, 3200
        )
        humidity_dry = self._value_for_write(
            attributes, self.AttributeDefs.humidity_threshold_dry, 1500
        )
        humidity_damp = self._value_for_write(
            attributes, self.AttributeDefs.humidity_threshold_damp, 3000
        )

        if temperature_low > temperature_high or humidity_dry > humidity_damp:
            return [
                [
                    foundation.WriteAttributesStatusRecord(
                        foundation.Status.INVALID_VALUE
                    )
                ]
            ]

        result = await super().write_attributes(attributes, **kwargs)
        successful_ids = {
            record.attrid
            for record in result[0]
            if record.status == foundation.Status.SUCCESS
        }
        if successful_ids & {
            self.AttributeDefs.temperature_threshold_low.id,
            self.AttributeDefs.temperature_threshold_high.id,
            self.AttributeDefs.frost_threshold_temperature.id,
        }:
            self._refresh_temperature_status()
        if successful_ids & {
            self.AttributeDefs.humidity_threshold_dry.id,
            self.AttributeDefs.humidity_threshold_damp.id,
        }:
            self._refresh_humidity_status()
        return result


class SonoffTemperatureMeasurement(CustomCluster, TemperatureMeasurement):
    """Recalculate threshold states when a temperature report arrives."""

    def _update_attribute(
        self, attrid: int | t.uint16_t | ZCLAttributeDef, value: Any
    ) -> None:
        super()._update_attribute(attrid, value)
        attr_id = attrid.id if isinstance(attrid, ZCLAttributeDef) else attrid
        if attr_id == self.AttributeDefs.measured_value.id:
            self.endpoint.sonoff_soil_cluster._refresh_temperature_status()


class SonoffSoilMoisture(CustomCluster, SoilMoisture):
    """Recalculate threshold state when a soil moisture report arrives."""

    def _update_attribute(
        self, attrid: int | t.uint16_t | ZCLAttributeDef, value: Any
    ) -> None:
        super()._update_attribute(attrid, value)
        attr_id = attrid.id if isinstance(attrid, ZCLAttributeDef) else attrid
        if attr_id == self.AttributeDefs.measured_value.id:
            self.endpoint.sonoff_soil_cluster._refresh_humidity_status()


(
    QuirkBuilder("SONOFF", "SS-Z2N")
    .applies_to("SONOFF", "SNZB-SSZ2N")
    .replaces(SonoffSoilCluster)
    .replaces(SonoffTemperatureMeasurement)
    .replaces(SonoffSoilMoisture)
    .command_button(
        command_name=SonoffSoilCluster.ServerCommandDefs.refresh_measurements.name,
        cluster_id=SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_refresh_measurements",
        fallback_name="Refresh soil temperature and moisture",
    )
    .change_entity_metadata(
        endpoint_id=1,
        cluster_id=SoilMoisture.cluster_id,
        new_device_class=SensorDeviceClass.MOISTURE,
    )
    .prevent_default_entity_creation(
        endpoint_id=1,
        cluster_id=RelativeHumidity.cluster_id,
    )
    .sensor(
        SonoffSoilCluster.AttributeDefs.temperature_threshold_status.name,
        SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_temperature_threshold_status",
        fallback_name="Temperature threshold status",
    )
    .sensor(
        SonoffSoilCluster.AttributeDefs.humidity_threshold_status.name,
        SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_humidity_threshold_status",
        fallback_name="Soil moisture threshold status",
    )
    .sensor(
        SonoffSoilCluster.AttributeDefs.frost_warning_status.name,
        SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_frost_warning_status",
        fallback_name="Frost warning status",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.temperature_offset.name,
        SonoffSoilCluster.cluster_id,
        min_value=-10,
        max_value=10,
        step=0.1,
        mode="box",
        device_class=NumberDeviceClass.TEMPERATURE_DELTA,
        unit=UnitOfTemperature.CELSIUS,
        multiplier=0.01,
        translation_key="snzbssz2n_temperature_offset",
        fallback_name="9 Temperature offset",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.humidity_offset.name,
        SonoffSoilCluster.cluster_id,
        min_value=-100,
        max_value=100,
        step=0.1,
        mode="box",
        device_class=NumberDeviceClass.HUMIDITY,
        unit=PERCENTAGE,
        multiplier=0.01,
        translation_key="snzbssz2n_humidity_offset",
        fallback_name="10 Humidity offset",
    )
    .enum(
        SonoffSoilCluster.AttributeDefs.measure_interval.name,
        MeasureInterval,
        SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_measure_interval",
        fallback_name="Measure interval",
    )
    .switch(
        SonoffSoilCluster.AttributeDefs.temperature_threshold_enable.name,
        SonoffSoilCluster.cluster_id,
        off_value=0,
        on_value=1,
        translation_key="snzbssz2n_temperature_threshold_enable",
        fallback_name="1 Temperature threshold enable",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.temperature_threshold_high.name,
        SonoffSoilCluster.cluster_id,
        min_value=-10,
        max_value=60,
        step=1,
        mode="box",
        multiplier=0.01,
        device_class=NumberDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        translation_key="snzbssz2n_temperature_threshold_high",
        fallback_name="2 Temperature threshold high",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.temperature_threshold_low.name,
        SonoffSoilCluster.cluster_id,
        min_value=-10,
        max_value=60,
        step=1,
        mode="box",
        multiplier=0.01,
        device_class=NumberDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        translation_key="snzbssz2n_temperature_threshold_low",
        fallback_name="3 Temperature threshold low",
    )
    .switch(
        SonoffSoilCluster.AttributeDefs.humidity_threshold_enable.name,
        SonoffSoilCluster.cluster_id,
        off_value=0,
        on_value=1,
        translation_key="snzbssz2n_humidity_threshold_enable",
        fallback_name="4 Humidity threshold enable",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.humidity_threshold_dry.name,
        SonoffSoilCluster.cluster_id,
        min_value=1,
        max_value=99,
        step=1,
        mode="box",
        multiplier=0.01,
        device_class=NumberDeviceClass.HUMIDITY,
        unit=PERCENTAGE,
        translation_key="snzbssz2n_humidity_threshold_dry",
        fallback_name="6 Humidity threshold dry",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.humidity_threshold_damp.name,
        SonoffSoilCluster.cluster_id,
        min_value=1,
        max_value=99,
        step=1,
        mode="box",
        multiplier=0.01,
        device_class=NumberDeviceClass.HUMIDITY,
        unit=PERCENTAGE,
        translation_key="snzbssz2n_humidity_threshold_damp",
        fallback_name="5 Humidity threshold damp",
    )
    .switch(
        SonoffSoilCluster.AttributeDefs.frost_threshold_enable.name,
        SonoffSoilCluster.cluster_id,
        off_value=0,
        on_value=1,
        translation_key="snzbssz2n_frost_threshold_enable",
        fallback_name="7 Frost threshold enable",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.frost_threshold_temperature.name,
        SonoffSoilCluster.cluster_id,
        min_value=-10,
        max_value=20,
        step=1,
        mode="box",
        multiplier=0.01,
        device_class=NumberDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        translation_key="snzbssz2n_frost_threshold_temperature",
        fallback_name="8 Frost threshold temperature",
    )
    .add_to_registry()
)
