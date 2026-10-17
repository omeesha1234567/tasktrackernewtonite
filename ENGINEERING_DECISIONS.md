# Newtonite Task Tracker: Architecture Decision Records (ADR)

This document formalizes the significant software engineering, database, architectural, and design decisions adopted in the Newtonite Task Tracker codebase.

---

## Decision 1: Architecture Style — Modular Django Monolith with Server-Side Rendering

### 1. The Problem
The challenge mandates building a complete, cohesive, secure, and production-grade task tracking SaaS application that is easy to install, run, verify, and demonstrate on Windows without brittle dependency chains.

### 2. The Chosen Solution
A **Modular Django Monolith** utilizing Django's Model-View-Template (MVT) architecture with server-side rendered HTML, a clean domain service layer (`services.py`), a policy layer (`permissions.py`), and vanilla CSS/JS.

### 3. Alternatives Considered
- **Decoupled React / Next.js SPA with Django REST Framework (DRF)**:
  - *Drawback*: Requires running Node.js alongside Python, managing dual dev servers, handling CORS, JWT expiration, client-side state hydration, and multi-gigabyte `node_modules` folders.
- **Microservices Architecture**:
  - *Drawback*: Extreme operational complexity (RPCs, distributed transactions, service discovery) for a challenge task manager.

### 4. Why This Solution Is Appropriate
- Immediate local installation (`pip install django` and run).
- Zero build tools (no Webpack, Vite, or npm bundle build step required to run).
- Native CSRF protection, session management, and server-side validation.
- Sub-millisecond template rendering latency with minimal CPU overhead.

### 5. Trade-offs & Limitations
- Full-page navigation on some interactions (mitigated by instant server response times and the Post-Redirect-Get pattern).
- Dynamic client-side features require targeted vanilla JavaScript (implemented cleanly in `app.js`).

---

## Decision 2: Concurrency Management — Optimistic Concurrency Control (OCC)

### 1. The Problem
Multiple users simultaneously editing the same task cause "lost updates" (User A saves changes, User B overwrites them seconds later without knowing User A's changes ever happened).

### 2. The Chosen Solution
**Optimistic Concurrency Control (OCC)** using a dedicated integer `version` field on the `Task` model, verified via an atomic conditional SQL update:
```sql
UPDATE tasks_task
SET ..., version = version + 1
WHERE id = %s AND version = %s;
```
If the number of affected rows is 0, a concurrent edit has occurred. The system raises `StaleUpdateError` and presents a side-by-side reconciliation diff.

### 3. Alternatives Considered
- **Pessimistic Locking (`SELECT FOR UPDATE`)**:
  - *Drawback*: Locks the database row while the user has the page open in their browser. If a user walks away for lunch, nobody else can edit the task. Also causes database deadlocks and holds connections open.
- **Last-Write-Wins (LWW)**:
  - *Drawback*: Silently discards data; unacceptable for a professional engineering team tracker.

### 4. Why This Solution Is Appropriate
- Non-blocking: Users never wait for locks while composing descriptions.
- Completely safe against race conditions.
- Works identically on SQLite, PostgreSQL, and MySQL.

### 5. Trade-offs & Limitations
- If two users submit edits in the same second, the second user receives a conflict alert and must re-verify their inputs against the newest state.

---

## Decision 3: Idempotency Protection — Database-Enforced Idempotency Key

### 1. The Problem
Users accidentally double-clicking submit buttons or network timeouts retried by HTTP clients cause duplicate task creation.

### 2. The Chosen Solution
An **Idempotency Key Architecture**:
1. When rendering the task creation form, a unique UUID token is generated and stored in a hidden field.
2. On submission, `TaskService.create_task()` checks `IdempotencyRecord` inside an atomic transaction.
3. If the key exists for the user and action, the previously created task is returned.
4. Database unique constraint on `(user_id, key, action)` guarantees that even concurrent duplicate submissions cannot insert duplicate tasks.

### 3. Alternatives Considered
- **Client-Side Button Disabling (`disabled=true`)**:
  - *Drawback*: Fails if JavaScript is slow, if the user presses enter twice, or if network retries originate from an automated client or API script. Disabling a button is UX convenience, not security.
- **In-Memory Cache (e.g. Redis cache)**:
  - *Drawback*: Cache keys are lost if Redis restarts; introduces another external server dependency.

### 4. Why This Solution Is Appropriate
- ACID compliant and persistent across server restarts.
- Zero extra dependencies.

---

## Decision 4: Multi-Tenant Team Isolation — Scoped Queryset Layer

### 1. The Problem
Preventing users from accessing, viewing, or modifying tasks belonging to other teams (Insecure Direct Object Reference - IDOR).

### 2. The Chosen Solution
Centralized **Queryset Scoping** in `tasks/permissions.py`:
`get_accessible_tasks_qs(user)` filters all task queries by `team__memberships__user=user`. Views and forms exclusively build on top of these scoped querysets.

### 3. Alternatives Considered
- **Separate SQLite Database per Team**:
  - *Drawback*: Prevents cross-team reporting, complicates system-wide migrations, and prevents users belonging to multiple teams from viewing a unified "My Tasks" queue.
- **Ad-hoc checks in every view**:
  - *Drawback*: Error-prone; easy for a developer to forget an `if` condition in a new endpoint.

### 4. Why This Solution Is Appropriate
- Declarative and reusable across list views, search filters, and detail endpoints.
- High performance through composite indexes.

---

## Decision 5: Task Deletion — Soft Archiving with Immutable Audit Timeline

### 1. The Problem
Accidental task deletions destroy project history, metrics, and accountability.

### 2. The Chosen Solution
**Soft Archiving (`is_archived = True`, `archived_at = timestamp`)**:
Archived tasks are excluded from active boards, active counters, and searches by default, but remain fully inspectable in audit logs and can be restored by Team Admins or Managers.

### 3. Alternatives Considered
- **Physical Hard Deletion (`DELETE FROM tasks_task`)**:
  - *Drawback*: Cascades or nullifies related records, permanently destroying historical audit trails.

### 4. Why This Solution Is Appropriate
- Preserves referential integrity for metrics and audit compliance.
- Allows immediate recovery from accidental deletions.
