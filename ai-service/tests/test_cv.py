from app.ingest.classify import suggest
from app.ingest.cv import merge, name_hints, strip_personal_details
from app.privacy.redactor import Redactor

CV = """CURRICULUM VITAE
Name: Anil Kumar
Proposed Position: Project Manager
Date of Birth: 12/04/1980
Father's Name: R. K. Sharma
Aadhaar No: 2341 2341 2346
PAN No: ABCPK1234F
Permanent Address: 12, Sector 5, Noida
Client: State Bank of India - Core banking rollout
Accounts Manager, Finance Dept (2004-2008)
Education: M.Tech (Computer Science), IIT Delhi
Certifications: PMP, ITIL v4"""


def test_personal_identifier_lines_are_dropped_and_experience_kept():
    out = strip_personal_details(CV)
    for gone in ("1980", "Sharma", "2341", "ABCPK1234F", "Sector 5"):
        assert gone not in out
    assert "State Bank of India" in out and "Accounts Manager" in out and "PMP" in out


def test_name_is_redacted_from_file_name_hint_even_in_capitals():
    hints = name_hints("CV_Anil_Kumar", {})
    assert "Anil Kumar" in hints and "ANIL KUMAR" in hints
    r = Redactor(actions={}, people=hints)
    out = r.redact("ANIL KUMAR\nAnil Kumar has 19 years of experience")
    assert "Anil" not in out and "ANIL" not in out


def test_merge_fills_cv_fields_and_keeps_what_a_person_entered():
    answer = {"name": "Anil Kumar", "proposed_role": "Project Manager", "total_years": 19.0,
              "qualifications": ["M.Tech (Computer Science)", "B.Tech", "Diploma"], "skills": ["Java", "Oracle"],
              "certifications": ["PMP", "ITIL v4"]}
    meta, filled = merge({"role": "Team Lead", "skills": ["java"]}, answer)
    assert meta["role"] == "Team Lead"                       # entered by a person
    assert meta["name"] == "Anil Kumar" and meta["total_years"] == 19
    assert meta["qualification"] == "M.Tech (Computer Science), B.Tech"     # two highest only
    assert meta["skills"] == ["java", "Oracle"] and meta["certifications"] == ["PMP", "ITIL v4"]
    assert "role" not in filled and set(meta["_auto"]) == set(filled)


def test_cv_type_is_recognised():
    assert suggest("hr", "Anil Kumar", CV)["doc_type"] == "cv"
