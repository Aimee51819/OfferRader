from app.domain.duplicates import check_duplicate
from app.domain.normalize import canonical_url, extract_requisition_ids, normalize_company, normalize_title
from app.services.applications_service import create_application


def _mk(db, **kw):
    app, _ = create_application(db, **kw)
    db.commit()
    return app


def test_exact_req_blocks_other_role_warns_new_cycle_allows(db):
    old = _mk(db, company="IBM", role_title="Product Manager Intern", external_job_id="84211",
              recruiting_cycle="Summer 2026", stage="rejected")
    _mk(db, company="IBM", role_title="Software Engineer Intern", external_job_id="84377", recruiting_cycle="Summer 2027")

    same = check_duplicate(db, company_name="IBM Corporation", role_title="Software Engineer Intern",
                           external_job_id="84377", recruiting_cycle="Summer 2027")
    assert same.decision == "BLOCK"
    assert [m["match_kind"] for m in same.matches if m["level"] == "BLOCK"] == ["exact_opening"]

    other = check_duplicate(db, company_name="IBM", role_title="Data Analyst Intern", recruiting_cycle="Summer 2027")
    assert other.decision == "WARN"
    assert {m["match_kind"] for m in other.matches} == {"other_role_same_company"}

    reopened = check_duplicate(db, company_name="IBM", role_title="Product Manager Intern", external_job_id="84211",
                               recruiting_cycle="Summer 2027")
    kinds = {m["application_id"]: m["match_kind"] for m in reopened.matches}
    assert kinds[old.id] == "reopened_cycle"
    assert reopened.decision == "WARN"  # still warns because a different IBM role is active
    assert all(m["level"] != "BLOCK" for m in reopened.matches)


def test_canonical_url_blocks_even_with_tracking_params(db):
    _mk(db, company="Globex", role_title="Solutions Engineer Intern",
        posting_url="https://boards.greenhouse.io/globex/jobs/4455?gh_jid=4455&utm_source=linkedin")
    r = check_duplicate(db, company_name="Globex", role_title="SE Intern",
                        posting_url="https://www.boards.greenhouse.io/globex/jobs/4455/?gh_jid=4455&utm_campaign=x")
    assert r.decision == "BLOCK"


def test_same_title_without_ids_warns_not_blocks(db):
    _mk(db, company="Hooli", role_title="Software Engineer Intern")
    r = check_duplicate(db, company_name="Hooli", role_title="Software Engineer Intern")
    assert r.decision == "WARN" and r.matches[0]["match_kind"] == "same_title_no_id"


def test_no_company_allows(db):
    assert check_duplicate(db, company_name="Brand New Co", role_title="PM Intern").decision == "ALLOW"


def test_normalizers():
    assert normalize_company("The Acme Robotics, Inc.") == "acme robotics"
    assert normalize_title("SWE Intern (Summer 2027)") == "software engineer intern"
    assert normalize_title("Software Development Engineer Internship") == "software engineer intern"
    assert canonical_url("https://jobs.lever.co/x/123/apply?lever-source=li") == "https://jobs.lever.co/x/123"
    assert canonical_url("javascript:alert(1)") is None
    assert extract_requisition_ids("Aimee Sun - 84211 - Product Manager Intern") == ["84211"]
    assert "R0123456" in extract_requisition_ids("Requisition ID: R0123456")
    assert extract_requisition_ids("Summer 2027 internship") == []
