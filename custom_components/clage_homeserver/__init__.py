"""clage_homeserver integration"""
import voluptuous as vol
import ipaddress
import logging
from datetime import timedelta
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_HOST, CONF_SCAN_INTERVAL
from homeassistant.core import valid_entity_id
from homeassistant import core
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_HOMESERVERS,
    CONF_HOMESERVER_IP_ADDRESS,
    CONF_HOMESERVER_ID,
    CONF_HEATER_ID,
    HOMESERVER_API,
)

from clage_homeserver import ClageHomeServer

_LOGGER = logging.getLogger(__name__)

SERVICE_HOMESERVE_NAME_ATTRIBUTE = "homeserver_name"
SERVICE_HEATER_ID_ATTRIBUTE = "heater_id"
SERVICE_HEATER_TEMPERATURE = "temperature"

MIN_UPDATE_INTERVAL = timedelta(seconds=10)
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=20)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_HOMESERVERS, default=[]): vol.All(
                    [
                        cv.ensure_list,
                        [
                            vol.All(
                                {
                                    vol.Required(CONF_NAME): vol.All(cv.string),
                                    vol.Required(CONF_HOMESERVER_IP_ADDRESS): vol.All(
                                        ipaddress.ip_address, cv.string
                                    ),
                                    vol.Required(CONF_HOMESERVER_ID): vol.All(
                                        cv.string
                                    ),
                                    vol.Required(CONF_HEATER_ID): vol.All(cv.string),
                                }
                            )
                        ],
                    ]
                ),
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                ): vol.All(cv.time_period, vol.Clamp(min=MIN_UPDATE_INTERVAL)),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup_entry(hass, config):
    """Setup the integration in HOME ASSISTANT (Read and apply the configuration)"""

    _LOGGER.info("Async_Setup_entry for clage_homeserver component")
    _LOGGER.debug(repr(config.data))

    name = config.data[CONF_NAME]
    clage_homeserver = ClageHomeServer(
        config.data[CONF_HOMESERVER_IP_ADDRESS],
        config.data[CONF_HOMESERVER_ID],
        config.data[CONF_HEATER_ID],
    )
    hass.data[DOMAIN]["api"][name] = clage_homeserver

    await hass.data[DOMAIN]["coordinator"].async_refresh()

    # device_registry = dr.async_get(hass)

    # device_registry.async_get_or_create(
    #     config_entry_id=config.entry_id,
    #     identifiers=f"{name}.{config.data[CONF_HOMESERVER_ID]}.{config.data[CONF_HEATER_ID]}",
    #     manufacturer="CLAGE GmbH",
    #     name=name,
    #     model="DSX Touch",
    #     configuration_url=config.data[CONF_HOMESERVER_IP_ADDRESS],
    # )

    hass.async_create_task(
        hass.config_entries.async_forward_entry_setup(config, ["sensor"])
    )
    return True


async def async_unload_entry(hass, entry):
    """Unload the integration clage_homeserver from HOME ASSISTANT"""

    _LOGGER.info("Unloading homeserver %s", entry.data[CONF_NAME])
    hass.data[DOMAIN]["api"].pop(entry.data[CONF_NAME])
    return True


class HomeserverStateFetcher:
    """Class to manage the states of the homeserver and heater"""

    def __init__(self, hass):
        self._hass = hass

    async def fetch_states(self):
        """Fetch the actual states from the homeserver"""

        _LOGGER.debug("Updating the states")
        homeservers = self._hass.data[DOMAIN]["api"]
        data = self.coordinator.data if self.coordinator.data else {}
        for homeserver_id in homeservers.keys():
            homeserver = homeservers[homeserver_id]

            _LOGGER.debug(
                "Fetch the states (status) from the CLAGE Homeserver '%s' und update them in Home Assistant",
                homeserver_id,
            )
            fetched_states = dict(
                await self._hass.async_add_executor_job(homeserver.requestStatus)
            )

            _LOGGER.debug(
                "Fetch the states (setup) from the CLAGE Homeserver '%s' und update them in Home Assistant",
                homeserver_id,
            )
            fetched_states.update(
                await self._hass.async_add_executor_job(homeserver.requestSetup)
            )

            _LOGGER.debug(
                "Fetch the consumption logs from the CLAGE Homeserver '%s' und update them in Home Assistant",
                homeserver_id,
            )
            fetched_states.update(
                await self._hass.async_add_executor_job(homeserver.GetConsumptionTotals)
            )

            data[homeserver_id] = fetched_states
        return data


async def async_setup(hass: core.HomeAssistant, config: dict) -> bool:
    """Set up clage_homeserver platforms and services."""

    _LOGGER.debug("clage_homeserver: async_setup")
    scan_interval = DEFAULT_UPDATE_INTERVAL

    hass.data[DOMAIN] = {}
    homeserver_api = {}
    homeservers = []
    if DOMAIN in config:
        scan_interval = config[DOMAIN].get(CONF_SCAN_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        homeservers = config[DOMAIN].get(CONF_HOMESERVERS, [])

        for homeserver in homeservers:
            homeserver_name = homeserver[0][CONF_NAME]
            ip_address = homeserver[0][CONF_HOMESERVER_IP_ADDRESS]
            homeserver_id = homeserver[0][CONF_HOMESERVER_ID]
            heater_id = homeserver[0][CONF_HEATER_ID]
            _LOGGER.info(
                "Setup: ip_address: '%s', homeserver_id: '%s', heater_id: '%s'",
                ip_address,
                homeserver_id,
                heater_id,
            )
            clage_home_server = ClageHomeServer(ip_address, homeserver_id, heater_id)
            homeserver_api[homeserver_name] = clage_home_server

    hass.data[DOMAIN]["api"] = homeserver_api

    homeserver_state_fetcher = HomeserverStateFetcher(hass)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=homeserver_state_fetcher.fetch_states,
        update_interval=scan_interval,
    )
    homeserver_state_fetcher.coordinator = coordinator

    hass.data[DOMAIN]["coordinator"] = coordinator

    await coordinator.async_refresh()

    async def async_handle_set_temperature(call):
        """Handle the service call to set the temperature of the heater."""
        homeserver_name_input = call.data.get(SERVICE_HOMESERVE_NAME_ATTRIBUTE, "")
        heater_id_input = call.data.get(SERVICE_HEATER_ID_ATTRIBUTE, "")
        temperature_input = call.data.get(SERVICE_HEATER_TEMPERATURE, "")
        temperature = 0
        if isinstance(temperature_input, str):
            if temperature_input.isnumeric():
                temperature = int(temperature_input)
            elif valid_entity_id(temperature_input):
                temperature = int(hass.states.get(temperature_input).state)
            else:
                _LOGGER.error(
                    "No valid value for '%s': %s",
                    SERVICE_HEATER_TEMPERATURE,
                    temperature,
                )
                return
        else:
            temperature = temperature_input

        temperature = min(max(temperature, 10), 60)

        if (len(heater_id_input) > 0) and len(heater_id_input) > 0:
            _LOGGER.debug(
                "Set temperature (setpoint) for homerserver/heater %s/%s to %s °C",
                homeserver_name_input,
                heater_id_input,
                temperature,
            )

            try:
                homeservers = hass.data[DOMAIN]["api"]
                homeserver = homeservers[homeserver_name_input]
                await hass.async_add_executor_job(
                    homeserver.setTemperature,
                    temperature,
                )
            except KeyError:
                _LOGGER.error("Heater with id '%s' not found!", heater_id_input)

        await hass.data[DOMAIN]["coordinator"].async_refresh()

    hass.services.async_register(
        DOMAIN, "set_temperature", async_handle_set_temperature
    )

    hass.async_create_task(
        async_load_platform(
            hass,
            "sensor",
            DOMAIN,
            {CONF_HOMESERVERS: homeservers, HOMESERVER_API: homeserver_api},
            config,
        )
    )

    return True
