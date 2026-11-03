"""Duplicate detection must catch split rows without merging distinct reqs."""


def _add(client, **fields) -> dict:
    return client.post("/api/applications", json=fields).json()


def _pairs(client) -> set[frozenset[int]]:
    groups = client.get("/api/applications/duplicates").json()
    return {frozenset(a["id"] for a in g["applications"]) for g in groups}


def test_placeholder_title_row_is_flagged(client):
    stub = _add(client, company="Solace", title="Unknown", status="ghosted")
    real = _add(
        client,
        company="Solace",
        title="Associate Product Engineer (College Grad 2027)",
        status="rejected",
    )
    assert _pairs(client) == {frozenset({stub["id"], real["id"]})}


def test_reordered_title_is_flagged(client):
    a = _add(
        client,
        company="Northrop Grumman",
        title="Associate Software Engineer / Software Engineer (2027)",
        status="ghosted",
    )
    b = _add(
        client,
        company="Northrop Grumman",
        title="2027 Associate Software Engineer / Software Engineer",
        status="rejected",
        job_id="R10239590",
    )
    assert _pairs(client) == {frozenset({a["id"], b["id"]})}


def test_distinct_requisitions_are_never_flagged(client):
    _add(
        client,
        company="IBM",
        title="Software Developer Spring Co-op 2027",
        status="rejected",
        job_id="128506",
    )
    _add(
        client,
        company="IBM",
        title="Technical Sales Engineer - Entry-Level Sales Program 2027",
        status="rejected",
        job_id="128340",
    )
    _add(
        client,
        company="IBM",
        title="2027 Software Engineering Intern - Agentic AI",
        status="rejected",
        job_id="129919",
    )
    assert _pairs(client) == set()


def test_different_roles_without_job_ids_are_not_flagged(client):
    _add(
        client,
        company="P&G",
        title="Data & AI Engineering (2027 Grads)",
        status="rejected",
    )
    _add(
        client,
        company="P&G",
        title="IT Engineering (Software, Platform, & Network) (2027 Grads)",
        status="rejected",
    )
    assert _pairs(client) == set()


def test_same_job_id_is_flagged(client):
    a = _add(client, company="Cigna", title="TECDP Track A", job_id="26010470")
    b = _add(client, company="Cigna", title="Something Else", job_id="26010470")
    assert _pairs(client) == {frozenset({a["id"], b["id"]})}


def _dismiss(client, first_id: int, second_id: int):
    return client.post(
        "/api/applications/duplicates/dismiss",
        json={"first_id": first_id, "second_id": second_id},
    )


def test_dismissed_pair_is_not_suggested_and_others_are(client):
    a = _add(client, company="Cigna", title="TECDP Track A", job_id="26010470")
    b = _add(client, company="Cigna", title="Something Else", job_id="26010470")
    c = _add(client, company="Solace", title="Unknown")
    d = _add(client, company="Solace", title="Associate Product Engineer")
    assert len(_pairs(client)) == 2

    assert _dismiss(client, a["id"], b["id"]).status_code == 200
    assert _pairs(client) == {frozenset({c["id"], d["id"]})}


def test_dismissal_is_order_independent_and_idempotent(client):
    a = _add(client, company="Cigna", title="TECDP Track A", job_id="26010470")
    b = _add(client, company="Cigna", title="Something Else", job_id="26010470")

    first = _dismiss(client, b["id"], a["id"]).json()
    again = _dismiss(client, a["id"], b["id"]).json()
    assert first == again
    assert (first["low_id"], first["high_id"]) == (min(a["id"], b["id"]), max(a["id"], b["id"]))

    dismissed = client.get("/api/applications/duplicates/dismissed").json()
    assert len(dismissed) == 1
    # The UI names the pair from this, so both rows must come back, low first.
    assert [a["id"] for a in dismissed[0]["applications"]] == [
        min(a["id"], b["id"]),
        max(a["id"], b["id"]),
    ]
    assert _pairs(client) == set()


def test_restoring_a_dismissal_suggests_the_pair_again(client):
    a = _add(client, company="Cigna", title="TECDP Track A", job_id="26010470")
    b = _add(client, company="Cigna", title="Something Else", job_id="26010470")
    _dismiss(client, a["id"], b["id"])

    # Reversed order on purpose; restoring twice is harmless.
    for _ in range(2):
        res = client.delete(f"/api/applications/duplicates/dismissed/{b['id']}/{a['id']}")
        assert res.status_code == 200
    assert _pairs(client) == {frozenset({a["id"], b["id"]})}
    assert client.get("/api/applications/duplicates/dismissed").json() == []


def test_dismiss_rejects_bad_pairs(client):
    a = _add(client, company="Cigna", title="TECDP Track A")
    assert _dismiss(client, a["id"], a["id"]).status_code == 400
    assert _dismiss(client, a["id"], a["id"] + 999).status_code == 404


def test_deleting_or_merging_an_application_drops_its_dismissals(client):
    a = _add(client, company="Cigna", title="TECDP Track A", job_id="26010470")
    b = _add(client, company="Cigna", title="Something Else", job_id="26010470")
    c = _add(client, company="Cigna", title="Unknown")
    _dismiss(client, a["id"], b["id"])
    _dismiss(client, b["id"], c["id"])

    assert client.delete(f"/api/applications/{a['id']}").status_code == 200
    remaining = client.get("/api/applications/duplicates/dismissed").json()
    assert [(r["low_id"], r["high_id"]) for r in remaining] == [(b["id"], c["id"])]

    res = client.post(
        "/api/applications/merge",
        json={"source_id": c["id"], "target_id": b["id"]},
    )
    assert res.status_code == 200
    assert client.get("/api/applications/duplicates/dismissed").json() == []
    assert client.get("/api/applications/duplicates").status_code == 200


def test_merge_keeps_job_id_and_beats_ghosted(client):
    ghost = _add(
        client,
        company="Northrop Grumman",
        title="Associate Software Engineer (2027)",
        status="ghosted",
    )
    real = _add(
        client,
        company="Northrop Grumman",
        title="Unknown",
        status="rejected",
        job_id="R10239590",
    )

    # Merge the richer row into the ghosted one: direction must not lose data.
    merged = client.post(
        "/api/applications/merge",
        json={"source_id": real["id"], "target_id": ghost["id"]},
    ).json()

    assert merged["status"] == "rejected", "a real outcome outranks ghosted"
    assert merged["job_id"] == "R10239590", "job id must survive the merge"
    assert merged["title"] == "Associate Software Engineer (2027)"
    assert len(client.get("/api/applications").json()) == 1
