"""Test arming with force (bypass_open_sensors).

Force bypasses only what is not ready when arming is requested. Whatever is
not ready only at the end of the exit delay gets the treatment it gets without
force, and a sensor with allow_open is never bypassed.
"""

from typing import Any

import pytest
from homeassistant.core import State
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    async_capture_events,
)

from tests.helpers import (
    advance_time,
    cleanup_timers,
    assert_alarm_state,
    setup_alarmo_entry,
    patch_alarmo_integration_dependencies,
)
from tests.factories import AreaFactory, SensorFactory

ALARM_ENTITY = "alarm_control_panel.test_area_1"
DOOR = "binary_sensor.generic_area_1_door_sensor"
WINDOW = "binary_sensor.generic_area_1_window_sensor"
MOTION = "binary_sensor.generic_area_1_motion_sensor"
EXIT_TIME = 10


@pytest.fixture(autouse=True)
def expected_lingering_timers():
    """Fix expected lingering timers for tests."""
    return True


async def _setup(
    hass: Any,
    sensors: list[dict[str, Any]],
    exit_time: int = EXIT_TIME,
    **area: Any,
):
    area = AreaFactory.create_area(
        area_id="area_1", modes=["armed_away"], armed_away_exit_time=exit_time, **area
    )
    storage, entry = setup_alarmo_entry(
        hass, areas=[area], sensors=sensors, entry_id="test_force_arming"
    )
    return storage, entry


async def _set(hass: Any, entity_id: str, state: str) -> None:
    hass.states.async_set(entity_id, state)
    await hass.async_block_till_done()


async def _arm(hass: Any, force: bool = True) -> None:
    await hass.services.async_call(
        "alarmo",
        "arm",
        {"entity_id": ALARM_ENTITY, "code": "1234", "force": force},
        blocking=True,
    )
    await hass.async_block_till_done()


async def _disarm(hass: Any) -> None:
    await hass.services.async_call(
        "alarmo",
        "disarm",
        {"entity_id": ALARM_ENTITY, "code": "1234"},
        blocking=True,
    )
    await hass.async_block_till_done()


def _bypassed(hass: Any) -> list[str]:
    return hass.states.get(ALARM_ENTITY).attributes.get("bypassed_sensors") or []


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_time", [0, EXIT_TIME])
async def test_force_bypasses_door_open_at_request_until_disarm(
    hass: Any, enable_custom_integrations: Any, exit_time: int
) -> None:
    """A door open at the request is bypassed until disarm, as before."""
    storage, entry = await _setup(
        hass, [SensorFactory.create_door_sensor(entity_id=DOOR)], exit_time
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, DOOR, "on")

        await _arm(hass)
        await advance_time(hass, exit_time + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [DOOR]

        await _set(hass, DOOR, "off")
        await _set(hass, DOOR, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [DOOR]

        await _disarm(hass)
        assert_alarm_state(hass, ALARM_ENTITY, "disarmed")
        assert _bypassed(hass) == []
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_force_watches_door_open_at_request_and_closed_at_end(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """A door open at the request but closed when the exit delay ends is watched."""
    storage, entry = await _setup(
        hass,
        [SensorFactory.create_door_sensor(entity_id=DOOR, use_entry_delay=False)],
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, DOOR, "on")

        await _arm(hass)
        assert_alarm_state(hass, ALARM_ENTITY, "arming")
        await _set(hass, DOOR, "off")
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == []

        await _set(hass, DOOR, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "triggered")
        await cleanup_timers(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exit_time", "on_at_request"),
    [(0, True), (EXIT_TIME, True), (EXIT_TIME, False)],
)
async def test_force_does_not_bypass_allow_open_motion(
    hass: Any, enable_custom_integrations: Any, exit_time: int, on_at_request: bool
) -> None:
    """An allow_open motion sensor on when armed is ignored only until it clears."""
    storage, entry = await _setup(
        hass,
        [
            SensorFactory.create_motion_sensor(
                entity_id=MOTION, allow_open=True, use_entry_delay=False
            )
        ],
        exit_time,
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, MOTION, "on" if on_at_request else "off")

        await _arm(hass)
        await _set(hass, MOTION, "on")
        await advance_time(hass, exit_time + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == []

        await _set(hass, MOTION, "off")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        await _set(hass, MOTION, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "triggered")
        await cleanup_timers(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sensor",
    [
        SensorFactory.create_door_sensor(entity_id=DOOR),
        SensorFactory.create_motion_sensor(entity_id=MOTION),
    ],
    ids=["door", "motion_without_allow_open"],
)
async def test_force_fails_when_sensor_ready_at_request_is_not_at_end(
    hass: Any, enable_custom_integrations: Any, sensor: dict[str, Any]
) -> None:
    """A sensor ready at the request but not at the end fails arming, as without force."""  # noqa: E501
    entity = sensor["entity_id"]
    window = SensorFactory.create_window_sensor(entity_id=WINDOW)
    storage, entry = await _setup(hass, [sensor, window])
    failed = async_capture_events(hass, "alarmo_failed_to_arm")
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, entity, "off")
        await _set(hass, WINDOW, "on")

        await _arm(hass)
        assert_alarm_state(hass, ALARM_ENTITY, "arming")
        await _set(hass, entity, "on")
        await advance_time(hass, EXIT_TIME + 1)

        assert_alarm_state(hass, ALARM_ENTITY, "disarmed")
        assert _bypassed(hass) == []
        assert len(failed) == 1
        assert failed[0].data["sensors"] == [entity]
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_restart_while_arming_ends_as_without_force(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """A restart during the exit delay ends it as without force, back to disarmed.

    Force is only kept in memory while arming, so a restart forgets it, as it
    always did: the exit delay starts again and a door still open at its end
    fails arming with alarmo_failed_to_arm, instead of leaving the alarm stuck
    in arming. Nothing is left unwatched that was not confirmed after the
    restart.
    """
    storage, entry = await _setup(
        hass, [SensorFactory.create_door_sensor(entity_id=DOOR)]
    )
    mock_restore_cache(
        hass, [State(ALARM_ENTITY, "arming", {"arm_mode": "armed_away"})]
    )
    failed = async_capture_events(hass, "alarmo_failed_to_arm")
    with patch_alarmo_integration_dependencies(storage):
        await _set(hass, DOOR, "on")
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert_alarm_state(hass, ALARM_ENTITY, "arming")

        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "disarmed")
        assert _bypassed(hass) == []
        assert len(failed) == 1
        assert failed[0].data["sensors"] == [DOOR]
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_restart_while_armed_keeps_door_bypassed_by_force(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """A door bypassed by force stays bypassed after a restart while armed."""
    storage, entry = await _setup(
        hass, [SensorFactory.create_door_sensor(entity_id=DOOR)]
    )
    mock_restore_cache(
        hass,
        [
            State(
                ALARM_ENTITY,
                "armed_away",
                {"arm_mode": "armed_away", "bypassed_sensors": [DOOR]},
            )
        ],
    )
    with patch_alarmo_integration_dependencies(storage):
        await _set(hass, DOOR, "on")
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [DOOR]

        await _set(hass, DOOR, "off")
        await _set(hass, DOOR, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        await cleanup_timers(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_auto_bypass_still_applies_at_end_of_exit_delay(
    hass: Any, enable_custom_integrations: Any, force: bool
) -> None:
    """An auto_bypass door opened during the exit delay is bypassed, force or not."""
    storage, entry = await _setup(
        hass,
        [
            SensorFactory.create_door_sensor(
                entity_id=DOOR, auto_bypass=True, auto_bypass_modes=["armed_away"]
            )
        ],
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, DOOR, "off")

        await _arm(hass, force=force)
        await _set(hass, DOOR, "on")
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [DOOR]
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_auto_bypass_does_not_bypass_allow_open_motion(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """An allow_open motion sensor with auto_bypass is ignored only until it clears."""
    storage, entry = await _setup(
        hass,
        [
            SensorFactory.create_motion_sensor(
                entity_id=MOTION,
                allow_open=True,
                auto_bypass=True,
                auto_bypass_modes=["armed_away"],
                use_entry_delay=False,
            )
        ],
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, MOTION, "on")

        await _arm(hass, force=False)
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == []

        await _set(hass, MOTION, "off")
        await _set(hass, MOTION, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "triggered")
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_restart_while_arming_still_needs_the_code_to_disarm(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """An arming restored after a restart may be a switch from armed: keep the code."""
    storage, entry = await _setup(hass, [])
    mock_restore_cache(
        hass, [State(ALARM_ENTITY, "arming", {"arm_mode": "armed_away"})]
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert_alarm_state(hass, ALARM_ENTITY, "arming")

        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": ALARM_ENTITY},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert_alarm_state(hass, ALARM_ENTITY, "arming")
        await cleanup_timers(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("clears", [True, False])
async def test_force_does_not_cover_motion_only_detecting_at_request(
    hass: Any, enable_custom_integrations: Any, clears: bool
) -> None:
    """Motion detecting at the request, allowed in the exit delay, is not forced.

    The window is what force is for. The motion sensor is watched once it
    clears, and still detecting at the end it fails arming, as without force.
    """
    storage, entry = await _setup(
        hass,
        [
            SensorFactory.create_window_sensor(entity_id=WINDOW),
            SensorFactory.create_motion_sensor(entity_id=MOTION, use_entry_delay=False),
        ],
    )
    failed = async_capture_events(hass, "alarmo_failed_to_arm")
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, WINDOW, "on")
        await _set(hass, MOTION, "on")

        await _arm(hass)
        if clears:
            await _set(hass, MOTION, "off")
        await advance_time(hass, EXIT_TIME + 1)

        if clears:
            assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
            assert _bypassed(hass) == [WINDOW]
            await _set(hass, MOTION, "on")
            await advance_time(hass, 1)
            assert_alarm_state(hass, ALARM_ENTITY, "triggered")
        else:
            assert_alarm_state(hass, ALARM_ENTITY, "disarmed")
            assert failed[0].data["sensors"] == [MOTION]
        await cleanup_timers(hass)


@pytest.mark.asyncio
async def test_force_covers_motion_blocking_the_request(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """Motion without exit delay detecting at the request is what force is for."""
    storage, entry = await _setup(
        hass,
        [SensorFactory.create_motion_sensor(entity_id=MOTION, use_exit_delay=False)],
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, MOTION, "on")

        await _arm(hass)
        await advance_time(hass, EXIT_TIME + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [MOTION]
        await cleanup_timers(hass)


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_time", [0, EXIT_TIME])
async def test_rearm_after_trigger_does_not_force_the_intrusion(
    hass: Any, enable_custom_integrations: Any, exit_time: int
) -> None:
    """Re-arming after the trigger time does not bypass the door that triggered."""
    storage, entry = await _setup(
        hass,
        [
            SensorFactory.create_window_sensor(entity_id=WINDOW),
            SensorFactory.create_door_sensor(entity_id=DOOR, use_entry_delay=False),
        ],
        exit_time,
        armed_away_trigger_time=5,
        ignore_blocking_sensors_after_trigger=True,
    )
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _set(hass, WINDOW, "on")
        await _set(hass, DOOR, "off")

        await _arm(hass)
        await advance_time(hass, exit_time + 1)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        await _set(hass, DOOR, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "triggered")

        await advance_time(hass, 6)
        assert_alarm_state(hass, ALARM_ENTITY, "armed_away")
        assert _bypassed(hass) == [WINDOW]
        await _set(hass, DOOR, "off")
        await _set(hass, DOOR, "on")
        await advance_time(hass, 1)
        assert_alarm_state(hass, ALARM_ENTITY, "triggered")
        await cleanup_timers(hass)
