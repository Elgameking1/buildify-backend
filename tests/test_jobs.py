"""Job request lifecycle and who is allowed to move it."""

import pytest

from tests.conftest import API, auth, register


@pytest.fixture
async def hiring(client):
    """A client, a worker, and a pending job request between them."""
    hirer = await register(client, "CLIENT", "hirer@test.com", region="Greater Accra")
    worker = await register(
        client,
        "WORKER",
        "mason@test.com",
        headline="Mason",
        region="Greater Accra",
        city="Accra",
    )

    response = await client.post(
        f"{API}/jobs",
        json={
            "worker_id": worker["user"]["id"],
            "title": "Build a boundary wall",
            "description": "Block work for a 30 metre boundary wall in East Legon.",
            "location": "East Legon, Accra",
            "budget": "4500.00",
        },
        headers=auth(hirer["access_token"]),
    )
    assert response.status_code == 201, response.text

    return {"client": hirer, "worker": worker, "job": response.json()}


async def set_status(client, token, job_id, status):
    return await client.patch(
        f"{API}/jobs/{job_id}/status", json={"status": status}, headers=auth(token)
    )


async def test_job_request_starts_pending_and_notifies_the_worker(client, hiring):
    assert hiring["job"]["status"] == "PENDING"

    notifications = await client.get(
        f"{API}/notifications", headers=auth(hiring["worker"]["access_token"])
    )
    types = [item["type"] for item in notifications.json()["items"]]
    assert "JOB_REQUEST_RECEIVED" in types


async def test_full_happy_path(client, hiring):
    job_id = hiring["job"]["id"]
    worker_token = hiring["worker"]["access_token"]
    client_token = hiring["client"]["access_token"]

    assert (await set_status(client, worker_token, job_id, "ACCEPTED")).json()[
        "status"
    ] == "ACCEPTED"
    assert (await set_status(client, worker_token, job_id, "IN_PROGRESS")).json()[
        "status"
    ] == "IN_PROGRESS"

    completed = await set_status(client, client_token, job_id, "COMPLETED")
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["completed_at"] is not None


async def test_pending_cannot_jump_straight_to_completed(client, hiring):
    response = await set_status(
        client, hiring["client"]["access_token"], hiring["job"]["id"], "COMPLETED"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "invalid_transition"


async def test_worker_cannot_mark_a_job_complete(client, hiring):
    """Completion is the client's judgement, never the worker's."""
    job_id = hiring["job"]["id"]
    worker_token = hiring["worker"]["access_token"]

    await set_status(client, worker_token, job_id, "ACCEPTED")
    await set_status(client, worker_token, job_id, "IN_PROGRESS")

    response = await set_status(client, worker_token, job_id, "COMPLETED")

    assert response.status_code == 403


async def test_client_cannot_accept_on_the_workers_behalf(client, hiring):
    response = await set_status(
        client, hiring["client"]["access_token"], hiring["job"]["id"], "ACCEPTED"
    )

    assert response.status_code == 403


async def test_an_unrelated_worker_cannot_touch_the_job(client, hiring):
    intruder = await register(client, "WORKER", "intruder-worker@test.com")

    response = await set_status(
        client, intruder["access_token"], hiring["job"]["id"], "ACCEPTED"
    )

    # 404, not 403 - a 403 would confirm this job id exists.
    assert response.status_code == 404


async def test_declined_jobs_are_terminal(client, hiring):
    job_id = hiring["job"]["id"]
    worker_token = hiring["worker"]["access_token"]

    await set_status(client, worker_token, job_id, "DECLINED")
    response = await set_status(client, worker_token, job_id, "ACCEPTED")

    assert response.status_code == 409


async def test_client_may_cancel_a_pending_job(client, hiring):
    response = await set_status(
        client, hiring["client"]["access_token"], hiring["job"]["id"], "CANCELLED"
    )

    assert response.json()["status"] == "CANCELLED"


async def test_role_scoped_listing(client, hiring):
    sent = await client.get(
        f"{API}/jobs", params={"role": "sent"}, headers=auth(hiring["client"]["access_token"])
    )
    received = await client.get(
        f"{API}/jobs",
        params={"role": "received"},
        headers=auth(hiring["worker"]["access_token"]),
    )

    assert sent.json()["total"] == 1
    assert received.json()["total"] == 1

    # The client sent it, so it must not appear in their "received" list.
    wrong_way = await client.get(
        f"{API}/jobs",
        params={"role": "received"},
        headers=auth(hiring["client"]["access_token"]),
    )
    assert wrong_way.json()["total"] == 0


async def test_a_client_cannot_hire_themselves(client):
    hirer = await register(client, "CLIENT", "selfhire@test.com")

    response = await client.post(
        f"{API}/jobs",
        json={
            "worker_id": hirer["user"]["id"],
            "title": "Do my own work",
            "description": "This should not be possible at all.",
            "location": "Accra",
        },
        headers=auth(hirer["access_token"]),
    )

    assert response.status_code in {404, 409}
