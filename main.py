
#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#  main.py
#  NatureBridge
#
#  Copyright 2020 Kenji Nishishiro. All rights reserved.
#  Written by Kenji Nishishiro <marvel@programmershigh.org>.
#

import os
import logging
import signal

from pyhap.const import CATEGORY_SENSOR, CATEGORY_LIGHTBULB, CATEGORY_AIR_CONDITIONER
from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from remo import NatureRemoAPI, NatureRemoError

logging.basicConfig(level=logging.INFO, format="[%(module)s] %(message)s")

api = NatureRemoAPI(os.environ['ACCESS_TOKEN'])


class NatureAccessory(Accessory):

    def __init__(self, driver, device, appliance=None):
        if appliance is None:
            super().__init__(driver, device.name)

            self.device_id = device.id
            self.appliance_id = None 
            self.appliance_type = None 

        else:
            super().__init__(driver, appliance.nickname)

            self.device_id = device.id
            self.appliance_id = appliance.id 
            self.appliance_type = appliance.type


class NatureBridge(Bridge):

    @Accessory.run_at_interval(60)
    async def run(self):
        #
        # Nature APIのリクエスト制限を軽減する為に、一度に取得した値を複数のアクセサリで共有する。
        # アクセサリの状態をそれぞれが更新する必要は無いので、Accessory.runは呼び出さない。
        #
        try:
            devices = api.get_devices()
            appliances = api.get_appliances()

        except NatureRemoError as exception:
            logging.exception(exception)
            return

        # TODO: アクセサリの増減や変更を検出した場合の対応を考える。
        for accessory in self.accessories.values():
            if accessory.appliance_id is None:
                device = next(filter(lambda device: device.id == accessory.device_id, devices), None)
                if device is None:
                    continue

                accessory.update(device)

            else:
                appliance = next(filter(lambda appliance: appliance.id == accessory.appliance_id, appliances), None)
                if appliance is None:
                    continue
                if appliance.type != accessory.appliance_type:
                    continue

                device = next(filter(lambda device: device.id == appliance.device.id, devices), None)
                if device is None:
                    continue

                accessory.update(device, appliance)


class Sensor(NatureAccessory):

    category = CATEGORY_SENSOR

    def __init__(self, driver, device):
        super().__init__(driver, device)

        temperature_sensor = self.add_preload_service('TemperatureSensor')
        self._current_temperature = temperature_sensor.configure_char(
            'CurrentTemperature',
            value=device.newest_events.get('te').val
        )

        if 'hu' in device.newest_events:
            humidity_sensor = self.add_preload_service('HumiditySensor')
            self._current_relative_humidity = humidity_sensor.configure_char(
                'CurrentRelativeHumidity',
                value=device.newest_events.get('hu').val
            )

        if 'il' in device.newest_events:
            light_sensor = self.add_preload_service('LightSensor')
            self._current_ambient_light_level = light_sensor.configure_char(
                'CurrentAmbientLightLevel',
                value=device.newest_events.get('il').val
            )

    def update(self, device):
        self._current_temperature.set_value(device.newest_events['te'].val)

        if hasattr(self, '_current_relative_humidity') and 'hu' in device.newest_events:
            self._current_relative_humidity.set_value(device.newest_events['hu'].val)

        if hasattr(self, '_current_ambient_light_level') and 'il' in device.newest_events:
            self._current_ambient_light_level.set_value(device.newest_events['il'].val)


class Aircon(NatureAccessory):

    category = CATEGORY_AIR_CONDITIONER

    def __init__(self, driver, device, appliance):
        super().__init__(driver, device, appliance)

        self._temperature_unit = appliance.aircon.tempUnit

        # TODO: サーモスタットではなくエアコンと表示させたい。
        thermostat = self.add_preload_service('Thermostat')
        self._current_heating_cooling_state = thermostat.configure_char(
            'CurrentHeatingCoolingState',
            value=self.toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button, current=True),
        )
        self._target_heating_cooling_state = thermostat.configure_char(
            'TargetHeatingCoolingState',
            value=self.toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button),
            setter_callback=self.set_target_heating_cooling_state
        )
        self._current_temperature = thermostat.configure_char(
            'CurrentTemperature',
            value=device.newest_events.get('te').val
        )
        self._target_temperature = thermostat.configure_char(
            'TargetTemperature',
            value=self.toHomeKitTemperature(appliance.settings.temp),
            setter_callback=self.set_target_temperature
        )
        self.temperature_display_units = thermostat.configure_char(
            'TemperatureDisplayUnits',
            value=self.toHomeKitTemperatureUnits()
        )

    def set_target_heating_cooling_state(self, value):
        # TODO: エアコンに設定できない運転モードであればAPIの呼び出しと現在の運転モードへの反映をスキップする。
        mode, button = self.toNatureHeatingCoolingState(value)
        try:
            api.update_aircon_settings(self.appliance_id, operation_mode=mode, button=button)

        except NatureRemoError as exception:
            logging.exception(exception)
            return

        # 運転モードの設定変更を即座に現在の運転モードに反映する。
        self._current_heating_cooling_state.set_value(
            self.toHomeKitHeatingCoolingState(mode, button, current=True)
        )

    def set_target_temperature(self, value):
        try:
            api.update_aircon_settings(self.appliance_id, temperature=self.toNatureTemperature(value))

        except NatureRemoError as exception:
            logging.exception(exception)

    def update(self, device, appliance):
        self._current_heating_cooling_state.set_value(
            self.toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button, current=True)
        )
        self._target_heating_cooling_state.set_value(
            self.toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button)
        )
        self._current_temperature.set_value(device.newest_events['te'].val)
        self._target_temperature.set_value(self.toHomeKitTemperature(appliance.settings.temp))

    def toHomeKitHeatingCoolingState(self, mode, button, current=False):
        if button == '':
            # HomeKitに設定できない運転モードは冷房と仮定している。
            if mode == 'cool':
                return 2
            elif mode == 'warm':
                return 1
            elif mode == 'dry':
                return 2
            elif mode == 'blow':
                return 2
            elif mode == 'auto':
                # 自動運転中の実際の運転モードは判別する方法が無いので冷房と仮定している。
                if current:
                    return 2
                else:
                    return 3
            else:
                raise ValueError
        elif button == 'power-off':
            return 0
        else:
            raise ValueError

    def toNatureHeatingCoolingState(self, value):
        if value == 0:
            return None, 'power-off'
        elif value == 1:
            return 'warm', ''
        elif value == 2:
            return 'cool', ''
        elif value == 3:
            return 'auto', ''
        else:
            raise ValueError

    def toHomeKitTemperature(self, value):
        # HomeKitは0.1度刻みで温度を設定できる。
        if self._temperature_unit == 'c':
            return round(float(value), 1)
        elif self._temperature_unit == 'f':
            return round((float(value) - 32) * 5 / 9, 1)
        else:
            raise ValueError

    def toNatureTemperature(self, value):
        # TODO: エアコンが対応している精度と範囲で近似値に丸める。
        if self._temperature_unit == 'c':
            return str(round(value))
        elif self._temperature_unit == 'f':
            return str(round(value * 9 / 5 + 32))
        else:
            raise ValueError

    def toHomeKitTemperatureUnits(self):
        if self._temperature_unit == 'c':
            return 0
        elif self._temperature_unit == 'f':
            return 1
        else:
            raise ValueError


class Light(NatureAccessory):

    category = CATEGORY_LIGHTBULB

    def __init__(self, driver, device, appliance):
        super().__init__(driver, device, appliance)

        # Brightnessは絶対値で設定する方法が無いので対応できない。
        lightbulb = self.add_preload_service('Lightbulb')
        self._on = lightbulb.configure_char(
            'On',
            value=self.toHomeKitPower(appliance.light.state.power),
            setter_callback=self.set_on
        )

    def set_on(self, value):
        try:
            api.send_light_infrared_signal(self.appliance_id, self.toNaturePower(value))

        except NatureRemoError as exception:
            logging.exception(exception)

    def update(self, device, appliance):
        self._on.set_value(self.toHomeKitPower(appliance.light.state.power))

    def toHomeKitPower(self, value):
        if value == 'on':
            return 1
        elif value == 'off':
            return 0
        else:
            raise ValueError

    def toNaturePower(self, value):
        if value == 0:
            return 'off'
        elif value == 1:
            return 'on'
        else:
            raise ValueError


# TODO: ペアリングをやり直す場合の場合の対応を考える。
driver = AccessoryDriver(port=51826)

try:
    user = api.get_user()
    devices = api.get_devices()
    appliances = api.get_appliances()
except NatureRemoError as exception:
    logging.exception(exception)
    exit(1)

bridge = NatureBridge(driver, user.nickname)

# TODO: 再起動時にアクセサリが増減するとHomeKit側の対応が崩れる問題の対応を考える。
for device in devices:
    accessory = Sensor(
        driver,
        device
    )
    bridge.add_accessory(accessory)

for appliance in appliances:
    device = next(filter(lambda device: device.id == appliance.device.id, devices), None)
    if device is None:
        continue

    if appliance.type == 'AC':
        accessory = Aircon(
            driver,
            device,
            appliance
        )
        bridge.add_accessory(accessory)

    elif appliance.type == 'LIGHT':
        accessory = Light(
            driver,
            device,
            appliance
        )
        bridge.add_accessory(accessory)

driver.add_accessory(bridge)

signal.signal(signal.SIGTERM, driver.signal_handler)

driver.start()
