"""English OTC guardrails: red-flag detection (see a doctor first), abuse-prone quantity limits, prohibited-claim
post-filter, drug_class notices, server-side item filter."""
from __future__ import annotations

import pytest

from app.agent import mock, safety

C1, D2, C2 = "Pharmacist-only (Class 1)", "Designated Class 2", "Class 2"


@pytest.mark.parametrize("text,code", [
    ("I've had a fever of 39C for 3 days and it won't go down", "high_fever_3days"),
    ("My temperature has been 38.8°C for three days", "high_fever_3days"),
    ("I can't breathe properly", "breathing"),
    ("I have chest pain and shortness of breath", "breathing"),
    ("My 2 months old baby has a fever", "infant_fever"),
    ("I have a fever of 41 degrees", "fever_41"),
    ("My child had a seizure", "seizure"),
    ("I'm pregnant, can I take a headache medicine?", "pregnancy"),
    ("I take blood pressure medicine", "chronic"),
    ("He can't keep fluids down", "dehydration"),
    ("I noticed black stool", "bleeding"),
    ("I got a rash after taking the medicine", "side_effect"),
    ("I want to die", "crisis"),
    ("What happens if I take a whole bottle at once", "crisis"),
])
def test_red_flags_detected(text, code):
    codes = [f["code"] for f in safety.detect_red_flags(text)]
    assert code in codes, (text, codes)


@pytest.mark.parametrize("text", ["I've had a fever since yesterday and feel like I'm getting a cold",
                                  "My throat is a little sore", "My eyes are tired", "I have a slight fever of 37.5C"])
def test_no_false_red_flags(text):
    assert not safety.is_blocking(safety.detect_red_flags(text))


def test_preface_levels():
    em = safety.red_flag_preface(safety.detect_red_flags("I can't breathe"))
    assert em.startswith("**Please seek medical care now.**") and "119" in em and "#7119" in em
    con = safety.red_flag_preface(safety.detect_red_flags("I've had a fever of 39C for 3 days and it won't go down"))
    assert "doctor" in con[:40] and "#7119" in con
    cr = safety.red_flag_preface(safety.detect_red_flags("I want to die"))
    assert "03-5774-0992" in cr and "won't suggest any products" in cr
    ph = safety.red_flag_preface(safety.detect_red_flags("I take blood pressure medicine"))
    assert "talk to a doctor or pharmacist" in ph


def test_abuse_detection():
    assert safety.detect_abuse("I want 5 boxes of cold medicine")
    assert safety.detect_abuse("Can I stock up on cough syrup?")
    assert safety.detect_abuse("I can't sleep so I want to take extra cold medicine")
    assert not safety.detect_abuse("I need cold medicine")
    assert not safety.detect_abuse("I want 5 boxes of masks")  # not a medicine


def test_mock_abuse_reply_limits_quantity():
    msg = "I want 5 boxes of cold medicine"
    ev = mock.respond(msg, "S01", "CR-0001", [], safety.detect_abuse(msg), None)
    text = "".join(e.get("text", "") for e in ev if e["type"] == "text")
    assert "one per customer" in text and "pharmacist" in text
    assert not any(e["type"] == "action" for e in ev)


@pytest.mark.parametrize("bad", ["This medicine will cure your cold.", "It is guaranteed to work.",
                                 "This is the strongest cold medicine.", "The No. 1 eye drops.",
                                 "It has no side effects.", "It is completely safe.", "Doctors recommend this medicine.",
                                 "You probably have the flu."])
def test_post_filter_replaces_banned(bad):
    out = safety.sanitize(bad)
    assert not safety.has_banned(out), out
    for w in ("will cure", "guaranteed", "strongest", "No. 1", "no side effects", "completely safe",
              "Doctors recommend", "have the flu"):
        assert w.lower() not in out.lower(), (bad, out)


def test_streaming_filter_handles_split_tokens():
    f = safety.TextFilter()
    out = f.feed("This medicine will ") + f.feed("cure you. Please ") + f.feed("rest well") + f.flush()
    assert "will cure" not in out and out.endswith("rest well")


def test_filter_items_rules():
    def P(pid, dc, cat, age=None):
        return {"product": {"id": pid, "drug_class": dc, "category_path": cat, "age_min": age, "title": pid}}

    items = [P("cold", D2, "Health & Medicine > Cold Medicine > Multi-Symptom Cold Tablets", 15),
             P("pain", C2, "Health & Medicine > Pain & Fever Relief > Headache & Period Pain Relief", 15),
             P("sheet", "", "Beauty & Personal Care > Hygiene & First Aid > Cooling Patches"),
             P("ors", "", "Beverages > Sports Drinks > Oral Rehydration Solution")]
    kept, _ = safety.filter_items(items, [])
    ids = [i["product"]["id"] for i in kept]
    assert ids == ["cold", "sheet", "ors"]            # cold medicine + pain reliever never together
    kept, _ = safety.filter_items(items, safety.detect_red_flags("I can't breathe"))
    assert [i["product"]["id"] for i in kept] == ["sheet", "ors"]
    kept, _ = safety.filter_items(items, safety.detect_red_flags("I want to die"))
    assert kept == []
    kept, _ = safety.filter_items(items, [], age=5)    # age_min 15 → removed for a 5-year-old
    assert [i["product"]["id"] for i in kept] == ["sheet", "ors"]


def test_symptom_groups_english_paths():
    g = lambda p: safety.symptom_group({"category_path": p})  # noqa: E731
    assert g("Health & Medicine > Cold Medicine > Children's Syrup") == "fever_cold"
    assert g("Health & Medicine > Pain & Fever Relief > Class 1 Pain & Fever Relief") == "fever_cold"
    assert g("Health & Medicine > Throat & Cough > Cough Suppressants") == "cough_throat"
    assert g("Health & Medicine > Nasal Allergy Meds > Nasal Sprays") == "nose"
    assert g("Health & Medicine > Topical Meds & Patches > Cold Patches") == "topical"
    assert g("Health & Medicine > Eye Drops > Eye Strain Relief") == "eye"


def test_child_age():
    assert safety.child_age("My 5-year-old has a fever") == 5
    assert safety.child_age("My 2 months old baby has a fever") < 0.3
    assert safety.child_age("I feel feverish") is None


def test_drug_class_notes_wording():
    n1 = safety.compliance_notes({"drug_class": C1})
    assert "a pharmacist must explain this medicine at purchase" in n1[0]
    assert any("pharmacy counter" in n for n in n1)
    assert safety.compliance_notes({"drug_class": D2})[0].startswith("Designated Class 2: check the warnings")
    assert "one per customer" in " ".join(safety.compliance_notes({"drug_class": D2, "abuse_control": True}))
    assert safety.DISCLAIMER.startswith("This service provides general information only and does not replace a diagnosis")


def test_mock_red_flag_has_no_medicine_and_preface():
    msg = "My 2 months old baby has a fever"
    flags = safety.detect_red_flags(msg)
    ev = mock.respond(msg, "S01", "CR-0001", flags, [], safety.child_age(msg))
    first = next(e for e in ev if e["type"] == "text")
    assert "doctor" in first["text"][:60]
    for e in ev:
        if e["type"] == "action":
            assert all(it["product"].get("drug_class") not in safety.MEDICINE_CLASSES for it in e["args"]["items"])


def test_mock_crisis_no_products():
    flags = safety.detect_red_flags("I want to die")
    ev = mock.respond("I want to die", "S01", "CR-0001", flags, [], None)
    assert not any(e["type"] == "action" for e in ev)
    assert "03-5774-0992" in "".join(e.get("text", "") for e in ev)


def test_filter_items_abuse_removes_abuse_control():
    items = [{"product": {"id": "cough", "drug_class": D2, "category_path": "Health & Medicine > Throat & Cough > Cough Suppressants",
                          "abuse_control": True, "title": "cough"}},
             {"product": {"id": "candy", "drug_class": "", "category_path": "Snacks & Sweets > Candy > Throat Drops", "title": "candy"}}]
    kept, notes = safety.filter_items(items, [], abuse=True)
    assert [i["product"]["id"] for i in kept] == ["candy"] and notes


def test_stream_abuse_preface_first(client):
    from conftest import sse_events

    r = client.post("/api/agent/stream", json={"session_id": "pytest-abuse", "message": "I want 5 boxes of cough medicine",
                                               "store_id": "S01"})
    ev = sse_events(r.text)
    first = next(e for e in ev if e["type"] == "text")
    assert "one per customer" in first["text"]
    texts = [e["text"] for e in ev if e["type"] == "text"]
    assert sum("one per customer" in t for t in texts) == 1


def test_post_filter_keeps_harmless_best():
    assert safety.sanitize("I'll show you the best route.") == "I'll show you the best route."
    assert "best cold medicine" not in safety.sanitize("This is the best cold medicine.")
