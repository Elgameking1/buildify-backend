"""Worker profiles and search by skill, location and rating."""

from tests.conftest import API, auth, make_admin, register


async def create_skill(client, admin_token, name):
    response = await client.post(
        f"{API}/skills", json={"name": name}, headers=auth(admin_token)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def admin_token(client, session_factory, email="skills-admin@test.com"):
    account = await register(client, "CLIENT", email)
    await make_admin(session_factory, account["user"]["id"])
    login = await client.post(
        f"{API}/auth/login", json={"email": email, "password": "DemoPass!2026"}
    )
    return login.json()["access_token"]


async def test_worker_can_update_profile_and_skills(client, session_factory):
    token = await admin_token(client, session_factory)
    plumbing = await create_skill(client, token, "Plumbing")
    worker = await register(client, "WORKER", "plumber@test.com")

    await client.patch(
        f"{API}/workers/me",
        json={"headline": "Plumber", "years_experience": 7, "base_rate": "250.00"},
        headers=auth(worker["access_token"]),
    )
    response = await client.put(
        f"{API}/workers/me/skills",
        json={"skills": [{"skill_id": plumbing["id"], "proficiency": "EXPERT"}]},
        headers=auth(worker["access_token"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["years_experience"] == 7
    assert [s["skill"]["name"] for s in body["skills"]] == ["Plumbing"]


async def test_setting_skills_replaces_the_previous_set(client, session_factory):
    token = await admin_token(client, session_factory)
    plumbing = await create_skill(client, token, "Plumbing")
    welding = await create_skill(client, token, "Welding")
    worker = await register(client, "WORKER", "multi-skill@test.com")

    await client.put(
        f"{API}/workers/me/skills",
        json={"skills": [{"skill_id": plumbing["id"]}, {"skill_id": welding["id"]}]},
        headers=auth(worker["access_token"]),
    )
    response = await client.put(
        f"{API}/workers/me/skills",
        json={"skills": [{"skill_id": welding["id"]}]},
        headers=auth(worker["access_token"]),
    )

    assert [s["skill"]["name"] for s in response.json()["skills"]] == ["Welding"]


async def test_search_by_skill_and_location(client, session_factory):
    token = await admin_token(client, session_factory)
    masonry = await create_skill(client, token, "Masonry")
    electrical = await create_skill(client, token, "Electrical")

    mason = await register(
        client, "WORKER", "accra-mason@test.com", region="Greater Accra", city="Accra"
    )
    await client.put(
        f"{API}/workers/me/skills",
        json={"skills": [{"skill_id": masonry["id"]}]},
        headers=auth(mason["access_token"]),
    )

    electrician = await register(
        client, "WORKER", "kumasi-electrician@test.com", region="Ashanti", city="Kumasi"
    )
    await client.put(
        f"{API}/workers/me/skills",
        json={"skills": [{"skill_id": electrical["id"]}]},
        headers=auth(electrician["access_token"]),
    )

    by_skill = await client.get(f"{API}/workers", params={"skill": "masonry"})
    assert [w["user_id"] for w in by_skill.json()["items"]] == [mason["user"]["id"]]

    by_region = await client.get(f"{API}/workers", params={"region": "Ashanti"})
    assert [w["user_id"] for w in by_region.json()["items"]] == [electrician["user"]["id"]]

    both = await client.get(
        f"{API}/workers", params={"skill": "masonry", "region": "Ashanti"}
    )
    assert both.json()["total"] == 0, "filters must combine, not widen the result"


async def test_a_client_has_no_worker_profile(client):
    account = await register(client, "CLIENT", "not-a-worker@test.com")

    response = await client.get(f"{API}/workers/me", headers=auth(account["access_token"]))

    assert response.status_code == 403


async def test_portfolio_rejects_a_key_from_another_account(client):
    """Object keys are namespaced by user id, and the server checks the prefix."""
    worker = await register(client, "WORKER", "portfolio@test.com")

    response = await client.put(
        f"{API}/workers/me/portfolio",
        json={"object_keys": ["portfolio/999999/somebody-elses-photo.jpg"]},
        headers=auth(worker["access_token"]),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
