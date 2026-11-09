"""auto_all policy (no review queue), deletion, categories and typed deadlines."""
from __future__ import annotations

from sqlalchemy import select

from app.domain.normalize import infer_category
from app.models import ActionItem, Application, ApplicationEvent

from .helpers import apps, deliver, pending


def test_auto_all_applies_everything_without_review(db, replay, fixtures):
    deliver(db, replay, fixtures, "01_ibm_pm_ack", "02_ibm_swe_ack", "03_ibm_pm_oa", "19_hooli_swe_rejection",
            policy="auto_all")
    assert pending(db) == []
    ibm = apps(db, "IBM")
    assert len(ibm) == 2  # the OA attached to the PM application, no duplicate
    pm = next(a for a in ibm if "Product" in a.role_title)
    assert pm.stage == "assessment_invited"
    kinds = [i.kind for i in db.execute(select(ActionItem).where(ActionItem.application_id == pm.id)).scalars()]
    assert kinds == ["oa_deadline"]


def test_auto_all_never_creates_from_contradictory_output(db, fixtures):
    from app.services.llm.offline import ReplayBackend

    f = fixtures["14_umbrella_sa_ack"]
    contradictory = {**f["model_output"], "is_job_related": False}
    deliver(db, ReplayBackend({f["subject"]: contradictory}), fixtures, f["name"], policy="auto_all")
    assert apps(db) == [] and pending(db) == []


def test_delete_application_removes_children(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "03_ibm_pm_oa", policy="auto_all")
    (app,) = apps(db)
    app_id = app.id
    db.expire_all()
    r = client.delete(f"/api/applications/{app_id}")
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Application, app_id) is None
    assert db.execute(select(ActionItem)).scalars().all() == []
    assert db.execute(select(ApplicationEvent).where(ApplicationEvent.application_id == app_id)).scalars().all() == []
    assert client.delete(f"/api/applications/{app_id}").status_code == 404


def test_deadlines_are_typed_and_sorted_nearest_first(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "08_globex_oa_dateonly", "03_ibm_pm_oa", policy="auto_all")
    items = client.get("/api/dashboard/deadlines").json()["items"]
    assert items and all(i["deadline_type"] == "OA" for i in items)
    keys = [i["due_at"] or f"{i['due_date']}T23:59:59" for i in items]
    assert keys == sorted(keys)


def test_categories():
    assert infer_category("Solutions Engineer Intern") == "SE"
    assert infer_category("Strategy & Solutions Consulting Analyst") == "SE"
    assert infer_category("Product Manager Intern") == "PM"
    assert infer_category("Activision US 2027 Summer Internships - Production") == "PM"
    assert infer_category("Technical Program Manager") == "Other"
    assert infer_category("Software Engineer Intern") == "SDE/SWE"
    assert infer_category("Data Analyst Intern") == "Data"


def test_done_deadlines_are_listed_and_deletable(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "03_ibm_pm_oa", policy="auto_all")
    (item,) = client.get("/api/dashboard/deadlines").json()["items"]
    assert client.post(f"/api/action-items/{item['id']}", json={"state": "completed"}).status_code == 200
    data = client.get("/api/dashboard/deadlines").json()
    assert data["items"] == []
    (done,) = data["done"]
    assert done["id"] == item["id"] and done["completed_at"] and done["deadline_type"] == "OA"
    assert client.delete(f"/api/action-items/{item['id']}").status_code == 200
    assert client.get("/api/dashboard/deadlines").json()["done"] == []
    assert len(apps(db)) == 1  # the application itself stays


def test_application_links_to_its_latest_email(client, db, replay, fixtures):
    from app.models import EmailMessage

    deliver(db, replay, fixtures, "01_ibm_pm_ack", "03_ibm_pm_oa", policy="auto_all")
    for m in db.execute(select(EmailMessage)).scalars():
        m.web_link = f"https://mail.example/{m.subject[:10]}"
    db.commit()
    (row,) = [a for a in client.get("/api/applications").json() if "Product" in a["role_title"]]
    oa = next(m for m in db.execute(select(EmailMessage)).scalars() if "OA" in m.subject or "assessment" in m.subject.lower())
    assert row["source_email_link"] == oa.web_link


def test_recover_last_removed_application_and_deadline(client, db, replay, fixtures):
    deliver(db, replay, fixtures, "01_ibm_pm_ack", "03_ibm_pm_oa", policy="auto_all")
    (app,) = apps(db)
    app_id = app.id
    before = client.get(f"/api/applications/{app_id}").json()
    (item,) = client.get("/api/dashboard/deadlines").json()["items"]

    # Deadline: done -> delete -> recover
    client.post(f"/api/action-items/{item['id']}", json={"state": "completed"})
    client.delete(f"/api/action-items/{item['id']}")
    assert client.get("/api/trash/deadline").json()["last"]["id"] == item["id"]
    assert client.post("/api/trash/deadline/restore").status_code == 200
    assert [d["id"] for d in client.get("/api/dashboard/deadlines").json()["done"]] == [item["id"]]
    assert client.get("/api/trash/deadline").json()["last"] is None

    # Application: remove -> recover brings back events and deadlines with the same ids
    assert client.delete(f"/api/applications/{app_id}").status_code == 200
    assert client.get("/api/applications").json() == []
    assert "IBM" in client.get("/api/trash/application").json()["last"]["label"]
    assert client.post("/api/trash/application/restore").status_code == 200
    after = client.get(f"/api/applications/{app_id}").json()
    assert after["role_title"] == before["role_title"] and after["stage"] == before["stage"]
    assert len(after["events"]) == len(before["events"]) + 1  # + the "marked done" audit event
    assert [d["id"] for d in client.get("/api/dashboard/deadlines").json()["done"]] == [item["id"]]
    assert client.post("/api/trash/application/restore").status_code == 409  # nothing left
