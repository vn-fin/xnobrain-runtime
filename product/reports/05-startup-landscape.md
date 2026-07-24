# 05 · Startup Landscape — Who's Nearest, and the Whitespace

> The companies closest to Brain4All's vision, sorted by how directly they touch each
> engine, plus the explicit gap you can own. Funding/traction figures are linked in
> [04-market-and-numbers.md](04-market-and-numbers.md).

## 1. The map: nobody sits where you're aiming

Brain4All's vision spans three things most startups do **only one** of:
**(A)** a self-hostable agent runtime, **(B)** cloning a *named expert's* judgment,
**(C)** a certified marketplace with royalties. The nearest players each cover one or
two — the intersection is empty.

```
              (A) self-host runtime         (B) expert clone            (C) certified marketplace + royalty
Hermes / Brain4All  ●●●                          ○ (twin = planned)          ○ (planned — the bet)
OpenClaw            ●●●                          ○                            ○
Delphi              ○                            ●●● (persona/knowledge)      ● (creator monetization)
Cloneable           ● (deploys agents)           ●●● (shadows field experts)  ○
Personal AI/Kamoto  ○                            ●● (persona)                 ● (subscription/IP license)
Salesforce AgentX   ○                            ○                            ●● ("trusted", partner-vetted)
Eval startups       ○                            ○                            ○ (measure app quality, not fidelity-to-person)
```

**● = strong, ○ = weak/none.** The row that has all three does not exist yet. That is
the Brain4All bet.

## 2. Closest analogues, and what to steal from each

### Delphi — closest on "expert twin as product"
Turns experts into "digital minds"; Sequoia-backed; creator-monetization angle.
**Steal:** the *"digital mind that scales one person's communication"* framing lands
with experts and investors. **Beat them on:** they don't publicly *certify fidelity*
or run a *cross-org royalty registry* on the customer's own data/runtime — they're a
hosted consumer/creator product. Your enterprise self-host + certification + royalty
lineage is a different, more defensible layer.

### Cloneable — closest on "clone real expert judgment for enterprise"
Shadows field experts in utilities/energy, redeploys as agents; real enterprise
customers (AEP, SCE); huge ARR growth. **This is the strongest proof your thesis
works** — enterprises pay to capture and redeploy scarce expert judgment.
**Steal:** their vertical wedge discipline (one industry, deep). **Beat them on:**
they're vertical-services-heavy (they do the shadowing). Your terminal is
**industry-agnostic** because you certify *fidelity*, not *domain quality* — the exact
scoping insight in the vision doc. You can be the horizontal rail they (or their
competitors) list on.

### OpenClaw — closest on "self-hosted personal agent, massive adoption"
160k+ stars proves the self-host demand. **Steal:** config-first simplicity
(`SOUL.md`) as an onboarding ideal — twins should be that easy to author. **Beat them
on:** they have no fidelity/trust/registry layer at all.

### Salesforce AgentExchange — closest on "trusted agent marketplace at scale"
Fastest-growing product in Salesforce history; explicitly *"trusted."* **Steal:** the
distribution-channel playbook and the fact that buyers demand *trust* signals.
**Beat them on:** their "trust" = partner vetting + security review, **not** measured
fidelity to a named human. And it's locked to the Salesforce ecosystem.

### Personal AI / Kamoto / Miria — persona clones + monetization models
**Steal:** subscription + IP-licensing + platform/expert revenue-share mechanics — the
royalty plumbing you'll need. **Beat them on:** consumer-persona focus, no
enterprise-grade certification or on-customer-data runtime.

## 3. The whitespace, stated precisely

> **No company today offers: a certification gate that measures whether an AI twin
> faithfully reproduces a *specific named expert's* judgment on held-out cases, issues
> an *expiring* trust badge, monitors drift, and settles *royalties* when that twin is
> rented across organizations — while the twin runs on the *customer's own data and
> runtime*.**

Every neighbor touches a piece:
- Cloneable/Delphi clone experts — but don't *certify fidelity with expiring badges*.
- Eval startups measure faithfulness — but of *app output vs. ground truth*, **not of
  a clone vs. the person** (and LLM-judge bias makes even that hard).
- Marketplaces vet partners — but don't measure *fidelity to a human*.
- Routers/runtimes are commodity plumbing.

## 4. Risks the landscape reveals (be honest)

1. **Fast-funded, fast-moving neighbors.** Delphi (Sequoia) and Cloneable (100× ARR)
   could extend toward certification. Your defensibility is being *first to define the
   fidelity standard* and accumulating the craft-ledger + royalty-lineage data network.
   Speed on engine 07 matters.
2. **Marketplace commodification.** GPT Store shows marketplaces race to $0. Avoid
   "list a million cheap agents." Your value is *scarcity + verification + royalty*,
   not volume.
3. **Verification is genuinely hard.** LLM-as-judge has known biases. Your certification
   methodology must be defensible or the whole moat is hollow — treat it as core R&D,
   not a feature. See [06-certification-moat.md](06-certification-moat.md).
4. **Supply consent (the vision's "supply wall").** Cloneable solves it by targeting
   *retiring* expertise (knowledge-loss framing) — experts near exit consent readily.
   Consider the same wedge: capture craft that's *about to walk out the door*.
