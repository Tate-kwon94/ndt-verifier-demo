# NDT Billing Verifier — synthetic-data demo

*An LLM-assisted pipeline that checks a contractor's non-destructive-testing (NDT) invoices against drawings, site procedures and inspection reports — and refuses to accuse until the evidence chain is complete.*

> **Everything in this repository is synthetic.** Drawings, procedures, inspection reports, billing sheets, company and project names are all generated for the demo. No real project data, standards text, or internal infrastructure is included. Standards are referenced **by number and clause only**.

---

## What it does

A nuclear-plant owner receives, every billing round, an Excel sheet of NDT work the contractor claims to have performed: hundreds of rows of *joint × method × result*. Checking each row by hand against the design drawings, the site welding-execution procedure (SCWEP) and the inspector's PDF reports is slow and error-prone. This tool automates the three-way reconciliation and produces a reviewed Excel and a criteria handbook.

```
drawings (DC/SD/BG) --+
SCWEP procedure ------+--> requirements DB --+
standards (cited) ----+                      |
                                             v
billing Excel --> parse --> match --> compliance --> verdict --> reviewed Excel + guide
inspection PDF --> OCR/segment ----+           ^
                                          basis gate (SCWEP)
```

Each billing row gets one of three verdicts — **OK / SUSPECT / NONCOMPLIANT** — plus a risk score, the evidence cited, and a recommended action.

## Run the demo (offline, about 10 seconds)

```bash
python -m venv venv && source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
python samples/synthetic/run_demo.py
```

`run_demo.py` generates the synthetic documents, then runs the exact CLI a reviewer would use: `ingest-drawings` → `ingest-standards --type scwep` → `review`. `NDT_HCX_MOCK=1` makes every LLM stage return the canned fixtures in `tests/fixtures/hcx_mock.json`, so the run is deterministic and needs no API key or network.

The reviewed Excel lands next to the billing sheet. This is what the six demo rows produce:

| Report | Method | Billed result | Drawing requires | Verdict | Basis state | Risk | Citation |
|---|---|---|---|---|---|---|---|
| 12-001RT | RT | Accept | RT, UT | **OK** | – | 5 | – |
| 12-002UT | UT | Accept | RT, UT | **OK** | – | 5 | – |
| 12-003PT | PT | Accept | RT, UT | **SUSPECT** | SCWEP basis found — confirm the condition | 40 | MD-SCWEP-P1-007 |
| 12-004MT | MT | Accept | RT, UT | **SUSPECT** | SCWEP basis found — confirm the condition | 40 | MD-SCWEP-P1-007 |
| 12-005VT | VT | Accept | RT, UT | **NONCOMPLIANT** | No basis — over-billing | 65 | – |
| 12-006RT | RT | **Reject** | RT, UT | **NONCOMPLIANT** | – | 5 | – |

Rows 3–5 are the interesting ones. All three bill a method the drawing does not require. The tool does **not** call any of them over-billing on that fact alone. It first reads the submitted SCWEP:

* **PT after lug removal** and **MT after repair welds** are conditional requirements the procedure carries and the drawing never will. Both rows are marked *basis found*, the clause is cited, and the reviewer is asked to confirm the condition actually occurred — not to demand a refund.
* **VT** is mentioned nowhere in the procedure. Only then — with a submitted, in-scope, confidently extracted procedure that is silent — does the verdict become NONCOMPLIANT.
* Row 6 shows a hard rule (billed *Reject* vs. report *Accept*) still going straight to NONCOMPLIANT. The basis gate caps the LLM's vote; it never lowers a deterministic violation.

## The four basis states

When a billed method is absent from the drawing, the row is classified before any accusation is made:

| State | Meaning | Verdict |
|---|---|---|
| `covered` | A conditional requirement in the submitted SCWEP names this method, with a trigger and a quote, above the confidence threshold | SUSPECT + citation |
| `unclear` | The method is mentioned only in general rules or sampling rates, or the hit is low-confidence | SUSPECT — check the clause |
| `not_submitted` | No SCWEP, scope unknown, legacy extraction schema, or empty extraction | SUSPECT — ask the contractor to submit the basis |
| `no_basis_found` | Submitted · in scope · conditional-aware schema · rules actually extracted · silent on this method | **NONCOMPLIANT** |

The order is the safety property: *a low-confidence or missing basis can never produce an accusation.* A single procedure clause also cannot quietly exonerate a whole round — the round summary reports how many rows each document covered and warns when one document dominates.

Every switch that gates an accusation lives in `config/matching_rules.yaml` under `compliance.overbilling_claim`; set them all to `false` and the tool reverts to "not in drawing ⇒ over-billing" with no code change.

## Three things this project taught me

**The confidence yardstick was blind.** Tables in scanned standards lose columns silently under OCR (9 of 10 columns on one page, no error raised). I added a vision-model transcription with a digit cross-check against OCR: any number the model wrote that OCR never saw is flagged as invented. The first real run flagged one page at confidence 0.16 with "43 invented numbers". Before lowering the threshold I opened the page: the *reference text layer* held 20 digits, a fresh OCR pass held 147. The yardstick was the thing that was wrong. The fix was to build the reference from the union of independent readings. A confidence metric is only as good as what it is measured against — verify the ruler before tuning the threshold.

**A search that returned zero tokens for months.** The standards retriever tokenized Latin script only, so every Korean query produced an empty candidate list and the compliance stage silently reasoned over no evidence. Nothing errored. The rebuild uses a multilingual tokenizer (Latin, Cyrillic, Hangul with 2-grams, numeric codes with head segments), BM25 with proper IDF, dense embeddings when available, and reciprocal-rank fusion — plus a test that asserts a Korean query yields tokens.

**Domain knowledge beat ten code reviewers.** A ten-lens automated audit of this codebase (115 findings, each adversarially verified) did not catch the most consequential defect: conditional requirements from the site procedure were structurally invisible, so a legitimate PT after lug removal would have been branded over-billing. It surfaced from a one-sentence explanation by the domain expert. The basis gate above is the result — designed as three independent proposals, each risk-reviewed, then merged, and pinned first by 49 characterization tests so the change was provably scoped.

## Layout

```
app/analyzers/     compliance.py (rules), scwep_basis.py (basis gate), pipeline.py (verdict + round rollup)
app/extractors/    excel, PDF/OCR, report segmentation, drawing set (DC/SD/BG), SCWEP, table transcription, retrieval
app/matchers/      deterministic -> fuzzy -> LLM judge
app/report/        reviewed Excel writer, criteria guide (xlsx + html)
config/            matching_rules.yaml, templates.yaml, hcx.yaml, prompts/*.md
samples/synthetic/ pdfmini.py (dependency-free PDF writer), make_synthetic.py, run_demo.py
tests/             end-to-end synthetic round + characterization tests that pin behaviour before each refactor
```

## What is deliberately not here

* Real drawings, reports, billing sheets, procedures, or any document from a real project.
* Standards text (GOST, SP, PNAE, ASME, …). They are copyrighted; the code cites them by **number and clause** and the demo fixtures carry citations only.
* Internal endpoints or credentials. The LLM client targets a placeholder host and reads tokens from environment variables; the demo never contacts a network.
* The in-house deployment tooling (offline installers, import-gate checker, patch packager) — specific to a closed corporate network.

Under mock mode the *explanation* column shows canned text; in production that column is written by the LLM per row.

## Korean operator guide

`시작하기.md` is the operator-facing guide (Korean), kept as it is used in practice with names replaced.

---
*MIT licensed. Synthetic data generated by `samples/synthetic/make_synthetic.py`.*
