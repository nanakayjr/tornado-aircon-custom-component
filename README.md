# Tornado Aircon Custom Component for Home Assistant

## Description

This custom component integrates Tornado Aircon devices with Home Assistant, allowing you to control and monitor your air conditioning units directly from the Home Assistant interface.

## Installation

### Option 1: Manual Installation

1. Download the `custom_components` folder from this repository.
2. Copy the `custom_components/tornado_aircon` directory into your Home Assistant `config/custom_components` directory.
3. Restart Home Assistant.

### Option 2: Installation via HACS

1. Ensure you have [HACS](https://hacs.xyz/) installed in your Home Assistant setup.
2. Navigate to **HACS** → **Integrations**.
3. Click the three dots menu in the top right corner and select **Custom repositories**.
4. Add the repository URL `https://github.com/romfreiman/tornado-aircon-custom-component` and select the category as **Integration**.
5. Find and install the "Tornado Air Conditioner" integration from the HACS store.
6. Restart Home Assistant.

## Configuration

To set up the Tornado Air Conditioner integration in Home Assistant:

1. Navigate to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for "Tornado Air Conditioner"
4. In the configuration screen, enter:
   - Your Tornado app email address
   - Your Tornado app password
   - Region: Select USA (Note: Verified working with Israel-based deployments)
5. Click **Submit** to complete the setup

## Features

- Control power, mode, temperature, and fan speed of your Tornado Aircon units.
- Monitor current temperature, humidity, and operational status.
- Automate your air conditioning based on Home Assistant automations.
- **Power-saving preset modes**: Control energy consumption with built-in power limit presets:
  - **Normal**: Full power operation (default)
  - **Eco 30%**: Limits power consumption to 30% for maximum energy savings
  - **Eco 50%**: Limits power consumption to 50% for balanced comfort and efficiency
  - **Eco 70%**: Limits power consumption to 70% for minimal energy reduction
- **Separate entity controls**: Each AC device creates multiple entities for flexible automation:
  - **Climate Entity**: Main AC control with all features
  - **Temperature Sensors**: Room and target temperature as separate sensors
  - **Power Limit Slider**: Adjust power consumption from 30-100% with a slider (100% = no limit)
  - **HVAC Mode Selector**: Change operating mode (cool, heat, dry, fan_only, auto, off)
  - **Eco Mode Selector**: Quick access to preset power-saving modes

### Available Entities

For each Tornado AC device, the following entities are created:

#### Climate Entity
- **Entity ID**: `climate.tornado_ac_[device_name]`
- Full control over all AC functions
- Supports: power, mode, temperature, fan speed, swing, and preset modes

#### Sensor Entities
- **Room Temperature**: `sensor.tornado_ac_[device_name]_room_temperature`
  - Current room temperature reading from the AC unit
- **Target Temperature**: `sensor.tornado_ac_[device_name]_target_temperature`
  - Current temperature setpoint

#### Number Entity
- **Power Limit**: `number.tornado_ac_[device_name]_power_limit`
  - Slider control for power consumption (30-100%)
  - Set to 100% to disable power limiting
  - Works in conjunction with eco mode selector

#### Select Entities
- **HVAC Mode**: `select.tornado_ac_[device_name]_mode`
  - Options: off, cool, heat, dry, fan_only, auto
- **Eco Mode**: `select.tornado_ac_[device_name]_eco_mode`
  - Options: normal, eco_30, eco_50, eco_70

### Using Preset Modes

Preset modes allow you to control the power consumption of your air conditioner:

1. In the Home Assistant UI, select your Tornado AC device
2. Look for the "Preset" dropdown (available in the climate card)
3. Choose from:
   - **normal**: Full power operation (no power limit)
   - **eco_30**: Limits maximum power to 30% (highest energy savings)
   - **eco_50**: Limits maximum power to 50% (balanced mode)
   - **eco_70**: Limits maximum power to 70% (moderate savings)

You can also control preset modes via automations and scripts:

```yaml
# Example automation to set eco mode at night
automation:
  - alias: "AC Eco Mode at Night"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: climate.set_preset_mode
        target:
          entity_id: climate.tornado_ac_bedroom
        data:
          preset_mode: "eco_50"
```

### Using the Power Limit Slider

The power limit slider provides fine-grained control over power consumption:

```yaml
# Example: Set power limit to 60%
service: number.set_value
target:
  entity_id: number.tornado_ac_bedroom_power_limit
data:
  value: 60
```

### Using Separate Selectors

Control HVAC mode and eco mode independently:

```yaml
# Example: Switch to cooling mode
service: select.select_option
target:
  entity_id: select.tornado_ac_bedroom_mode
data:
  option: "cool"

# Example: Enable eco mode
service: select.select_option
target:
  entity_id: select.tornado_ac_bedroom_eco_mode
data:
  option: "eco_50"
```

### Using Temperature Sensors in Automations

```yaml
# Example: Turn on AC when room gets too hot
automation:
  - alias: "Auto Cool When Hot"
    trigger:
      - platform: numeric_state
        entity_id: sensor.tornado_ac_bedroom_room_temperature
        above: 26
    action:
      - service: climate.turn_on
        target:
          entity_id: climate.tornado_ac_bedroom
      - service: climate.set_hvac_mode
        target:
          entity_id: climate.tornado_ac_bedroom
        data:
          hvac_mode: "cool"
```

**Note**: Not all Tornado AC models may support the power limit feature. If your device doesn't support this feature, the preset modes and power limit slider will still be available but may not have any effect.

## Usage

Once configured, you will see new entities in Home Assistant for each Tornado Aircon unit. You can use these entities in automations, scripts, and dashboards.

## Troubleshooting

If you encounter any issues, please check the Home Assistant logs for error messages. You can also open an issue on the [GitHub repository](https://github.com/romfreiman/tornado-aircon-custom-component/issues).

## Contributing

Contributions are welcome! Please open a pull request with your changes. Make sure to follow the [contributing guidelines](CONTRIBUTING.md).

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

## Resources

- [Home Assistant Documentation](https://www.home-assistant.io/docs/)
- [Home Assistant Community Forum](https://community.home-assistant.io/)
- [HACS Documentation](https://hacs.xyz/docs/)

## Acknowledgements

Special shoutout to [@maeek](https://github.com/maeek) for their great work on [ha-aux-cloud](https://github.com/maeek/ha-aux-cloud) as a baseline for this Home Assistant component.
Also, thanks to [@thewh1teagle](https://github.com/thewh1teagle) for their excellent work on [tornado-control](https://github.com/thewh1teagle/tornado-control) which inspired this component.

## TODO

- Add a custom integration icon.
