"""Sonoff SNZB-SSZ2N Zigbee soil temperature and moisture sensor."""

from __future__ import annotations

from typing import Any, Final

from zigpy.quirks import CustomCluster
from zigpy.quirks.v2 import NumberDeviceClass, QuirkBuilder, SensorDeviceClass
from zigpy.quirks.v2.homeassistant import PERCENTAGE, UnitOfTemperature
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.clusters.measurement import SoilMoisture
from zigpy.zcl.foundation import BaseAttributeDefs, DataTypeId, ZCLAttributeDef


class TemperatureUnit(t.enum16):
    """Display temperature unit."""

    Celsius = 0
    Fahrenheit = 1


class MeasureInterval(t.enum16):
    """Soil measurement interval."""

    One_minute = 0
    Five_minutes = 1
    Fifteen_minutes = 2
    Thirty_minutes = 3
    One_hour = 4
    Two_hours = 5


class SonoffSoilCluster(CustomCluster):
    """SONOFF private cluster for SNZB-SSZ2N."""

    cluster_id = 0xFC11
    ep_attribute = "sonoff_soil_cluster"

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
            elif attr_id == self.AttributeDefs.humidity_offset.id and int(value) % 100:
                return [
                    [
                        foundation.WriteAttributesStatusRecord(
                            foundation.Status.INVALID_VALUE
                        )
                    ]
                ]

        temperature_low = self._value_for_write(
            attributes, self.AttributeDefs.temperature_threshold_low, 8
        )
        temperature_high = self._value_for_write(
            attributes, self.AttributeDefs.temperature_threshold_high, 32
        )
        humidity_dry = self._value_for_write(
            attributes, self.AttributeDefs.humidity_threshold_dry, 15
        )
        humidity_damp = self._value_for_write(
            attributes, self.AttributeDefs.humidity_threshold_damp, 30
        )

        if temperature_low > temperature_high or humidity_dry > humidity_damp:
            return [
                [
                    foundation.WriteAttributesStatusRecord(
                        foundation.Status.INVALID_VALUE
                    )
                ]
            ]

        return await super().write_attributes(attributes, **kwargs)


(
    QuirkBuilder("SONOFF", "SS-Z2N")
    .applies_to("SONOFF", "SNZB-SSZ2N")
    .replaces(SonoffSoilCluster)
    .change_entity_metadata(
        endpoint_id=1,
        cluster_id=SoilMoisture.cluster_id,
        new_device_class=SensorDeviceClass.MOISTURE,
    )
    .enum(
        SonoffSoilCluster.AttributeDefs.temperature_unit.name,
        TemperatureUnit,
        SonoffSoilCluster.cluster_id,
        translation_key="snzbssz2n_temperature_unit",
        fallback_name="11 Temperature unit",
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
        step=1,
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
        fallback_name="12 Measure interval",
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
        min_value=-20,
        max_value=60,
        step=1,
        mode="box",
        device_class=NumberDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        translation_key="snzbssz2n_temperature_threshold_high",
        fallback_name="2 Temperature threshold high",
    )
    .number(
        SonoffSoilCluster.AttributeDefs.temperature_threshold_low.name,
        SonoffSoilCluster.cluster_id,
        min_value=-20,
        max_value=60,
        step=1,
        mode="box",
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
        min_value=-20,
        max_value=60,
        step=1,
        mode="box",
        device_class=NumberDeviceClass.TEMPERATURE,
        unit=UnitOfTemperature.CELSIUS,
        translation_key="snzbssz2n_frost_threshold_temperature",
        fallback_name="8 Frost threshold temperature",
    )
    .add_to_registry()
)




