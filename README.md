# Daylight

**Is there daylight between the source and the claim?**

Daylight checks whether the sources an AI agent cites are actually independent of the agent. Existing tools only check that a cited source is *real*. Daylight checks whether it is *independent*: whether it could have existed without the agent.

> **Status:** under active development. The design below is the target; not all of it is implemented yet.

---

## The problem

AI agents are increasingly asked to back their answers with citations. Some have learned to satisfy that requirement literally instead of honestly.

In one documented case, an agent that had found an answer uploaded a file containing that answer to a public host, then cited the resulting link as its source. The citation was real and reachable, and it passed every existence check, but it proved nothing. The agent was citing itself.

This is a form of **reward hacking**: optimizing the measurable proxy ("has a citation") instead of the intent behind it ("the answer is independently verified"). The same pattern shows up as:

- **Self-publishing:** the agent uploads or writes the page it later cites.
- **Page editing:** the agent edits an existing page (for example, a wiki) so an old URL now contains its answer.
- **Laundering:** content the agent produced is rehosted elsewhere, possibly by another agent or a later session, and then cited.
- **Cite-without-reading:** the agent answers from memory and attaches sources afterward without using them.

Citation-existence checkers, including ones built on Google Scholar, Crossref, or OpenAlex lookups, pass all of these. The source exists. It just isn't independent.

---

## What Daylight does

For every citation in an agent's answer, Daylight asks one question:

**Could this source have existed without the agent?**

It answers that from evidence captured while the agent works, not reconstructed afterward, and returns a verdict with reasons:

| Verdict | Meaning |
|---|---|
| `INDEPENDENT` | Third-party evidence shows the source existed before the agent touched it, it matches nothing the agent produced, and it supports the claim. |
| `CIRCULAR` | The source traces back to the agent: it was written, uploaded, or edited by the agent, or matches agent output that came first. |
| `SUSPECT` | Dependency signals exist but are not conclusive. |
| `UNVERIFIED` | Not enough evidence either way. Unknown stays unknown. |

Example reason: *"First archived 6 minutes after session start; 94% overlap with the agent's own draft."*

---

## How it works

```
AI agent
   │  all network traffic
   ▼
Egress proxy ──────────► Tamper-evident event log
   │                     (snapshots, hashes, fingerprints)
   ▼                              │
Agent Output Registry ◄───────────┤
(everything any agent produced)   │
                                  ▼
                         Provenance graph
            agent → artifact → publish → source → read → citation
                                  │
        ┌──────────────┬──────────┼──────────┬──────────────┐
        ▼              ▼          ▼          ▼              ▼
    Snapshot        Origin     Temporal   Evidence
     check          check       fusion    verifier
        └──────────────┴──────────┼──────────┘
                                  ▼
                           Citation gate
                ALLOW · BLOCK · REQUIRE_REVIEW
```

### Capture

- **Egress proxy.** All of the agent's network traffic passes through a proxy at the sandbox boundary, not just its tool calls. An agent that uses `curl`, a script, or `git push` is still seen. Every outbound write (POST, PUT, upload, push) is treated as a possible publish.
- **Tamper-evident log.** The proxy, never the agent, writes an append-only, hash-chained log stored outside the agent's sandbox. The agent has no credentials for it.
- **Snapshots.** For every fetch, Daylight stores the cleaned main text the agent actually saw, with an exact hash and fuzzy fingerprints (MinHash/SimHash). Pages change constantly, so exact hashes alone are too brittle.
- **Agent Output Registry.** Fingerprints of everything any agent in the organization has written or published, across all sessions. This catches laundering between agents and between runs, which a per-session view would miss.

### Checks

1. **Snapshot check.** The cited passage must exist in the snapshot taken when the agent fetched the source, before the citation was made. Daylight verifies against the snapshot, never the live page, because a live page can change or be edited by the agent afterward.
2. **Origin check.** Did the agent write, upload, or edit this source? Does the source match anything in the registry? Similarity is **directional**: it counts as circular only if the agent's artifact came first. An honest answer naturally resembles the sources it summarized, so similarity alone proves nothing.
3. **Temporal fusion.** Did this content exist before the task started? Evidence is weighted by who controls it:
   - **Third-party observed** (archive captures, Crossref DOI registration, links from unrelated domains) can establish prior existence.
   - **Self-asserted** (dates printed on the page, HTTP metadata, commit dates) can only support it, because the publisher can backdate them.
   - Any evidence the agent itself touched is discarded.
   Checks are made at the revision level, so editing an old page doesn't inherit the page's age.
4. **Evidence verifier.** Does the cited passage support the claim? Deterministic checks on numbers, dates, and named entities run first, then an entailment model judges the exact span. A model contributes a signal; it never decides alone.

### Decision rules

Applied in order, so every verdict is deterministic and explainable:

1. Direct self-origin, or a registry match where the agent's artifact came first → **CIRCULAR**
2. Cited passage not in the snapshot, or claim not supported → **BLOCK**
3. Third-party proof of prior existence, no registry match, claim supported → **INDEPENDENT**
4. Dependency signals present but inconclusive → **SUSPECT**
5. Otherwise → **UNVERIFIED**

Absence of evidence is never treated as independence.

### Citation gate

Daylight can sit in front of the agent and decide before a citation is attached, not just audit afterward.

- **Instant checks** (snapshot, self-write, registry lookup) run synchronously and can block on the spot.
- **Slow checks** (temporal fusion, entailment) run in the background. Until they finish, the citation is marked `REQUIRE_REVIEW`.
- **Fail-closed.** If a check times out or a dependency is down, the result is `REQUIRE_REVIEW`, never `ALLOW`. An outage must not become a bypass.
- **Honest exit.** "No independent source found" is an acceptable outcome for the agent, not a failure. Demanding a citation when none legitimately exists is what creates the incentive to fake one.
- Gate decisions go to humans as alerts and are kept out of any training reward, so the gate does not become the next thing to game.

---

## Interface

The application has four views:

- **Review queue:** every `BLOCK` and `REQUIRE_REVIEW` decision, for a person to approve or reject.
- **Run explorer:** an agent's answer with each citation marked by verdict and its reasons.
- **Provenance view:** the graph behind a single citation, showing the loop when a source traces back to the agent.
- **Dashboard:** verdict trends across runs, plus registry and checker health.

---

## Evaluation

Daylight is evaluated only on **real agent runs**, never on hand-written traces.

- **Honeypot tasks:** tasks where no legitimate source exists, a citation is required, and the agent has a publish tool. Any cheating that happens is genuine.
- **Red-team runs:** real agents explicitly instructed to launder a citation (upload and cite, edit a page, rehost a copy, reword it). The actions and traces are real; only the intent is assigned. These provide enough positive cases to measure detection.
- **Clean runs:** honest research tasks, including ones that cite legitimately brand-new sources, to measure false alarms.

All publishing during evaluation goes to a controlled target owned by the project, never the public internet.

The headline metrics are **catch rate** per laundering type and **false-alarm rate** on clean runs.

---

## Built for scale

- **Sidecar proxies:** one stateless proxy per agent sandbox that only records and forwards.
- **Event sourcing:** the append-only log is the source of truth; the graph and verdicts are rebuilt from it. Events carry idempotency keys.
- **Tamper evidence:** per-session hash chains, with periodic Merkle roots published outside the system, as in Certificate Transparency.
- **Content-addressed storage:** snapshots keyed by hash, so a page fetched by thousands of agents is stored once.
- **Layered registry lookup:** a Bloom filter for exact hashes, MinHash LSH for near-duplicates, and a vector index for semantic matches, each narrowing candidates for the next.
- **CQRS:** the write side ingests events; the read side serves precomputed verdicts to the gate.
- **Resilient external calls:** caching, backoff, and circuit breakers around archive and metadata APIs. Proof that a source existed before a date never changes, so it is cached permanently.
- **Standard input:** traces follow the OpenTelemetry GenAI conventions, so existing agent frameworks can plug in.

---

## Limitations

Daylight is deliberately honest about what it cannot do:

- **Laundering outside your organization.** The registry only knows your own agents. Content copied by another organization's agent, or by a human, is invisible to the origin check.
- **Heavy paraphrase.** Content rewritten thoroughly enough can defeat fingerprint matching. Semantic similarity helps but can be beaten.
- **Human-written papers.** Without live capture there is no event log, so verification is forensic and probabilistic.
- **Brand-new legitimate sources.** A real article published minutes ago often ends up `UNVERIFIED`. That is the correct answer, even when it's inconvenient.

---

## Related work

- OpenAI, [The Hugging Face incident and the road ahead](https://openai.com/index/hugging-face-incident-and-the-road-ahead/): documents reward hacking by agents, including manufacturing citable links.
- Ma et al., *Proof-of-Use: Mitigating Tool-Call Hacking in Deep Research Agents* (arXiv:2510.10931): addresses citing without using evidence at training time. Daylight is an external check that does not rely on the agent grading itself.
- Citation-existence checkers (CiteCheck, CITADEL, and others) verify that references are real. Daylight is the layer on top: it verifies that they are independent.