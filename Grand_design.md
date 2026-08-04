# What I was trying to build

I was not trying to build another coding agent.

I was trying to build a **reusable software-development execution system** that converts a high-level product goal into an implemented, tested, integrated codebase by coordinating multiple AI coding agents.

The working name became **RailWarden — the agent factory**.

Its purpose was:

> Enter any repository, describe the desired outcome once, and have the system plan, decompose, assign, execute, validate, review, merge, and track the work across multiple agents.

I am going to use an example project "TMOM" to convery how this agent factory was supposed to work. Consider this to be only a working example throughout this file. This is being used to show how this project was supposed to be built and what the use case would be like. 

---

# 1. The user experience I wanted

The intended workflow was approximately:

```bash
cd ~/CODE/Tmom_Deviation
warden launch
```

Then I would tell Hermes something like:

> Finish the brokerless TMOM MCP control plane according to the architecture and make it production-ready.

From there, the system should:

1. Understand the repository and existing state.
2. Produce or update the implementation plan.
3. Decompose the goal into work packages.
4. Build a dependency DAG.
5. Decide what can run in parallel.
6. Assign each package to the most suitable model.
7. Create isolated branches and worktrees.
8. Launch the coding agents.
9. Monitor their work.
10. Validate every result.
11. request corrective work when validation fails.
12. Merge packages in dependency-safe order.
13. Run integration and release checks.
14. Preserve all state so work can resume later.

I specifically did not want to spend my time manually:

* opening separate agent chats;
* copying prompts between Codex, Gemini, Claude, and Grok;
* explaining the repository repeatedly;
* checking which package depended on another;
* creating branches and worktrees;
* tracking completion manually;
* reconciling conflicting agent changes;
* remembering where an interrupted agent stopped.

The desired interface was effectively:

```text
Me: Build this.

System:
- plan created
- contracts frozen
- 7 work packages ready
- 4 executing in parallel
- 2 blocked by dependencies
- 1 awaiting review
- integration status: green
```

---

# 2. The distinction between Hermes Agent and RailWarden

This became the central design decision.

## Hermes Agent

Hermes was supposed to be the **human-facing orchestration console**.

It would handle:

* conversation with me;
* interpretation of goals;
* repository and project context;
* skills and MCP access;
* memory;
* model selection discussions;
* plan presentation;
* approval and intervention;
* summaries;
* manual commands;
* exceptional decisions.

Hermes is the interface and project manager.

## RailWarden

RailWarden was supposed to be the **deterministic execution engine**.

It would own:

* goals;
* work packages;
* dependencies;
* the DAG;
* task states;
* branches;
* worktrees;
* agent processes;
* provider adapters;
* validation commands;
* checkpoints;
* retries;
* corrective runs;
* handoffs;
* merge order;
* integration state;
* quota state;
* provider health;
* audit logs.

RailWarden is the operating system underneath Hermes.

The final conceptual boundary was:

```text
Me
 ↓
Hermes
  Human interface, reasoning, planning, intervention
 ↓
RailWarden
  Deterministic scheduling, execution, state, validation
 ↓
Codex / Claude / Gemini / Grok / local models
  Isolated workers
 ↓
Git worktrees + tests + CI
  Objective source of truth
```

This separation mattered because Hermes alone could delegate tasks, but it did not provide the durable scheduler, transaction model, resumability, branch isolation, or integration guarantees I wanted.

---

# 3. The factory model

I wanted the system to behave like a **software factory**, not a chat swarm.

A goal would pass through a controlled lifecycle:

```text
GOAL_RECEIVED
      ↓
REPOSITORY_ANALYZED
      ↓
PLAN_CREATED
      ↓
CONTRACTS_FROZEN
      ↓
WORK_PACKAGES_CREATED
      ↓
DAG_VALIDATED
      ↓
AGENTS_ASSIGNED
      ↓
WORK_EXECUTING
      ↓
PACKAGE_VALIDATION
      ↓
PACKAGE_REVIEW
      ↓
DEPENDENCY-SAFE_MERGE
      ↓
INTEGRATION_VALIDATION
      ↓
RELEASE_REVIEW
      ↓
GOAL_COMPLETE
```

The important point was that agents did not autonomously wander around the repository. They received bounded work contracts.

For example : 
Each work package needed:

```yaml
id: WP-009
title: Intent Evaluation Service

goal: >
  Implement deterministic order-intent evaluation against
  the active policy version.

dependencies:
  - WP-001
  - WP-002
  - WP-003

owned_paths:
  - apps/control-plane/src/tmom/application/intent_service.py
  - tests/unit/application/test_intent_service.py

acceptance_criteria:
  - idempotent duplicate requests return the same decision
  - Decimal is used for financial values
  - active policy version is persisted on the decision
  - reason graph is deterministic
  - all tests pass

validation:
  - uv run ruff check
  - uv run mypy --strict src/tmom
  - uv run pytest tests/unit/application/test_intent_service.py

model_profile: codex-gpt-5.5-high
reviewer_profile: claude-opus
```

That bounded contract was the difference between “several agents editing code” and an actual agent factory.

---

# 4. Parallel and sequential execution

I wanted parallelism, but not indiscriminate parallelism.

The scheduler needed to derive execution from dependencies.

For example:

```text
WP-001 Domain Contracts
 ├── WP-002 Policy Kernel
 ├── WP-003 Persistence
 ├── WP-004 MCP Skeleton
 └── WP-005 Authentication

WP-002 + WP-003
 └── WP-009 Intent Service

WP-008 + WP-009 + WP-010 + WP-011
 ├── WP-012 MCP Tools
 └── WP-013 REST API
```

Therefore:

* `WP-002`, `WP-003`, `WP-004`, and `WP-005` could run in parallel after `WP-001`.
* `WP-009` could not start until its domain, policy, and persistence dependencies were merged.
* MCP tools could not be implemented reliably until application services existed.
* release review could not run before integration and deployment work completed.

The Hermes Agent (orchestrator agent) would plan  this style of explicit work-package DAG, ownership, merge ordering, CI gates, context documents, handoff templates, and agent prompts. 

I wanted to generate and enforce this structure automatically for every project.

---

# 5. Multi-model orchestration

I did not want a single model used for everything.

The factory needed normalized model profiles such as:

```text
openai/codex-gpt-5.5-high
anthropic/claude-opus-4.6-thinking
anthropic/claude-sonnet-4.6-thinking
google/gemini-3.1-pro-high
google/gemini-3.5-flash-high
xai/grok
local/nemotron
```

The assignment logic would consider:

* semantic complexity;
* coding strength;
* repository size;
* security sensitivity;
* cost;
* quota availability;
* review independence;
* task duration;
* provider reliability.

Example:

| Work                      | Primary                    | Reviewer      |
| ------------------------- | -------------------------- | ------------- |
| Architecture              | Claude Opus                | Codex         |
| Domain contracts          | Codex                      | Claude Opus   |
| Policy kernel             | Codex                      | Claude Opus   |
| Authentication            | Claude Opus                | Codex         |
| Mechanical REST routes    | Gemini Flash               | Claude Sonnet |
| Azure infrastructure      | Codex                      | Gemini Pro    |
| Independent release audit | Local or separate provider | Claude Opus   |

The Hermes agent (orchestrator agent) would plan and own this model-assignment method rather than assigning one agent uniformly across all packages. 

---

# 6. Agents were workers, not authorities

I wanted agents to have substantial implementation autonomy inside their package, but not architectural authority over the whole project.

An agent could:

* inspect relevant code;
* implement its assigned package;
* add tests;
* run validation;
* make bounded corrections;
* document assumptions;
* produce a handoff;
* commit its work.

An agent could not independently:

* rewrite frozen contracts;
* change another package’s owned files;
* silently modify architecture;
* bypass validation;
* merge itself;
* declare the whole project complete;
* start unrelated work;
* alter protected dependencies without approval.

The hierarchy was:

```text
Frozen contracts
    >
Work-package acceptance criteria
    >
Repository tests and CI
    >
Reviewer decision
    >
Worker-agent opinion
```

Git and tests were the final judge, not agent confidence.

---

# 7. Branch and worktree isolation

Every package needed its own branch and worktree:

For example:

```text
integration/railwarden-goal-0042
agent/WP-001-domain
agent/WP-002-policy
agent/WP-003-persistence
agent/WP-005-auth
```

With worktrees such as:

```text
~/CODE/project
~/CODE/project-worktrees/wp001
~/CODE/project-worktrees/wp002
~/CODE/project-worktrees/wp003
```

This provided:

* filesystem isolation;
* branch isolation;
* fewer accidental overwrites;
* deterministic diffs;
* package-specific validation;
* clean corrective runs;
* controlled merge order;
* easier rollback.

I did not want multiple agents writing into one working tree.

---

# 8. Shared context without shared uncontrolled state

I wanted every agent to receive consistent project knowledge through a structured context system.

For example:
Typical project context included:

```text
context/
├── PROJECT_CONTEXT.md
├── PRODUCT_INVARIANTS.md
├── ARCHITECTURE.md
├── DOMAIN_GLOSSARY.md
├── SECURITY_MODEL.md
├── ERROR_TAXONOMY.md
├── TEST_STRATEGY.md
└── CONTRIBUTING_AGENTS.md
```

The Hermes agent (orchestrator agent) would formalize these documents together with orchestration manifests and contract definitions. 

The principle was:

> Agents share durable artifacts, not private conversational memory.

An agent should not need to know what another agent “was thinking.” It should depend on:

* frozen contracts;
* merged code;
* explicit handoffs;
* task state;
* test results;
* architectural decisions;
* repository history.

This prevented hidden conversational context from becoming an architectural dependency.

---

# 9. Persistent state and resumability

The factory could not be a one-session script.

I wanted it to survive:

* terminal closure;
* machine restart;
* exhausted provider quota;
* failed agent process;
* interrupted implementation;
* partially merged work;
* a change in the assigned model.

The planned separation was:

```text
.railwarden/
  Durable project configuration, tracked when appropriate

.railwarden-runtime/
  Runtime state, logs, process metadata, checkpoints
```

Possible durable files:

```text
.railwarden/project.yaml
.railwarden/factory.yaml
.railwarden/validation.yaml
```

Runtime state:

```text
.railwarden-runtime/goals/
.railwarden-runtime/work-packages/
.railwarden-runtime/sessions/
.railwarden-runtime/logs/
.railwarden-runtime/checkpoints/
.railwarden-runtime/provider-health/
```

This allowed:

```bash
warden status
warden resume
warden retry WP-009
warden pause WP-012
warden agent swap agent-04 --to anthropic/claude-sonnet
```

The scheduler database or durable state store—not a model’s conversational memory—would determine the actual state of the factory.

---

# 10. Quota-aware model switching

I explicitly wanted the factory to handle subscription limits and provider failures.

Example:

```text
WP-009 running on Codex
        ↓
Codex quota exhausted
        ↓
Checkpoint worktree and session state
        ↓
Mark agent PAUSED_PROVIDER_LIMIT
        ↓
Select compatible replacement
        ↓
Generate structured handoff
        ↓
Resume using Claude or Gemini
```

The replacement agent would receive:

* work-package definition;
* current branch and worktree;
* completed changes;
* remaining acceptance criteria;
* failing tests;
* prior agent handoff;
* architectural context;
* exact next action.

This is why the design included explicit abstractions such as:

```text
ModelProfile
AgentRole
AgentInstance
SessionProfile
QuotaState
ProviderHealth
```

I wanted providers to be replaceable execution backends, not hardcoded assumptions throughout the scheduler.

---

# 11. Observability and the tmux control surface

I wanted a persistent terminal workspace rather than several disconnected applications.

The envisioned command was:

```bash
warden launch
```

It would create a tmux session with at least:

```text
Window: factory
- Hermes console
- scheduler status
- active agent panes
- integration status

Window: observability
- logs
- task DAG
- test results
- Git graph
- provider health
- quota state
```

The dashboard needed to show:

* current goal;
* active work packages;
* dependency-blocked packages;
* assigned models;
* branch names;
* test status;
* review status;
* token/quota conditions;
* merge queue;
* failures;
* corrective attempts;
* overall completion.

GitLens or Git Graph could supplement this, but Git UI was not the scheduler. It only visualized repository activity.

---

# 12. Validation and corrective loops

A package was not complete because an agent said it was complete.

Completion required deterministic evidence:

```text
Agent implementation
      ↓
Static checks
      ↓
Unit tests
      ↓
Contract tests
      ↓
Package-specific acceptance checks
      ↓
Independent review
      ↓
Corrective task when required
      ↓
Merge eligibility
```
For eaxmple: 
For TMOM, examples included:

```bash
uv run ruff check
uv run ruff format --check
uv run mypy --strict src/tmom
uv run pytest tests/unit/domain
uv run pytest tests/contract
```

The broader TMOM plan defined per-PR, integration, and release gates, including tenant isolation, idempotency, MCP contract tests, migration validation, cross-client tests, security review, deployment smoke tests, and the no-broker-path safety test. 

The system needed to make these validation gates first-class scheduler objects.

---

# 13. Human control

I did not want an uncontrollable autonomous swarm.

I wanted high automation with explicit control points.

The human should be able to:

```bash
warden status
warden inspect WP-009
warden logs agent-04
warden pause WP-012
warden resume WP-012
warden reject WP-009
warden retry WP-009
warden reassign WP-009 --model codex-gpt-5.5-high
warden approve-contracts
warden approve-merge WP-009
warden abort-goal
```

Critical decisions that could require human approval included:

* freezing architecture;
* changing frozen contracts;
* destructive migrations;
* secret or infrastructure changes;
* accepting degraded validation;
* merging high-risk packages;
* production deployment.

The factory automates execution. It does not eliminate governance.

---

# 14. A high level understanding of the system?


* It is a general-purpose development system.
* It needs its own versioning, releases, tests, documentation, and roadmap.
* Future projects must use the same factory.


The correct separation became:

```text
~/CODE/railwarden/
  The reusable factory implementation

~/CODE/Tmom_Deviation/
  The TMOM product repository
```

RailWarden could operate on TMOM, but it would not live inside TMOM.

---

# 15. What belongs in RailWarden versus TMOM

## RailWarden repository

Reusable machinery:

```text
railwarden/
├── scheduler/
├── agents/
├── providers/
├── workflows/
├── validation/
├── git/
├── worktrees/
├── observability/
├── checkpoints/
├── handoffs/
├── templates/
├── cli/
├── config/
└── tests/
```

Reusable definitions:

* generic work-package schema;
* DAG scheduler;
* model adapters;
* provider-health system;
* quota handling;
* branch/worktree manager;
* validation engine;
* merge controller;
* handoff format;
* tmux launcher;
* project bootstrapper;
* generic prompts;
* generic context templates.

## TMOM repository

Project-specific artifacts:

```text
TMOM/
├── .railwarden/
│   ├── project.yaml
│   └── validation.yaml
├── orchestration/
│   ├── work_packages.yaml
│   ├── dependency_graph.mmd
│   ├── ownership_matrix.csv
│   ├── merge_order.yaml
│   ├── contract_freeze_manifest.yaml
│   ├── model_assignment.yaml
│   └── agent_prompts/
├── context/
│   ├── PRODUCT_INVARIANTS.md
│   ├── ARCHITECTURE.md
│   └── SECURITY_MODEL.md
└── application source
```

---

# 16. What needed to be in the system?


## Move into RailWarden as reusable capability

* generic work-package schema;
* generic DAG representation;
* scheduler logic;
* model-assignment mechanism;
* branch/worktree automation;
* generic handoff template;
* validation-runner framework;
* merge-state machine;
* agent lifecycle definitions;
* provider and quota abstractions;
* generic tmux setup;
* observability framework;
* common prompts.

---

# 17. Why the need for this system

Anything after a while can become a sufficiently complex project:

exmaple: 
* multiple architectural rewrites;
* brokerless pivot;
* domain contracts;
* deterministic policy kernel;
* persistence;
* MCP;
* REST;
* authentication;
* audit;
* infrastructure;
* many dependent work packages;
* several coding models working in parallel;
* strict merge ordering;
* package ownership;
* contract freezes;
* corrective commits.


---

# 18. What I expected the system to do for a project? 

Once this  existed, the intended workflow was:

```bash
cd ~/CODE/Tmom_Deviation
warden launch
```

RailWarden would then:

1. detect existing TMOM project configuration;
2. load context and architecture;
3. read completed and pending work packages;
4. inspect branches and worktrees;
5. recognize already merged work;
6. verify the integration branch;
7. resume from the correct dependency frontier;
8. dispatch newly unblocked packages;
9. track Codex and Antigravity workers;
10. validate corrections;
11. merge only after gates passed;
12. continue until the release goal was complete.

I expected that once the factory was finished, you would no longer need to reproduce the extensive manual WP-by-WP orchestration process used for TMOM for any future project.

---

# 19. What the system was not supposed to be

It was not:

* a generic chatbot;
* a collection of prompt templates;
* a wrapper around one model;
* an unrestricted agent swarm;
* a shared terminal where agents overwrite one another;
* a replacement for Git;
* a replacement for tests;
* a system where Hermes directly writes all production code;
* a project submodule;
* a dashboard with no execution engine;
* a workflow that stores critical state only in conversations.

It was supposed to be:

> A deterministic, persistent, multi-provider software-delivery control plane for AI coding agents.

---

# 20. The deeper objective

My broader engineering model was:

```text
Traditional engineer:
writes and manages code.

AI-native engineer:
defines architecture, invariants, contracts, tests, and goals;
then manages a fleet of coding agents that produce and validate code.
```

I wanted this to make that operating model repeatable.

The factory would let one human operate at the level of:

* product goal;
* architecture;
* system boundaries;
* contracts;
* risk;
* acceptance criteria;
* agent allocation;
* intervention;
* final approval.

The agents would operate at the level of:

* implementation;
* tests;
* corrections;
* documentation;
* bounded code changes.

The factory itself would control:

* coordination;
* execution order;
* isolation;
* state;
* validation;
* integration;
* recovery.

That was the complete intent behind the agent factory.
