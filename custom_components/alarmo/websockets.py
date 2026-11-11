"""WebSocket handler and registration for Alarmo configuration management."""

import time
import asyncio
import logging
import collections

import voluptuous as vol
import homeassistant.util.dt as dt_util
from homeassistant.core import callback
from homeassistant.const import (
    ATTR_CODE,
    ATTR_NAME,
    ATTR_STATE,
    ATTR_SERVICE,
    ATTR_ENTITY_ID,
    ATTR_CODE_FORMAT,
    CONF_SERVICE_DATA,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.mqtt import (
    DOMAIN as ATTR_MQTT,
)
from homeassistant.components.mqtt import (
    CONF_STATE_TOPIC,
    CONF_COMMAND_TOPIC,
)
from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
    async_dispatcher_connect,
)
from homeassistant.components.websocket_api import decorators, async_register_command
from homeassistant.components.http.decorators import require_admin
from homeassistant.components.alarm_control_panel import (
    ATTR_CODE_ARM_REQUIRED,
    CodeFormat,
)
from homeassistant.components.http.data_validator import RequestDataValidator

from . import const
from .mqtt import (
    CONF_EVENT_TOPIC,
)
from .sensors import (
    ATTR_GROUP,
    ATTR_TIMEOUT,
    SENSOR_TYPES,
    ATTR_DELAY_ON,
    ATTR_ENTITIES,
    ATTR_GROUP_ID,
    ATTR_ALWAYS_ON,
    ATTR_ALLOW_OPEN,
    ATTR_AUTO_BYPASS,
    ATTR_ENTRY_DELAY,
    ATTR_EVENT_COUNT,
    ATTR_ARM_ON_CLOSE,
    ATTR_NEW_ENTITY_ID,
    ATTR_USE_EXIT_DELAY,
    ATTR_USE_ENTRY_DELAY,
    ATTR_AUTO_BYPASS_MODES,
    ATTR_TRIGGER_UNAVAILABLE,
)


@callback
@decorators.websocket_command(
    {
        vol.Required("type"): "alarmo_config_updated",
    }
)
@decorators.async_response
async def handle_subscribe_updates(hass, connection, msg):
    """Handle subscribe updates."""

    @callback
    def async_handle_event():
        """Forward events to websocket."""
        connection.send_message(
            {
                "id": msg["id"],
                "type": "event",
            }
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, "alarmo_update_frontend", async_handle_event
    )
    connection.send_result(msg["id"])


class AlarmoConfigView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/config"
    name = "api:alarmo:config"

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Optional(ATTR_CODE_ARM_REQUIRED): cv.boolean,
                vol.Optional(const.ATTR_CODE_DISARM_REQUIRED): cv.boolean,
                vol.Optional(
                    const.ATTR_IGNORE_BLOCKING_SENSORS_AFTER_TRIGGER
                ): cv.boolean,
                vol.Optional(const.ATTR_CODE_MODE_CHANGE_REQUIRED): cv.boolean,
                vol.Optional(ATTR_CODE_FORMAT): vol.In(
                    [CodeFormat.NUMBER, CodeFormat.TEXT]
                ),
                vol.Optional(const.ATTR_TRIGGER_TIME): cv.positive_int,
                vol.Optional(const.ATTR_DISARM_AFTER_TRIGGER): cv.boolean,
                vol.Optional(ATTR_MQTT): vol.Schema(
                    {
                        vol.Required(const.ATTR_ENABLED): cv.boolean,
                        vol.Required(CONF_STATE_TOPIC): cv.string,
                        vol.Optional(const.ATTR_STATE_PAYLOAD): vol.Schema(
                            {
                                vol.Optional(const.CONF_ALARM_DISARMED): cv.string,
                                vol.Optional(const.CONF_ALARM_ARMED_HOME): cv.string,
                                vol.Optional(const.CONF_ALARM_ARMED_AWAY): cv.string,
                                vol.Optional(const.CONF_ALARM_ARMED_NIGHT): cv.string,
                                vol.Optional(
                                    const.CONF_ALARM_ARMED_CUSTOM_BYPASS
                                ): cv.string,
                                vol.Optional(
                                    const.CONF_ALARM_ARMED_VACATION
                                ): cv.string,
                                vol.Optional(const.CONF_ALARM_PENDING): cv.string,
                                vol.Optional(const.CONF_ALARM_ARMING): cv.string,
                                vol.Optional(const.CONF_ALARM_TRIGGERED): cv.string,
                            }
                        ),
                        vol.Required(CONF_COMMAND_TOPIC): cv.string,
                        vol.Optional(const.ATTR_COMMAND_PAYLOAD): vol.Schema(
                            {
                                vol.Optional(const.COMMAND_ARM_AWAY): cv.string,
                                vol.Optional(const.COMMAND_ARM_HOME): cv.string,
                                vol.Optional(const.COMMAND_ARM_NIGHT): cv.string,
                                vol.Optional(
                                    const.COMMAND_ARM_CUSTOM_BYPASS
                                ): cv.string,
                                vol.Optional(const.COMMAND_ARM_VACATION): cv.string,
                                vol.Optional(const.COMMAND_DISARM): cv.string,
                            }
                        ),
                        vol.Required(const.ATTR_REQUIRE_CODE): cv.boolean,
                        vol.Required(CONF_EVENT_TOPIC): cv.string,
                    }
                ),
                vol.Optional(const.ATTR_MASTER): vol.Schema(
                    {
                        vol.Required(const.ATTR_ENABLED): cv.boolean,
                        vol.Optional(ATTR_NAME): cv.string,
                    }
                ),
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        await coordinator.async_update_config(data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": True})


class AlarmoAreaView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/area"
    name = "api:alarmo:area"

    mode_schema = vol.Schema(
        {
            vol.Required(const.ATTR_ENABLED): cv.boolean,
            vol.Required(const.ATTR_EXIT_TIME): vol.Any(cv.positive_int, None),
            vol.Required(const.ATTR_ENTRY_TIME): vol.Any(cv.positive_int, None),
            vol.Optional(const.ATTR_TRIGGER_TIME): vol.Any(cv.positive_int, None),
        }
    )

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Optional("area_id"): cv.string,
                vol.Optional(ATTR_NAME): cv.string,
                vol.Optional(const.ATTR_REMOVE): cv.boolean,
                vol.Optional(const.ATTR_MODES): vol.Schema(
                    {
                        vol.Optional(const.CONF_ALARM_ARMED_AWAY): mode_schema,
                        vol.Optional(const.CONF_ALARM_ARMED_HOME): mode_schema,
                        vol.Optional(const.CONF_ALARM_ARMED_NIGHT): mode_schema,
                        vol.Optional(const.CONF_ALARM_ARMED_CUSTOM_BYPASS): mode_schema,
                        vol.Optional(const.CONF_ALARM_ARMED_VACATION): mode_schema,
                    }
                ),
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        if "area_id" in data:
            area = data["area_id"]
            del data["area_id"]
        else:
            area = None
        await coordinator.async_update_area_config(area, data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": True})


class AlarmoSensorView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/sensors"
    name = "api:alarmo:sensors"

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): cv.entity_id,
                vol.Optional(const.ATTR_REMOVE): cv.boolean,
                vol.Optional(const.ATTR_TYPE): vol.In(SENSOR_TYPES),
                vol.Optional(const.ATTR_MODES): vol.All(
                    cv.ensure_list, [vol.In(const.ARM_MODES)]
                ),
                vol.Optional(ATTR_USE_EXIT_DELAY): cv.boolean,
                vol.Optional(ATTR_USE_ENTRY_DELAY): cv.boolean,
                vol.Optional(ATTR_ARM_ON_CLOSE): cv.boolean,
                vol.Optional(ATTR_ALLOW_OPEN): cv.boolean,
                vol.Optional(ATTR_ALWAYS_ON): cv.boolean,
                vol.Optional(ATTR_TRIGGER_UNAVAILABLE): cv.boolean,
                vol.Optional(ATTR_AUTO_BYPASS): cv.boolean,
                vol.Optional(ATTR_AUTO_BYPASS_MODES): vol.All(
                    cv.ensure_list, [vol.In(const.ARM_MODES)]
                ),
                vol.Optional(const.ATTR_AREA): cv.string,
                vol.Optional(const.ATTR_ENABLED): cv.boolean,
                vol.Optional(ATTR_GROUP): vol.Any(cv.string, None),
                vol.Optional(ATTR_ENTRY_DELAY): vol.Any(cv.positive_int, None),
                vol.Optional(ATTR_DELAY_ON): vol.Any(cv.positive_int, None),
                vol.Optional(ATTR_NEW_ENTITY_ID): cv.string,
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        entity = data[ATTR_ENTITY_ID]
        del data[ATTR_ENTITY_ID]
        coordinator.async_update_sensor_config(entity, data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": True})


class AlarmoUserView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/users"
    name = "api:alarmo:users"

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Optional(const.ATTR_USER_ID): cv.string,
                vol.Optional(const.ATTR_REMOVE): cv.boolean,
                vol.Optional(ATTR_NAME): cv.string,
                vol.Optional(const.ATTR_ENABLED): cv.boolean,
                vol.Optional(ATTR_CODE): cv.string,
                vol.Optional(const.ATTR_OLD_CODE): cv.string,
                vol.Optional(const.ATTR_CAN_ARM): cv.boolean,
                vol.Optional(const.ATTR_CAN_DISARM): cv.boolean,
                vol.Optional(const.ATTR_IS_OVERRIDE_CODE): cv.boolean,
                vol.Optional(const.ATTR_CAN_MANAGE_USERS): cv.boolean,
                vol.Optional(const.ATTR_HA_USER_ID): vol.Any(None, cv.string),
                vol.Optional(const.ATTR_AREA_LIMIT): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        user_id = None
        if const.ATTR_USER_ID in data:
            user_id = data[const.ATTR_USER_ID]
            del data[const.ATTR_USER_ID]
        err = await coordinator.async_update_user_config(user_id, data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": not isinstance(err, str), "error": err})


class AlarmoAutomationView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/automations"
    name = "api:alarmo:automations"

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Optional(const.ATTR_AUTOMATION_ID): cv.string,
                vol.Optional(ATTR_NAME): cv.string,
                vol.Optional(const.ATTR_TYPE): cv.string,
                vol.Optional(const.ATTR_TRIGGERS): vol.All(
                    cv.ensure_list,
                    [
                        vol.Any(
                            vol.Schema(
                                {
                                    vol.Required(const.ATTR_EVENT): cv.string,
                                    vol.Optional(const.ATTR_AREA): vol.Any(
                                        int,
                                        cv.string,
                                    ),
                                    vol.Optional(const.ATTR_MODES): vol.All(
                                        cv.ensure_list, [vol.In(const.ARM_MODES)]
                                    ),
                                }
                            ),
                            vol.Schema(
                                {
                                    vol.Required(ATTR_ENTITY_ID): cv.string,
                                    vol.Required(ATTR_STATE): cv.string,
                                }
                            ),
                        )
                    ],
                ),
                vol.Optional(const.ATTR_ACTIONS): vol.All(
                    cv.ensure_list,
                    [
                        vol.Schema(
                            {
                                vol.Optional(ATTR_ENTITY_ID): cv.string,
                                vol.Required(ATTR_SERVICE): cv.string,
                                vol.Optional(CONF_SERVICE_DATA): dict,
                            }
                        )
                    ],
                ),
                vol.Optional(const.ATTR_ENABLED): cv.boolean,
                vol.Optional(const.ATTR_REMOVE): cv.boolean,
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        automation_id = None
        if const.ATTR_AUTOMATION_ID in data:
            automation_id = data[const.ATTR_AUTOMATION_ID]
            del data[const.ATTR_AUTOMATION_ID]
        coordinator.async_update_automation_config(automation_id, data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": True})


class AlarmoSensorGroupView(HomeAssistantView):
    """Login to Home Assistant cloud."""

    url = "/api/alarmo/sensor_groups"
    name = "api:alarmo:sensor_groups"

    @require_admin
    @RequestDataValidator(
        vol.Schema(
            {
                vol.Optional(ATTR_GROUP_ID): cv.string,
                vol.Optional(ATTR_NAME): cv.string,
                vol.Optional(ATTR_ENTITIES): vol.All(
                    cv.ensure_list, vol.Unique(), [cv.string]
                ),
                vol.Optional(ATTR_TIMEOUT): cv.positive_int,
                vol.Optional(ATTR_EVENT_COUNT): cv.positive_int,
                vol.Optional(const.ATTR_REMOVE): cv.boolean,
            }
        )
    )
    async def post(self, request, data):
        """Handle config update request."""
        hass = request.app["hass"]
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        group_id = None
        if ATTR_GROUP_ID in data:
            group_id = data[ATTR_GROUP_ID]
            del data[ATTR_GROUP_ID]
        coordinator.async_update_sensor_group_config(group_id, data)
        async_dispatcher_send(hass, "alarmo_update_frontend")
        return self.json({"success": True})


@callback
def websocket_get_config(hass, connection, msg):
    """Publish config data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    config = coordinator.store.async_get_config()
    connection.send_result(msg["id"], config)


@callback
@websocket_api.require_admin
def websocket_get_areas(hass, connection, msg):
    """Publish area data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    areas = coordinator.store.async_get_areas()
    connection.send_result(msg["id"], areas)


@callback
@websocket_api.require_admin
def websocket_get_sensors(hass, connection, msg):
    """Publish sensor data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    sensors = coordinator.store.async_get_sensors()
    for entity_id in sensors.keys():
        group = coordinator.async_get_group_for_sensor(entity_id)
        sensors[entity_id]["group"] = group
    connection.send_result(msg["id"], sensors)


@callback
@websocket_api.require_admin
def websocket_get_users(hass, connection, msg):
    """Publish user data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    users = coordinator.store.async_get_users()
    connection.send_result(msg["id"], users)


@callback
@websocket_api.require_admin
def websocket_get_automations(hass, connection, msg):
    """Publish automations data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    automations = coordinator.store.async_get_automations()
    connection.send_result(msg["id"], automations)


@callback
def websocket_get_alarm_entities(hass, connection, msg):
    """Publish alarm entity data."""
    result = [
        {"entity_id": entity.entity_id, "area_id": area_id}
        for (area_id, entity) in hass.data[const.DOMAIN]["areas"].items()
    ]
    if hass.data[const.DOMAIN]["master"]:
        result.append(
            {"entity_id": hass.data[const.DOMAIN]["master"].entity_id, "area_id": 0}
        )
    connection.send_result(msg["id"], result)


@callback
@websocket_api.require_admin
def websocket_get_sensor_groups(hass, connection, msg):
    """Publish sensor_group data."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    groups = coordinator.store.async_get_sensor_groups()
    connection.send_result(msg["id"], groups)


@callback
def websocket_get_countdown(hass, connection, msg):
    """Publish countdown time for alarm entity."""
    entity_id = msg["entity_id"]
    item = next(
        (
            entity
            for entity in hass.data[const.DOMAIN]["areas"].values()
            if entity.entity_id == entity_id
        ),
        None,
    )
    if (
        hass.data[const.DOMAIN]["master"]
        and not item
        and hass.data[const.DOMAIN]["master"].entity_id == entity_id
    ):
        item = hass.data[const.DOMAIN]["master"]

    data = {
        "delay": item.delay if item else 0,
        "remaining": round((item.expiration - dt_util.utcnow()).total_seconds(), 2)
        if item and item.expiration
        else 0,
    }
    connection.send_result(msg["id"], data)


@callback
def websocket_get_ready_to_arm_modes(hass, connection, msg):
    """Publish ready_to_arm_modes for alarm entity."""
    entity_id = msg["entity_id"]
    item = next(
        (
            entity
            for entity in hass.data[const.DOMAIN]["areas"].values()
            if entity.entity_id == entity_id
        ),
        None,
    )
    if (
        hass.data[const.DOMAIN]["master"]
        and not item
        and hass.data[const.DOMAIN]["master"].entity_id == entity_id
    ):
        item = hass.data[const.DOMAIN]["master"]

    data = {"modes": item._ready_to_arm_modes if item else None}
    connection.send_result(msg["id"], data)


_LOGGER = logging.getLogger(__name__)

NEW_CODE = vol.All(cv.string, vol.Length(min=4))
FREE_FAILURES = 3
LONGEST_WAIT = 60
NEW_CODES_PER_DAY = 20
DAY = 24 * 3600
CODE_ERRORS = {
    "User with same code already exists": "code_in_use",
    "User with same name already exists": "name_in_use",
    "Home Assistant user not found": "account_not_found",
    "Home Assistant user already linked to another user": "account_in_use",
    "A user who manages users needs a Home Assistant user": "needs_account",
}


def _now():
    return time.monotonic()


class CodeGuard:
    """Slow down guessing codes: one check at a time, and longer waits after misses."""

    def __init__(self):
        """Start with no wrong code."""
        self.failures = 0
        self.not_before = 0.0
        self.new_codes = collections.deque()
        self.lock = asyncio.Lock()

    def allows(self):
        """Whether a code may be checked now."""
        return _now() >= self.not_before

    def failed(self):
        """Count a wrong code; from the third, wait 1, 2, 4... seconds, up to 60."""
        self.failures += 1
        if self.failures >= FREE_FAILURES:
            wait = min(2 ** (self.failures - FREE_FAILURES), LONGEST_WAIT)
            self.not_before = _now() + wait
        _LOGGER.warning("Wrong alarm code given %s times in a row", self.failures)

    def takes_new_code(self):
        """Count a new code tried, taken or not; refuse past the day's limit."""
        now = _now()
        while self.new_codes and self.new_codes[0] <= now - DAY:
            self.new_codes.popleft()
        if len(self.new_codes) >= NEW_CODES_PER_DAY:
            _LOGGER.warning("Too many new alarm codes tried in a day")
            return False
        self.new_codes.append(now)
        return True

    def passed(self):
        """Forget the wrong codes before a right one."""
        self.failures = 0
        self.not_before = 0.0


def _guard(hass, connection):
    """Return the guard the code checks of this Home Assistant user go through."""
    guards = hass.data[const.DOMAIN].setdefault("code_guards", {})
    return guards.setdefault(connection.user.id, CodeGuard())


def _held_back(connection, msg, guard):
    """Answer that codes cannot be checked yet, when they cannot."""
    if guard.allows():
        return False
    connection.send_error(
        msg["id"], "too_many_attempts", "Too many wrong codes, try later"
    )
    return True


def _spent_new_codes(connection, msg, guard):
    """Answer that no more new codes can be tried today, when they cannot."""
    if guard.takes_new_code():
        return False
    connection.send_error(
        msg["id"], "too_many_new_codes", "Too many new codes tried, try later"
    )
    return True


def _linked_user(coordinator, connection):
    """Return the enabled user linked to this Home Assistant user, if any."""
    for user in coordinator.store.async_get_users().values():
        if (
            user[const.ATTR_HA_USER_ID] == connection.user.id
            and user[const.ATTR_ENABLED]
        ):
            return user
    return None


def _managers(coordinator):
    """Return the ids of the users who can manage users from a session."""
    return [
        user[const.ATTR_USER_ID]
        for user in coordinator.store.async_get_users().values()
        if user[const.ATTR_CAN_MANAGE_USERS]
        and user[const.ATTR_HA_USER_ID]
        and user[const.ATTR_ENABLED]
        and user[ATTR_CODE]
    ]


async def _async_people(hass, coordinator):
    """Return the users and the accounts they can be linked to, never a code."""
    accounts = [
        {"id": account.id, ATTR_NAME: account.name}
        for account in await hass.auth.async_get_users()
        if not account.system_generated and account.is_active
    ]
    users = [
        {
            const.ATTR_USER_ID: user[const.ATTR_USER_ID],
            ATTR_NAME: user[ATTR_NAME],
            const.ATTR_ENABLED: user[const.ATTR_ENABLED],
            const.ATTR_CAN_MANAGE_USERS: user[const.ATTR_CAN_MANAGE_USERS],
            const.ATTR_HA_USER_ID: user[const.ATTR_HA_USER_ID],
        }
        for user in coordinator.store.async_get_users().values()
    ]
    return {"users": users, "accounts": accounts}


@decorators.websocket_command({vol.Required("type"): "alarmo/my_user"})
@callback
def websocket_get_my_user(hass, connection, msg):
    """Publish the user linked to this Home Assistant user, or None."""
    user = _linked_user(hass.data[const.DOMAIN]["coordinator"], connection)
    connection.send_result(
        msg["id"],
        user
        and {
            const.ATTR_USER_ID: user[const.ATTR_USER_ID],
            ATTR_NAME: user[ATTR_NAME],
            const.ATTR_CAN_MANAGE_USERS: user[const.ATTR_CAN_MANAGE_USERS],
        },
    )


async def _async_code_in_use(coordinator, code, user_id):
    """Whether the code belongs to another user than this one."""
    owner = await coordinator.async_authenticate_user(code)
    return bool(owner) and owner[const.ATTR_USER_ID] != user_id


@decorators.websocket_command(
    {
        vol.Required("type"): "alarmo/change_own_code",
        vol.Required(const.ATTR_OLD_CODE): vol.All(cv.string, vol.Length(min=1)),
        vol.Required(ATTR_CODE): NEW_CODE,
    }
)
@decorators.async_response
async def websocket_change_own_code(hass, connection, msg):
    """Change the code of the user linked to this Home Assistant user."""
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    user = _linked_user(coordinator, connection)
    if not user:
        connection.send_error(
            msg["id"], "not_linked", "No user is linked to this account"
        )
        return
    user_id = user[const.ATTR_USER_ID]
    guard = _guard(hass, connection)
    # Held across the check, so codes sent together are counted one by one.
    async with guard.lock:
        if _held_back(connection, msg, guard):
            return
        if not await coordinator.async_authenticate_user(
            msg[const.ATTR_OLD_CODE], user_id
        ):
            guard.failed()
            connection.send_error(msg["id"], "wrong_code", "Wrong code provided")
            return
        if _spent_new_codes(connection, msg, guard):
            return
        # Someone else's code, as a guess at it would be.
        if await _async_code_in_use(coordinator, msg[ATTR_CODE], user_id):
            guard.failed()
            connection.send_error(
                msg["id"], "code_in_use", "User with same code already exists"
            )
            return
        guard.passed()
        coordinator.store.async_update_user(
            user_id, await coordinator.async_hash_code(msg[ATTR_CODE])
        )
    async_dispatcher_send(hass, "alarmo_update_frontend")
    connection.send_result(msg["id"])


def _manager(hass, connection, msg):
    """Return the linked user if they manage users; when not, say so."""
    user = _linked_user(hass.data[const.DOMAIN]["coordinator"], connection)
    if not user or not user[const.ATTR_CAN_MANAGE_USERS]:
        connection.send_error(
            msg["id"], "not_manager", "This account does not manage users"
        )
        return None
    return user


@decorators.websocket_command({vol.Required("type"): "alarmo/people"})
@decorators.async_response
async def websocket_get_people(hass, connection, msg):
    """Publish the users to a Home Assistant user who manages them."""
    if _manager(hass, connection, msg):
        coordinator = hass.data[const.DOMAIN]["coordinator"]
        connection.send_result(msg["id"], await _async_people(hass, coordinator))


def _leaves_no_manager(coordinator, user_id):
    """Whether this user is the only one who manages users."""
    return _managers(coordinator) == [user_id]


async def _async_add_person(coordinator, msg):
    if ATTR_NAME not in msg or "new_code" not in msg:
        return "invalid_format", "A name and a code are needed"
    data = {
        ATTR_NAME: msg[ATTR_NAME],
        ATTR_CODE: msg["new_code"],
        const.ATTR_CAN_ARM: True,
        const.ATTR_CAN_DISARM: True,
    }
    if msg.get(const.ATTR_HA_USER_ID):
        data[const.ATTR_HA_USER_ID] = msg[const.ATTR_HA_USER_ID]
    err = await coordinator.async_update_user_config(None, data)
    return (CODE_ERRORS.get(err, "invalid"), err) if err else None


async def _async_set_code(coordinator, msg):
    if "new_code" not in msg:
        return "invalid_format", "A code is needed"
    if await _async_code_in_use(coordinator, msg["new_code"], msg[const.ATTR_USER_ID]):
        return "code_in_use", "User with same code already exists"
    coordinator.store.async_update_user(
        msg[const.ATTR_USER_ID], await coordinator.async_hash_code(msg["new_code"])
    )
    return None


async def _async_set_manager(coordinator, msg):
    if const.ATTR_CAN_MANAGE_USERS not in msg:
        return "invalid_format", "Whether they manage users is needed"
    manages = msg[const.ATTR_CAN_MANAGE_USERS]
    if not manages and _leaves_no_manager(coordinator, msg[const.ATTR_USER_ID]):
        return "last_manager", "Someone has to manage users"
    err = await coordinator.async_update_user_config(
        msg[const.ATTR_USER_ID], {const.ATTR_CAN_MANAGE_USERS: manages}
    )
    return (CODE_ERRORS.get(err, "invalid"), err) if err else None


async def _async_link(coordinator, msg):
    if const.ATTR_HA_USER_ID not in msg:
        return "invalid_format", "A Home Assistant user, or none, is needed"
    account = msg[const.ATTR_HA_USER_ID]
    if not account and _leaves_no_manager(coordinator, msg[const.ATTR_USER_ID]):
        return "last_manager", "Someone has to manage users"
    changes = {const.ATTR_HA_USER_ID: account}
    if not account:
        changes[const.ATTR_CAN_MANAGE_USERS] = False
    err = await coordinator.async_update_user_config(msg[const.ATTR_USER_ID], changes)
    return (CODE_ERRORS.get(err, "invalid"), err) if err else None


async def _async_remove_person(coordinator, msg):
    if _leaves_no_manager(coordinator, msg[const.ATTR_USER_ID]):
        return "last_manager", "Someone has to manage users"
    coordinator.store.async_delete_user(msg[const.ATTR_USER_ID])
    return None


PEOPLE_ACTIONS = {
    "add": _async_add_person,
    "set_code": _async_set_code,
    "set_manager": _async_set_manager,
    "link": _async_link,
    "remove": _async_remove_person,
}
CODE_ACTION_ERRORS = ("code_in_use",)


@decorators.websocket_command(
    {
        vol.Required("type"): "alarmo/manage_people",
        vol.Required(ATTR_CODE): vol.All(cv.string, vol.Length(min=1)),
        vol.Required("action"): vol.In(list(PEOPLE_ACTIONS)),
        vol.Optional(const.ATTR_USER_ID): cv.string,
        vol.Optional(ATTR_NAME): vol.All(cv.string, vol.Strip, vol.Length(min=1)),
        vol.Optional("new_code"): NEW_CODE,
        vol.Optional(const.ATTR_CAN_MANAGE_USERS): cv.boolean,
        vol.Optional(const.ATTR_HA_USER_ID): vol.Any(None, cv.string),
    }
)
@decorators.async_response
async def websocket_manage_people(hass, connection, msg):
    """Add, change or remove a user, for a manager who confirms with their code."""
    manager = _manager(hass, connection, msg)
    if not manager:
        return
    coordinator = hass.data[const.DOMAIN]["coordinator"]
    if msg["action"] != "add" and not coordinator.store.async_get_user(
        msg.get(const.ATTR_USER_ID)
    ):
        connection.send_error(msg["id"], "not_found", "No such user")
        return
    guard = _guard(hass, connection)
    async with guard.lock:
        if _held_back(connection, msg, guard):
            return
        if not await coordinator.async_authenticate_user(
            msg[ATTR_CODE], manager[const.ATTR_USER_ID]
        ):
            guard.failed()
            connection.send_error(msg["id"], "wrong_code", "Wrong code provided")
            return
        if "new_code" in msg and _spent_new_codes(connection, msg, guard):
            return
        err = await PEOPLE_ACTIONS[msg["action"]](coordinator, msg)
        if err and err[0] in CODE_ACTION_ERRORS:
            guard.failed()
        else:
            guard.passed()
    if err:
        connection.send_error(msg["id"], *err)
        return
    async_dispatcher_send(hass, "alarmo_update_frontend")
    connection.send_result(msg["id"], await _async_people(hass, coordinator))


async def async_register_websockets(hass):
    """Register websocket handlers."""
    hass.http.register_view(AlarmoConfigView)
    hass.http.register_view(AlarmoSensorView)
    hass.http.register_view(AlarmoUserView)
    hass.http.register_view(AlarmoAutomationView)
    hass.http.register_view(AlarmoAreaView)
    hass.http.register_view(AlarmoSensorGroupView)

    async_register_command(hass, handle_subscribe_updates)
    async_register_command(hass, websocket_get_my_user)
    async_register_command(hass, websocket_change_own_code)
    async_register_command(hass, websocket_get_people)
    async_register_command(hass, websocket_manage_people)

    async_register_command(
        hass,
        "alarmo/config",
        websocket_get_config,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/config"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/areas",
        websocket_get_areas,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/areas"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/sensors",
        websocket_get_sensors,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/sensors"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/users",
        websocket_get_users,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/users"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/automations",
        websocket_get_automations,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/automations"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/entities",
        websocket_get_alarm_entities,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/entities"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/sensor_groups",
        websocket_get_sensor_groups,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {vol.Required("type"): "alarmo/sensor_groups"}
        ),
    )
    async_register_command(
        hass,
        "alarmo/countdown",
        websocket_get_countdown,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {
                vol.Required("type"): "alarmo/countdown",
                vol.Required("entity_id"): cv.entity_id,
            }
        ),
    )
    async_register_command(
        hass,
        "alarmo/ready_to_arm_modes",
        websocket_get_ready_to_arm_modes,
        websocket_api.BASE_COMMAND_MESSAGE_SCHEMA.extend(
            {
                vol.Required("type"): "alarmo/ready_to_arm_modes",
                vol.Required("entity_id"): cv.entity_id,
            }
        ),
    )
