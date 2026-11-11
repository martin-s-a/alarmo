"""Test users managing codes from a Home Assistant user linked to them."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.setup import async_setup_component

from tests.helpers import make_alarmo_entry, patch_alarmo_integration_dependencies
from tests.factories import AreaFactory, UserFactory
from custom_components.alarmo import websockets
from custom_components.alarmo.store import AlarmoStorage


async def setup_with_users(hass: Any, users: list[dict[str, Any]]) -> Any:
    """Set up Alarmo on a real store holding these users; return the coordinator."""
    storage = AlarmoStorage(hass)
    stored = {
        "config": {
            "code_arm_required": False,
            "code_mode_change_required": False,
            "code_disarm_required": False,
            "code_format": "number",
            "disarm_after_trigger": False,
        },
        "areas": [AreaFactory.create_area(area_id="area_1", name="Test Area 1")],
        "users": users,
    }
    with patch.object(storage._store, "async_load", AsyncMock(return_value=stored)):
        await storage.async_load()
    storage.async_schedule_save = lambda: None
    entry = make_alarmo_entry(entry_id="test_user_management")
    entry.add_to_hass(hass)
    with patch_alarmo_integration_dependencies(storage):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert await async_setup_component(hass, "websocket_api", {})
    await websockets.async_register_websockets(hass)
    return hass.data["alarmo"]["coordinator"]


def user(user_id: str, code: str, account: Any = None, manages: bool = False):
    """Return a stored user, linked to a Home Assistant user when given one."""
    return {
        **UserFactory.create_user(user_id=user_id, name=user_id.title(), code=code),
        "area_limit": [],
        "can_manage_users": manages,
        "ha_user_id": account.id if account else None,
    }


async def connect(hass: Any, hass_ws_client: Any, account: Any) -> Any:
    """Return a websocket client logged in as this Home Assistant user."""
    refresh_token = await hass.auth.async_create_refresh_token(account, "client")
    return await hass_ws_client(
        hass, hass.auth.async_create_access_token(refresh_token)
    )


class Caller:
    """Send commands over one websocket connection."""

    def __init__(self, client: Any) -> None:
        """Wrap the client."""
        self.client = client
        self.next_id = 1

    async def __call__(self, command: str, **data: Any) -> dict[str, Any]:
        """Send a command and return its answer."""
        self.next_id += 1
        await self.client.send_json({"id": self.next_id, "type": command, **data})
        return await self.client.receive_json()


async def owner(coordinator: Any, code: str) -> str | None:
    """Return the id of the user the code belongs to."""
    found = await coordinator.async_authenticate_user(code)
    return found["user_id"] if found else None


@pytest.mark.asyncio
async def test_my_user(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A Home Assistant user sees the user linked to them, and nobody else."""
    ana = await hass.auth.async_create_user("Ana")
    guest = await hass.auth.async_create_user("Guest")
    await setup_with_users(hass, [user("ana", "1234", ana, manages=True)])

    answer = await Caller(await connect(hass, hass_ws_client, ana))("alarmo/my_user")
    assert answer["result"] == {
        "user_id": "ana",
        "name": "Ana",
        "can_manage_users": True,
    }
    answer = await Caller(await connect(hass, hass_ws_client, guest))("alarmo/my_user")
    assert answer["result"] is None


@pytest.mark.asyncio
async def test_change_own_code(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A linked user changes their code with the current one."""
    ana = await hass.auth.async_create_user("Ana")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    answer = await call("alarmo/change_own_code", old_code="1234", code="4321")
    assert answer["success"]
    assert await owner(coordinator, "4321") == "ana"
    assert await owner(coordinator, "1234") is None


@pytest.mark.asyncio
async def test_change_own_code_refused(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """Only the linked user's own current code changes it, never to another's."""
    ana = await hass.auth.async_create_user("Ana")
    guest = await hass.auth.async_create_user("Guest")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    answer = await call("alarmo/change_own_code", old_code="5678", code="4321")
    assert answer["error"]["code"] == "wrong_code"
    answer = await call("alarmo/change_own_code", old_code="1234", code="5678")
    assert answer["error"]["code"] == "code_in_use"
    answer = await Caller(await connect(hass, hass_ws_client, guest))(
        "alarmo/change_own_code", old_code="5678", code="4321"
    )
    assert answer["error"]["code"] == "not_linked"
    assert await owner(coordinator, "1234") == "ana"
    assert await owner(coordinator, "5678") == "bob"
    assert await owner(coordinator, "4321") is None


@pytest.mark.asyncio
async def test_wrong_codes_are_held_back(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """From the third wrong code in a row, the next check waits, per account."""
    ana = await hass.auth.async_create_user("Ana")
    bob = await hass.auth.async_create_user("Bob")
    await setup_with_users(hass, [user("ana", "1234", ana), user("bob", "5678", bob)])
    call = Caller(await connect(hass, hass_ws_client, ana))

    for _ in range(3):
        answer = await call("alarmo/change_own_code", old_code="0000", code="4321")
        assert answer["error"]["code"] == "wrong_code"
    answer = await call("alarmo/change_own_code", old_code="1234", code="4321")
    assert answer["error"]["code"] == "too_many_attempts"

    answer = await Caller(await connect(hass, hass_ws_client, bob))(
        "alarmo/change_own_code", old_code="5678", code="8765"
    )
    assert answer["success"]


@pytest.mark.asyncio
async def test_new_codes_are_limited_per_day(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """Each new code tried counts, taken or free, so codes can't be enumerated."""
    ana = await hass.auth.async_create_user("Ana")
    bob = await hass.auth.async_create_user("Bob")
    coordinator = await setup_with_users(
        hass, [user("ana", "1000", ana), user("bob", "5678", bob)]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))
    last = str(1000 + websockets.NEW_CODES_PER_DAY - 1)
    beyond = str(1000 + websockets.NEW_CODES_PER_DAY)
    now = 1000.0

    with patch.object(websockets, "_now", lambda: now):
        answer = await call("alarmo/change_own_code", old_code="1000", code="5678")
        assert answer["error"]["code"] == "code_in_use"
        for code in range(1001, int(beyond)):
            answer = await call(
                "alarmo/change_own_code", old_code=str(code - 1), code=str(code)
            )
            assert answer["success"]
        answer = await call("alarmo/change_own_code", old_code=last, code=beyond)
        assert answer["error"]["code"] == "too_many_new_codes"
        assert await owner(coordinator, last) == "ana"

        answer = await Caller(await connect(hass, hass_ws_client, bob))(
            "alarmo/change_own_code", old_code="5678", code="8765"
        )
        assert answer["success"]

        now += 24 * 3600
        answer = await call("alarmo/change_own_code", old_code=last, code=beyond)
        assert answer["success"]


@pytest.mark.asyncio
async def test_new_codes_a_manager_sets_count_too(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """Adding a user or setting a code spends the manager's new codes of the day."""
    ana = await hass.auth.async_create_user("Ana")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana, manages=True), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))
    last = str(2000 + websockets.NEW_CODES_PER_DAY - 1)

    with patch.object(websockets, "_now", lambda: 1000.0):
        answer = await call(
            "alarmo/manage_people",
            code="1234",
            action="add",
            name="Eve",
            new_code="2000",
        )
        assert answer["success"]
        for code in range(2001, int(last) + 1):
            answer = await call(
                "alarmo/manage_people",
                code="1234",
                action="set_code",
                user_id="bob",
                new_code=str(code),
            )
            assert answer["success"]
        answer = await call(
            "alarmo/manage_people",
            code="1234",
            action="set_code",
            user_id="bob",
            new_code=str(int(last) + 1),
        )
        assert answer["error"]["code"] == "too_many_new_codes"
        assert await owner(coordinator, last) == "bob"
        answer = await call(
            "alarmo/manage_people", code="1234", action="remove", user_id="bob"
        )
        assert answer["success"]


@pytest.mark.asyncio
async def test_people_for_managers_only(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A manager lists users and accounts without codes; anyone else is refused."""
    ana = await hass.auth.async_create_user("Ana")
    bob = await hass.auth.async_create_user("Bob")
    await setup_with_users(
        hass, [user("ana", "1234", ana, manages=True), user("bob", "5678", bob)]
    )

    answer = await Caller(await connect(hass, hass_ws_client, bob))("alarmo/people")
    assert answer["error"]["code"] == "not_manager"

    answer = await Caller(await connect(hass, hass_ws_client, ana))("alarmo/people")
    assert answer["result"]["users"] == [
        {
            "user_id": "ana",
            "name": "Ana",
            "enabled": True,
            "can_manage_users": True,
            "ha_user_id": ana.id,
        },
        {
            "user_id": "bob",
            "name": "Bob",
            "enabled": True,
            "can_manage_users": False,
            "ha_user_id": bob.id,
        },
    ]
    accounts = {account["id"] for account in answer["result"]["accounts"]}
    assert {ana.id, bob.id} <= accounts
    system = [
        account.id
        for account in await hass.auth.async_get_users()
        if account.system_generated
    ]
    assert not accounts & set(system)


@pytest.mark.asyncio
async def test_manager_adds_sets_code_and_removes(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A manager adds a user, sets their code without the old one, and removes them."""
    ana = await hass.auth.async_create_user("Ana")
    carla = await hass.auth.async_create_user("Carla")
    coordinator = await setup_with_users(hass, [user("ana", "1234", ana, manages=True)])
    call = Caller(await connect(hass, hass_ws_client, ana))

    answer = await call(
        "alarmo/manage_people",
        code="1234",
        action="add",
        name="Carla",
        new_code="2468",
        ha_user_id=carla.id,
    )
    assert answer["success"]
    added = await owner(coordinator, "2468")
    assert coordinator.store.async_get_user(added)["ha_user_id"] == carla.id

    answer = await call(
        "alarmo/manage_people",
        code="1234",
        action="set_code",
        user_id=added,
        new_code="1357",
    )
    assert answer["success"]
    assert await owner(coordinator, "1357") == added

    answer = await call(
        "alarmo/manage_people", code="1234", action="remove", user_id=added
    )
    assert answer["success"]
    assert coordinator.store.async_get_user(added) is None


@pytest.mark.asyncio
async def test_manager_code_confirms_every_change(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A manager's session is not enough: a wrong code changes nothing."""
    ana = await hass.auth.async_create_user("Ana")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana, manages=True), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    answer = await call(
        "alarmo/manage_people",
        code="5678",
        action="set_code",
        user_id="bob",
        new_code="1357",
    )
    assert answer["error"]["code"] == "wrong_code"
    assert await owner(coordinator, "5678") == "bob"


@pytest.mark.asyncio
async def test_codes_in_use_count_as_wrong(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """Setting someone's code to one that is taken counts as a wrong code."""
    ana = await hass.auth.async_create_user("Ana")
    await setup_with_users(
        hass,
        [user("ana", "1234", ana, manages=True), user("bob", "5678"), user("eve", "9")],
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    for _ in range(3):
        answer = await call(
            "alarmo/manage_people",
            code="1234",
            action="set_code",
            user_id="eve",
            new_code="5678",
        )
        assert answer["error"]["code"] == "code_in_use"
    answer = await call(
        "alarmo/manage_people",
        code="1234",
        action="set_code",
        user_id="eve",
        new_code="1357",
    )
    assert answer["error"]["code"] == "too_many_attempts"


@pytest.mark.asyncio
async def test_links(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A manager links and unlinks accounts, one user per account at most."""
    ana = await hass.auth.async_create_user("Ana")
    bob = await hass.auth.async_create_user("Bob")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana, manages=True), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    async def link(account_id: str | None) -> dict[str, Any]:
        return await call(
            "alarmo/manage_people",
            code="1234",
            action="link",
            user_id="bob",
            ha_user_id=account_id,
        )

    assert (await link(ana.id))["error"]["code"] == "account_in_use"
    assert (await link("nobody"))["error"]["code"] == "account_not_found"
    assert (await link(bob.id))["success"]
    assert coordinator.store.async_get_user("bob")["ha_user_id"] == bob.id
    assert (await link(None))["success"]
    assert coordinator.store.async_get_user("bob")["ha_user_id"] is None


@pytest.mark.asyncio
async def test_someone_keeps_managing(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """The last manager is not removed, unlinked or stripped of the right."""
    ana = await hass.auth.async_create_user("Ana")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana, manages=True), user("bob", "5678")]
    )
    call = Caller(await connect(hass, hass_ws_client, ana))

    for action, data in (
        ("remove", {}),
        ("link", {"ha_user_id": None}),
        ("set_manager", {"can_manage_users": False}),
    ):
        answer = await call(
            "alarmo/manage_people", code="1234", action=action, user_id="ana", **data
        )
        assert answer["error"]["code"] == "last_manager"
    answer = await call(
        "alarmo/manage_people",
        code="1234",
        action="set_manager",
        user_id="bob",
        can_manage_users=True,
    )
    assert answer["error"]["code"] == "needs_account"
    assert coordinator.store.async_get_user("ana")["can_manage_users"]


@pytest.mark.asyncio
async def test_admin_links_through_the_panel(
    hass: Any, enable_custom_integrations: Any
) -> None:
    """The panel's user update links an account, checked like a manager's link."""
    ana = await hass.auth.async_create_user("Ana")
    system = await hass.auth.async_create_system_user("Service")
    coordinator = await setup_with_users(
        hass, [user("ana", "1234", ana), user("bob", "5678")]
    )

    err = await coordinator.async_update_user_config("bob", {"ha_user_id": ana.id})
    assert err == "Home Assistant user already linked to another user"
    err = await coordinator.async_update_user_config("bob", {"ha_user_id": system.id})
    assert err == "Home Assistant user not found"
    err = await coordinator.async_update_user_config("bob", {"can_manage_users": True})
    assert err == "A user who manages users needs a Home Assistant user"
    assert not await coordinator.async_update_user_config(
        "ana", {"can_manage_users": True}
    )
    assert coordinator.store.async_get_user("ana")["can_manage_users"]


@pytest.mark.asyncio
async def test_configuration_needs_an_admin(
    hass: Any, enable_custom_integrations: Any, hass_ws_client: Any
) -> None:
    """A Home Assistant user who is not an admin cannot read Alarmo's users."""
    ana = await hass.auth.async_create_user("Ana")
    await setup_with_users(hass, [user("ana", "1234", ana)])

    answer = await Caller(await connect(hass, hass_ws_client, ana))("alarmo/users")
    assert answer["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_users_created_in_the_same_second(hass: Any) -> None:
    """Two users created in the same second both stay."""
    storage = AlarmoStorage(hass)
    storage.async_schedule_save = lambda: None
    with patch("custom_components.alarmo.store.time.time", return_value=1700000000):
        first = storage.async_create_user({"name": "Ana"})
        second = storage.async_create_user({"name": "Bob"})
    assert first.user_id != second.user_id
    assert set(storage.users) == {first.user_id, second.user_id}
