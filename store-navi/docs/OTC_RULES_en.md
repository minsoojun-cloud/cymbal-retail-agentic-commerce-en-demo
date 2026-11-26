# OTC rules for the in-store AI Concierge (English demo)

Enforced in the agent instruction (`app/agent/agent.py`) **and** server-side (`app/agent/safety.py`).

| Rule | What the concierge does | Server enforcement |
|---|---|---|
| No diagnosis | Offers general information and "products that match your symptoms" only; never "you have the flu". | `TextFilter` rewrites diagnosis / guarantee / superlative claims ("will cure", "guaranteed", "no side effects", "the strongest", "No. 1", "doctors recommend"). |
| Severe or persistent symptoms | Recommends seeing a doctor **before** any product (fever 38.5 °C+ for 3+ days, 41 °C+, breathing trouble, chest pain, seizure, infant < 3 months with fever, symptoms 5+ days or worsening, bloody/black stool, side-effect signs). Emergency → 119; unsure → #7119. | `detect_red_flags` → preface emitted before model text; medicines removed from suggestions (`filter_items`). |
| Self-harm / overdose signals | No products; shares TELL Lifeline (03-5774-0992) and 119. | Crisis turns never reach the model. |
| Pregnancy, breastfeeding, chronic conditions, other medicines | "Please talk to a doctor or pharmacist before use." | Consult-level preface. |
| Pharmacist-only (Class 1) | "A pharmacist needs to explain this medicine at purchase; not sold when no pharmacist is on duty." | `compliance_notes`, `item_limits.pharmacist_required`. |
| Abuse-prone OTC (`abuse_control`) | One per customer (under 18: one small pack, age check); multi-pack / long-term / off-label requests → pharmacist. | `detect_abuse` → guidance first; such items filtered out. |
| One medicine per symptom group | Never a cold medicine plus a pain reliever (duplicate ingredients). | `symptom_group` on the English category path. |

Disclaimer (shown under every health answer): *This service provides general information only and does not replace a
diagnosis by a doctor. …*
