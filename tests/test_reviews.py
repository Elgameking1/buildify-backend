"""Reviews and the rating aggregation they drive."""

import pytest

from tests.conftest import API, auth, register


async def complete_job(client, hirer, worker, title="Wall plastering"):
    """Drive one job all the way to COMPLETED and return its id."""
    created = await client.post(
        f"{API}/jobs",
        json={
            "worker_id": worker["user"]["id"],
            "title": title,
            "description": "Plaster the internal walls of a three bedroom house.",
            "location": "Adenta, Accra",
        },
        headers=auth(hirer["access_token"]),
    )
    job_id = created.json()["id"]

    for actor, status in (
        (worker, "ACCEPTED"),
        (worker, "IN_PROGRESS"),
        (hirer, "COMPLETED"),
    ):
        response = await client.patch(
            f"{API}/jobs/{job_id}/status",
            json={"status": status},
            headers=auth(actor["access_token"]),
        )
        assert response.status_code == 200, response.text

    return job_id


@pytest.fixture
async def parties(client):
    hirer = await register(client, "CLIENT", "reviewer@test.com")
    worker = await register(client, "WORKER", "reviewed@test.com", headline="Plasterer")
    return hirer, worker


async def test_review_updates_the_worker_rating(client, parties):
    hirer, worker = parties
    job_id = await complete_job(client, hirer, worker)

    response = await client.post(
        f"{API}/jobs/{job_id}/review",
        json={"rating": 5, "comment": "Neat work, finished on time."},
        headers=auth(hirer["access_token"]),
    )

    assert response.status_code == 201
    assert response.json()["rating"] == 5

    profile = await client.get(f"{API}/workers/{worker['user']['id']}")
    assert float(profile.json()["avg_rating"]) == 5.0
    assert profile.json()["rating_count"] == 1


async def test_average_is_the_arithmetic_mean(client, parties):
    hirer, worker = parties

    for rating, title in ((5, "First job"), (2, "Second job")):
        job_id = await complete_job(client, hirer, worker, title=title)
        await client.post(
            f"{API}/jobs/{job_id}/review",
            json={"rating": rating},
            headers=auth(hirer["access_token"]),
        )

    profile = await client.get(f"{API}/workers/{worker['user']['id']}")
    assert float(profile.json()["avg_rating"]) == 3.5
    assert profile.json()["rating_count"] == 2


async def test_cannot_review_a_job_that_is_not_completed(client, parties):
    hirer, worker = parties
    created = await client.post(
        f"{API}/jobs",
        json={
            "worker_id": worker["user"]["id"],
            "title": "Not finished yet",
            "description": "This job has only just been requested, nothing done.",
            "location": "Accra",
        },
        headers=auth(hirer["access_token"]),
    )

    response = await client.post(
        f"{API}/jobs/{created.json()['id']}/review",
        json={"rating": 1},
        headers=auth(hirer["access_token"]),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "job_not_completed"


async def test_cannot_review_the_same_job_twice(client, parties):
    hirer, worker = parties
    job_id = await complete_job(client, hirer, worker)

    first = await client.post(
        f"{API}/jobs/{job_id}/review",
        json={"rating": 5},
        headers=auth(hirer["access_token"]),
    )
    assert first.status_code == 201

    second = await client.post(
        f"{API}/jobs/{job_id}/review",
        json={"rating": 1},
        headers=auth(hirer["access_token"]),
    )

    assert second.status_code == 409
    assert second.json()["code"] == "already_reviewed"


async def test_only_the_hiring_client_may_review(client, parties):
    hirer, worker = parties
    job_id = await complete_job(client, hirer, worker)
    stranger = await register(client, "CLIENT", "not-the-hirer@test.com")

    response = await client.post(
        f"{API}/jobs/{job_id}/review",
        json={"rating": 1},
        headers=auth(stranger["access_token"]),
    )

    assert response.status_code == 403


async def test_rating_out_of_range_is_rejected(client, parties):
    hirer, worker = parties
    job_id = await complete_job(client, hirer, worker)

    response = await client.post(
        f"{API}/jobs/{job_id}/review",
        json={"rating": 6},
        headers=auth(hirer["access_token"]),
    )

    assert response.status_code == 422


async def test_rating_summary_reports_the_distribution(client, parties):
    hirer, worker = parties
    for rating, title in ((5, "Job A"), (5, "Job B"), (3, "Job C")):
        job_id = await complete_job(client, hirer, worker, title=title)
        await client.post(
            f"{API}/jobs/{job_id}/review",
            json={"rating": rating},
            headers=auth(hirer["access_token"]),
        )

    response = await client.get(f"{API}/workers/{worker['user']['id']}/rating")

    body = response.json()
    assert body["rating_count"] == 3
    assert body["distribution"]["5"] == 2
    assert body["distribution"]["3"] == 1
