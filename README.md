# NDT Billing Verifier

One line: cross-checks a contractor's non-destructive-testing invoices against drawings, site procedures and inspection reports — and refuses to accuse until the evidence chain is complete.

> **Synthetic data only.** Every drawing, procedure, inspection report, billing sheet, company and project name in this repository is generated. No employer or project material appears here. Standards are referenced by number and clause only.

## Problem

On a large construction project, a contractor invoices for non-destructive testing (NDT) — radiographic, ultrasonic, penetrant, magnetic-particle and visual examination of welds. Every billing round arrives as a spreadsheet of hundreds of rows: *joint × method × result*. Someone on the owner's side has to check each row against three separate document families:

- **design drawings**, which say what inspection each joint requires,
- **the site welding-execution procedure**, which adds requirements the drawings never carry,
- **the inspector's report PDFs**, which say what was actually performed and what it found.

Done by hand this is slow, and it fails in a specific direction: it is far easier to miss an overcharge than to notice one. But the opposite error is worse. Telling a contractor they over-billed when a procedure clause required the work is a claim you cannot defend in the meeting that follows.

## Approach & Architecture

```
drawings (DC/SD/BG) ──┐
site procedure ───────┼──▶ requirements DB ──┐
standards (cited) ────┘                      │
                                             ▼
billing sheet ──▶ parse ──▶ match ──▶ compliance ──▶ verdict ──▶ reviewed sheet + guide
report PDFs ──▶ OCR / segment ──┘              ▲
                                        evidence gate
```

Each billing row ends as **OK / SUSPECT / NONCOMPLIANT**, with a risk score, the evidence cited, and a recommended action.

Three decisions shaped the design.

**Deterministic rules decide; the language model explains and cross-checks.** Verdicts come from explicit rules over extracted values. The model writes the reasoning and votes independently; where the two disagree the stricter verdict wins and the row is flagged for a human. The model can raise an alarm but cannot clear one.

**Nothing extracted with low confidence may produce an accusation.** OCR, table transcription and document extraction each carry a confidence that propagates to the verdict. A weak extraction downgrades a row to *needs review* rather than letting it assert something about money.

**An accusation requires a positive fact, not an absence.** This is the part I got wrong first and rebuilt. See below.

### The evidence gate

The naive rule — *billed method not in the drawing ⇒ over-billing* — is wrong, and wrong in the expensive direction. Site procedures carry **conditional** requirements that drawings structurally cannot: *after removing a temporary lug, penetrant-test that area*; *re-examine repair welds*. A drawing shows designed geometry, not the removal of temporary attachments. So a perfectly legitimate charge looks like padding.

When a billed method is absent from the drawing, the row is now classified before anything is claimed:

| State | Condition | Verdict |
|---|---|---|
| `covered` | A conditional clause in the submitted procedure names this method, with a trigger and a quotable line, above the confidence threshold | SUSPECT + citation |
| `unclear` | The method appears only in general rules or sampling tables, or the match is low-confidence | SUSPECT — read the clause |
| `not_submitted` | No procedure submitted, scope unknown, legacy extraction, or nothing extracted | SUSPECT — ask for the basis |
| `no_basis_found` | Submitted · in scope · conditional-aware extraction · rules actually present · silent on this method | **NONCOMPLIANT** |

The ordering is the safety property: an absent or weak basis can never reach an accusation. One clause also cannot quietly exonerate a whole round — the run summary reports how many rows each document covered and warns when a single document dominates, or when the accusation path produced nothing at all and why.

Every switch that gates an accusation lives in `config/matching_rules.yaml`. Set them false and the tool reverts to the naive rule with no code change.

## Results

Six synthetic rows, one per verdict path, produced by `run_demo.py`:

| Report | Method | Billed | Drawing requires | Verdict | Basis state | Risk |
|---|---|---|---|---|---|---|
| 12-001RT | RT | Accept | RT, UT | **OK** | – | 5 |
| 12-002UT | UT | Accept | RT, UT | **OK** | – | 5 |
| 12-003PT | PT | Accept | RT, UT | **SUSPECT** | basis found — confirm the condition | 40 |
| 12-004MT | MT | Accept | RT, UT | **SUSPECT** | basis found — confirm the condition | 40 |
| 12-005VT | VT | Accept | RT, UT | **NONCOMPLIANT** | no basis — over-billing | 65 |
| 12-006RT | RT | **Reject** | RT, UT | **NONCOMPLIANT** | – (report says Accept) | 5 |

Rows 3–5 all bill something the drawing does not require, and only one of them is an accusation. Row 6 shows a deterministic violation going straight through — the gate caps the model's vote, it never lowers a rule.

240 tests, including an end-to-end run that pins all six outcomes, and characterization tests written *before* each refactor to prove the change was scoped.

### What I'd do differently

- **Verify the yardstick before tuning the threshold.** I built a hallucination check for table transcription: any number the vision model writes that OCR never saw is flagged as invented. A real page came back at confidence 0.16 with "43 invented numbers" and I nearly lowered the threshold. The reference text layer held 20 digits; a fresh OCR pass on the same page held 147. The measurement was the broken thing. The fix was to build the reference from the union of independent readings — 0.16 became 0.74.
- **A fallback that never errors will hide for months.** The standards retriever tokenized Latin script only, so Korean queries produced an empty candidate list and the compliance stage reasoned over no evidence at all, silently. Now: multilingual tokenizer, BM25 with IDF, dense embeddings with rank fusion, and a test asserting a Korean query yields tokens.
- **Domain knowledge beat the automated review.** A ten-lens audit of this codebase surfaced 115 findings, each adversarially verified. It did not find the conditional-requirement gap above — the most consequential defect in the system. That came from thinking about how the procedure documents actually get used. Static analysis finds what code does; it cannot tell you what the code should have known.

## Run it

```bash
python -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
python samples/synthetic/run_demo.py
```

Generates the synthetic documents, then runs the same CLI a reviewer uses: `ingest-drawings` → `ingest-standards` → `review`. `NDT_HCX_MOCK=1` returns canned model responses from `tests/fixtures/`, so the run is deterministic, offline, and needs no API key. The reviewed spreadsheet lands next to the billing sheet.

```
app/analyzers/     rules, evidence gate, verdict + run summary
app/extractors/    spreadsheet, PDF/OCR, report segmentation, drawing sets, procedures, table transcription, retrieval
app/matchers/      deterministic → fuzzy → model-assisted
app/report/        reviewed spreadsheet, criteria handbook (xlsx + html)
samples/synthetic/ dependency-free PDF writer, document generator, demo runner
```

## Notes

**Data.** Synthetic throughout, generated by `samples/synthetic/make_synthetic.py`. Standards (ASME, GOST, SP, PNAE …) are cited by number and clause; no standard text is reproduced. The deployment tooling for the closed network this runs on is not included.

**Origin.** A personal tool, built to make my own review work tractable. This repository is a sanitized rebuild of it: the same architecture and logic, with every real name, document and endpoint replaced by generated equivalents.

**How it was built.** Design decisions, domain rules and the engineering judgment behind them are mine. Much of the implementation was written with an AI coding assistant, as was some of this README. The interesting parts of the project were deciding what the tool must never claim, and finding the places where it silently claimed it anyway.

---
MIT
