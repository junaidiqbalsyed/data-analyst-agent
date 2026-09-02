Report Guard Rails — enforced in code, not in prompts

What “enforced in code, not in prompts” means

The writer LLM is told the rules in its prompt (e.g. “do not name the provider
here”). But a prompt is a request, not a guarantee — the model ignored an all-caps
instruction and named the provider anyway, and the LLM reviewer approved it.

So the rules that carry real risk are checked in plain Python after the draft
is written. A check like “does the string DAVID JOHNSON appear?” is deterministic:
same input, same answer, every run. If a check fails it returns a specific,
actionable note, the draft is rejected, and the loop sends it back to be rewritten.

Two layers review every section, in this order:

1. Guard rails — the deterministic checks in this document. Run first.
2. LLM rubric — judges tone, fidelity, intent, concision. Runs only if the
guards pass (writer.py:776, _llm_review).

Guards are cheap and run first; a failed guard short-circuits before any model
call is spent on the rubric.

> Note on “seven”: the leadership slide groups these as *seven guards*. In the code
> there are a few more distinct checks than seven — the slide count is a
> simplification. The full, faithful list is below.

────────

Entry point

SectionCritic.review() — writer.py:752

```
review(spec, context_blob, draft, intel):
    1. identity guard        writer.py:760   -> identity_leaks()   :418
    2. structure guards      writer.py:767   -> structure_gaps()   :464
    3. LLM rubric            writer.py:774   -> _llm_review()       :776   (only if 1 & 2 pass)
```

The identity guard is separate and runs first. Everything else is bundled
inside structure_gaps(), which runs its checks in the order listed below and
returns every failure at once.

────────

The guards, in execution order

0. Identity guard  —  identity_leaks(), writer.py:418

Runs before structure_gaps, only on sections flagged forbid_subject_identity
(the Report Introduction and How-this-Concern-was-Identified sections).
Rejects the draft if the provider’s name, surname, NPI, or licence number
appears. This is the guard that exists because the prompt alone failed.
Safety net: redact_identity() (writer.py:442) scrubs any leak that survives all
loop passes, called from orchestrator.py:105.

────────

Inside structure_gaps() (writer.py:464), in the order they execute:

1. Bullets required  —  inline check, writer.py:473

If the section is marked require_bullets, rejects a draft with no Markdown
-  list. Forces enumerated findings into a bulleted layout.

2. Citation required  —  inline check, writer.py:481

If the section is marked require_citation (the Report Introduction), rejects a
draft with no ](http...) Markdown link. Forces the public cases to be cited.

3. Subheadings required  —  inline check, writer.py:487

For each heading the section requires (spec.require_subheadings), rejects the
draft if that ##  subheading is missing.

4. Model coverage  —  _model_coverage_gaps(), writer.py:695

For every model that returned findings: rejects the draft if the model is not
covered at all, or is named but missing its review period or dollar impact.
Ensures each in-scope model gets its own subsection with real figures.

5. Claim period  —  _claim_period_gaps(), writer.py:662

Rejects the Analysis draft if the provider-wide claim totals are stated without
their time period, or if the period is given without the word “inferred”. The
totals carry no dates of their own, so the window must be labelled inferred, not
presented as a reported reporting period.

6. Out-of-scope models  —  _out_of_scope_model_gaps(), writer.py:623

Rejects a draft that names a model the API returned no data for, or that cites
a model count different from the number actually covered (e.g. “all 8 models”
when only 4 returned findings).

7. Author voice  —  _voice_gaps(), writer.py:606

Rejects phrasing that reveals the report was machine-written — “this report was
generated”, “as an AI”, “the system identified”. Regex _MACHINE_VOICE at
writer.py:597. The report must read as Gainwell speaking.

8. Jargon / abbreviations  —  _jargon_gaps(), writer.py:570

Rejects unexpanded insider shorthand — DOJ, DOS, FCA, OIG, DME, TIN, POS. Map at
writer.py:559. URLs are stripped first (writer.py:576) so a domain like
oig.hhs.gov does not false-trigger; only shorthand the reader sees is flagged.

9. Dead links  —  _dead_link_gaps(), writer.py:534

Rejects a Markdown link whose URL does not resolve (is_resolvable_url,
citations.py). Synthetic watchlist references look authoritative but open to
nothing, so they must be named in prose instead of hyperlinked.

10. Peer / benchmark claims  —  _peer_claim_gaps(), writer.py:517

Rejects any peer or benchmark comparison — “compared to peers”, “peer average”,
“above the benchmark”. Regex _PEER_CLAIM at writer.py:507. Peer benchmarking
was removed (the dataset had at most two same-specialty providers), so any such
claim would be invented.

────────

Supporting helpers

|Helper               |Line           |Used by                                          |
|---------------------|---------------|-------------------------------------------------|
|`_mentions_period()` |`writer.py:724`|model coverage — is the period in the text?      |
|`_mentions_paid()`   |`writer.py:732`|model coverage — is the paid figure in the text? |
|`_figure_present()`  |`writer.py:737`|matches a number with or without thousands commas|
|`_inferred_period()` |(same module)  |claim period — computes the inferred window      |
|`is_resolvable_url()`|`citations.py` |dead links — does the URL resolve?               |

────────

What happens on a failure

Each guard returns a list of human-readable notes. review() collects them,
returns accept = False with the notes as the critique, and the loop
(adk_agents.py) calls writer.revise() with those notes on the next pass. On
acceptance the critic sets escalate=True (adk_agents.py:107), ending the loop.
If the loop never converges, max_iterations=3 (factory.py:35) caps it and
redact_identity is the final safety net.

────────

One-line summary for the room

> The prompt asks the writer to follow the rules; the guard rails prove it did.
> The ones that would embarrass us in front of a client — a leaked name, a dead
> citation, an invented benchmark, a wrong model count — are checked in code, where
> the answer is the same every single run.