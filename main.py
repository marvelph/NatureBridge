
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

from pyhap.const import CATEGORY_SENSOR, CATEGORY_AIR_CONDITIONER, CATEGORY_TELEVISION, CATEGORY_LIGHTBULB
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
        # Nature APIのリクエスト制限を軽減する為に、一度のAPI呼び出しで取得した値を使って全てのアクセサリを更新する。
        # アクセサリの状態をそれぞれが能動的に更新する必要は無いので、ブリッジに追加されているアクセサリのrunメソッドは呼び出さない。
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

        # TODO: 人感センサーの対応方法を考える。

        # 温度センサーは必ず利用できると仮定している。
        self._temperature_sensor = self.add_preload_service('TemperatureSensor')
        self._current_temperature = self._temperature_sensor.configure_char(
            'CurrentTemperature',
            value=device.newest_events.get('te').val
        )

        if 'hu' in device.newest_events:
            self._humidity_sensor = self.add_preload_service('HumiditySensor')
            self._current_relative_humidity = self._humidity_sensor.configure_char(
                'CurrentRelativeHumidity',
                value=device.newest_events.get('hu').val
            )

        if 'il' in device.newest_events:
            self._light_sensor = self.add_preload_service('LightSensor')
            self._current_ambient_light_level = self._light_sensor.configure_char(
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

        self._thermostat = self.add_preload_service('Thermostat')
        self._current_heating_cooling_state = self._thermostat.configure_char(
            'CurrentHeatingCoolingState',
            value=self._toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button, current=True),
        )
        self._target_heating_cooling_state = self._thermostat.configure_char(
            'TargetHeatingCoolingState',
            value=self._toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button),
            setter_callback=self._set_target_heating_cooling_state
        )
        self._current_temperature = self._thermostat.configure_char(
            'CurrentTemperature',
            value=device.newest_events.get('te').val
        )
        self._target_temperature = self._thermostat.configure_char(
            'TargetTemperature',
            value=self._toHomeKitTemperature(appliance.settings.temp),
            setter_callback=self._set_target_temperature
        )
        # エアコンの表示単位を変更する方法は無いので書き込みには対応できない。
        self._temperature_display_units = self._thermostat.configure_char(
            'TemperatureDisplayUnits',
            value=self._toHomeKitTemperatureUnits()
        )

    def _set_target_heating_cooling_state(self, value):
        # TODO: エアコンに設定できない運転モードであればAPIの呼び出しと実際の運転モードとしての反映をスキップするべき。
        mode, button = self._toNatureHeatingCoolingState(value)
        try:
            api.update_aircon_settings(self.appliance_id, operation_mode=mode, button=button)

        except NatureRemoError as exception:
            logging.exception(exception)
            return

        # 運転モードの設定変更を即座に実際の運転モードとして反映する。
        self._current_heating_cooling_state.set_value(
            self._toHomeKitHeatingCoolingState(mode, button, current=True)
        )

    def _set_target_temperature(self, value):
        try:
            api.update_aircon_settings(self.appliance_id, temperature=self._toNatureTemperature(value))

        except NatureRemoError as exception:
            logging.exception(exception)

    def update(self, device, appliance):
        self._current_heating_cooling_state.set_value(
            self._toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button, current=True)
        )
        self._target_heating_cooling_state.set_value(
            self._toHomeKitHeatingCoolingState(appliance.settings.mode, appliance.settings.button)
        )
        self._current_temperature.set_value(device.newest_events['te'].val)
        self._target_temperature.set_value(self._toHomeKitTemperature(appliance.settings.temp))

    def _toHomeKitHeatingCoolingState(self, mode, button, current=False):
        if button == '':
            # HomeKitに設定できない運転モードは冷房と仮定している。
            if mode == 'cool':
                return 2  # Cool
            elif mode == 'warm':
                return 1  # Heat
            elif mode == 'dry':
                return 2  # Cool
            elif mode == 'blow':
                return 2  # Cool
            elif mode == 'auto':
                # 自動運転中に実際の運転モードを判別する方法が無いので冷房と仮定している。
                if current:
                    return 2  # Cool
                else:
                    return 3  # Auto
            else:
                raise ValueError
        elif button == 'power-off':
            return 0  # Off
        else:
            raise ValueError

    def _toNatureHeatingCoolingState(self, value):
        if value == 0:  # Off
            return None, 'power-off'
        elif value == 1:  # Heat
            return 'warm', ''
        elif value == 2:  # Cool
            return 'cool', ''
        elif value == 3:  # Auto
            return 'auto', ''
        else:
            raise ValueError

    def _toHomeKitTemperature(self, value):
        # HomeKitは0.1度刻みで温度を設定できる。
        if self._temperature_unit == 'c':
            return round(float(value), 1)
        elif self._temperature_unit == 'f':
            return round((float(value) - 32) * 5 / 9, 1)
        else:
            raise ValueError

    def _toNatureTemperature(self, value):
        # TODO: エアコンが対応している精度と範囲で近似値に丸めるべき。
        if self._temperature_unit == 'c':
            return str(round(value))
        elif self._temperature_unit == 'f':
            return str(round(value * 9 / 5 + 32))
        else:
            raise ValueError

    def _toHomeKitTemperatureUnits(self):
        if self._temperature_unit == 'c':
            return 0  # Celsius
        elif self._temperature_unit == 'f':
            return 1  # Fahrenheit
        else:
            raise ValueError


class TV(NatureAccessory):

    category = CATEGORY_TELEVISION

    def __init__(self, driver, device, appliance):
        super().__init__(driver, device, appliance)

        self._television = self.add_preload_service('Television')
        # 電源のオン・オフを判別する方法が無いのでオフと仮定している。
        self._active = self._television.configure_char(
            'Active',
            value=0,  # Inactive
            setter_callback=self._set_active
        )
        self._active_identifier = self._television.configure_char(
            'ActiveIdentifier'
        )
        self._configured_name = self._television.configure_char(
            'ConfiguredName'
        )
        self._sleep_discovery_mode = self._television.configure_char(
            'SleepDiscoveryMode',
            value=1  # AlwaysDiscoverable
        )

        self._television_speaker = self.add_preload_service(
            'TelevisionSpeaker', ['Mute', 'VolumeControlType', 'VolumeSelector']
        )
        # 消音のオン・オフを判別する方法が無いのでオフと仮定している。
        self._mute = self._television_speaker.configure_char(
            'Mute',
            value=0,  # false
            setter_callback=self._set_mute
        )
        self._volume_control_type = self._television_speaker.configure_char(
            'VolumeControlType',
            value=1  # Relative
        )
        self._volume_selector = self._television_speaker.configure_char(
            'VolumeSelector',
            setter_callback=self._set_volume_selector
        )

    def _set_active(self, value):
        try:
            # TODO: 送信できないボタンであればAPIの呼び出しをスキップするべき。
            api.send_tv_infrared_signal(self.appliance_id, 'power')

        except NatureRemoError as exception:
            logging.exception(exception)

    def _set_mute(self, value):
        try:
            # TODO: 送信できないボタンであればAPIの呼び出しをスキップするべき。
            api.send_tv_infrared_signal(self.appliance_id, 'mute')

        except NatureRemoError as exception:
            logging.exception(exception)

    def _set_volume_selector(self, value):
        try:
            # TODO: 送信できないボタンであればAPIの呼び出しをスキップするべき。
            api.send_tv_infrared_signal(self.appliance_id, self._toNatureVol(value))

        except NatureRemoError as exception:
            logging.exception(exception)

    def update(self, device, appliance):
        pass

    def _toNatureVol(self, value):
        if value == 0:  # Increment
            return 'vol-up'
        elif value == 1:  # Decrement
            return 'vol-down'
        else:
            raise ValueError


class Light(NatureAccessory):

    category = CATEGORY_LIGHTBULB

    def __init__(self, driver, device, appliance):
        super().__init__(driver, device, appliance)

        # 照度を絶対値で設定する方法は無いので対応できない。

        self._lightbulb = self.add_preload_service('Lightbulb')
        self._on = self._lightbulb.configure_char(
            'On',
            value=self._toHomeKitPower(appliance.light.state.power),
            setter_callback=self._set_on
        )

    def _set_on(self, value):
        try:
            api.send_light_infrared_signal(self.appliance_id, self._toNaturePower(value))

        except NatureRemoError as exception:
            logging.exception(exception)

    def update(self, device, appliance):
        self._on.set_value(self._toHomeKitPower(appliance.light.state.power))

    def _toHomeKitPower(self, value):
        if value == 'on':
            return 1  # true
        elif value == 'off':
            return 0  # false
        else:
            raise ValueError

    def _toNaturePower(self, value):
        if value == 0:  # false
            return 'off'
        elif value == 1:  # true
            return 'on'
        else:
            raise ValueError


# TODO: ペアリングをやり直す場合の対応を考える。
driver = AccessoryDriver(port=51826)

try:
    user = api.get_user()
    devices = api.get_devices()
    appliances = api.get_appliances()
except NatureRemoError as exception:
    logging.exception(exception)
    exit(1)

bridge = NatureBridge(driver, user.nickname)

# TODO: 再起動時にアクセサリが増減するとHomeKit側との対応が崩れる問題の対応を考える。
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

    if appliance.type == 'TV':
        accessory = TV(
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
