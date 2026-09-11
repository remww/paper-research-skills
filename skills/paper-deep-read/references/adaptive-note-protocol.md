# Adaptive Paper Note Protocol

This is a template-generation protocol, not a reusable paper-note template. The
agent must design the note for the current research question before doing the full
read.

## A. Convert the research direction into a reading contract

Write one sentence for each item:

- **Research direction:** the broader problem being investigated.
- **Current question:** the uncertainty this paper may reduce.
- **Decision:** what could change after reading it.
- **Paper role:** why this source was selected, such as nearest prior work,
  mechanism evidence, baseline candidate, counterexample, measurement source, or
  background map.
- **Required depth:** targeted, full, full plus supplement, or full plus code.
- **Stop condition:** what evidence is sufficient, and what is outside this read.

If these cannot be inferred safely, clarify the research question before reading.
Formatting and storage choices are secondary and should follow the user's existing
conventions.

## B. Generate focus questions

Create 3-7 questions whose answers would change the stated decision. Good focus
questions are falsifiable or evidence-seeking. They are not generic prompts such as
"What is the methodology?"

Derive each question from the intersection of:

1. the active research question,
2. the paper's claimed contribution,
3. the decision the note must inform,
4. known uncertainties or competing explanations,
5. the kind of evidence this paper can realistically provide.

For each focus question, predeclare:

- what observation would answer it,
- what source location is likely to contain that observation,
- what assumptions or confounders must be checked,
- what result would leave it unresolved.

### Question-shape examples

These are transformations, not headings to copy:

- A causal question should generate checks for intervention, control, alternative
  explanation, effect, and identification limits.
- A reproducibility question should generate checks for artifact identity,
  dependencies, data availability, evaluator behavior, and missing execution steps.
- A comparison question should generate checks for matched conditions, baseline
  strength, metric definition, uncertainty, and fairness of the comparison.
- A theoretical question should generate checks for definitions, assumptions,
  proof dependencies, boundary cases, and whether the claimed consequence follows.
- A measurement question should generate checks for construct validity, population,
  instrumentation, aggregation, uncertainty, and failure cases.
- A synthesis question should generate checks for inclusion criteria, evidence
  quality, disagreement handling, and whether primary sources support the synthesis.

Choose only the shapes that fit the actual question and source.

## C. Generate the note structure

The note structure has two layers.

### Universal evidence core

Include these facts somewhere, using the user's preferred format:

- bibliographic identity and persistent identifiers,
- exact source version and retrieval location,
- actual read scope,
- active research direction and paper role,
- evidence locators,
- non-findings and limitations,
- assessment date or scope,
- downstream implications and re-read triggers.

### Research-specific modules

Generate one module for each focus question or tightly related group. Name modules
using the concepts in the current research direction, not generic labels copied from
this guide. Each module should make room for:

- the source observation,
- exact locator,
- conditions and assumptions,
- the agent's interpretation,
- confidence or unresolved issue,
- effect on the current decision.

A module may be a prose section, table, claim-evidence matrix, checklist, structured
data block, or another format suitable for the user's workflow. Do not require
Markdown if the project uses a different durable format.

Delete any generated module that does not serve a focus question. Add a module only
when the paper provides unexpected evidence relevant to the decision, and record why
it was added.

## D. Match read depth to the claim

Use honest depth labels in the note. Names may follow the user's convention, but the
meaning must be explicit.

- **Metadata:** identity and bibliographic fields only.
- **Abstract:** abstract and metadata only.
- **Targeted:** named passages or artifacts inspected for specific questions.
- **Full:** all main sections plus relevant figures, tables, limitations, and
  appendices inspected.
- **Full plus code:** full paper read plus the code paths needed for the current
  decision inspected.

Never label a read as full because the document was loaded into context. Record what
was actually inspected. A close prior-art threat, a baseline selection, a
load-bearing numerical claim, or a claimed paper-code equivalence normally needs a
fuller read than background context.

## E. Capture evidence precisely

For each important claim, preserve:

- source-native locator,
- exact object or population,
- method or comparison that supports it,
- metric definition and direction,
- value, units, uncertainty, and sample size when applicable,
- baseline and evaluation conditions,
- stated limitation,
- whether the claim is reported by the source or inferred by the agent.

Use short quotations only when wording matters. Prefer accurate paraphrase plus an
exact locator. For visual evidence, inspect the figure or page itself. For code
claims, record repository revision and file-level location when available.

Do not improve, average, interpolate, or normalize a reported number unless the note
explicitly labels the transformation and preserves the original value.

## F. Write what the paper does not establish

This is mandatory because later research often fails by treating absence as proof.
Record, as applicable:

- questions the experiment cannot answer,
- populations, settings, or scales not tested,
- missing controls or unavailable artifacts,
- claims supported only by correlation or qualitative evidence,
- comparisons that do not hold conditions fixed,
- conclusions that are plausible but not identified,
- uncertainty introduced by inaccessible pages, figures, supplements, or code.

Do not turn a missing result into a negative result.

## G. Produce a reusable research handoff

End with a scoped assessment that answers:

1. What durable fact from this paper matters to the research direction?
2. Which current assumption, hypothesis, baseline, or design choice does it support,
   weaken, or leave unchanged?
3. What evidence remains missing?
4. What is the cheapest next action that would reduce that uncertainty?
5. Under what future condition should this paper be re-read?

The assessment belongs to the current research context and may age. Keep it separate
from stable paper facts, and preserve prior assessments rather than silently
rewriting them when the project changes.

## H. Final self-review rubric

A useful note should score yes on all of these:

- **Relevant:** custom focus questions trace to an active research decision.
- **Grounded:** important conclusions have exact evidence locators.
- **Scoped:** conditions, assumptions, and non-findings are explicit.
- **Honest:** read depth and unresolved access gaps are accurate.
- **Discriminating:** observation is separate from interpretation.
- **Reusable:** stable paper facts survive a change in project direction.
- **Actionable:** the handoff says what this evidence changes and what to do next.
- **Lean:** no empty boilerplate or unrelated catalog of paper details remains.

If a criterion fails, repair the note or mark the limitation before handing it off.
