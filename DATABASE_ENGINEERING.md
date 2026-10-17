# Newtonite Task Tracker: Database Engineering & Concurrency Architecture

## 1. Relational Schema Architecture

Newtonite utilizes a normalized relational schema engineered for relational integrity, auditability, high read performance, and strict multi-tenant team isolation.

### 1.1 Tables and Field Definitions

#### `tasks_team`
Represents an isolated workspace/project group.
- `id` (INTEGER PK AUTOINCREMENT): Surrogate primary key.
- `name` (VARCHAR(100) UNIQUE NOT NULL): Distinct team identifier.
- `description` (TEXT): Team mandate and guidelines.
- `created_at` (DATETIME NOT NULL): Timestamp of creation.
- `updated_at` (DATETIME NOT NULL): Timestamp of last modification.

#### `tasks_teammembership`
Connects users to teams with an explicit role.
- `id` (INTEGER PK AUTOINCREMENT).
- `user_id` (INTEGER NOT NULL FK -> `auth_user.id` ON DELETE CASCADE).
- `team_id` (INTEGER NOT NULL FK -> `tasks_team.id` ON DELETE CASCADE).
- `role` (VARCHAR(20) NOT NULL): Choice field (`ADMIN`, `MANAGER`, `MEMBER`).
- `created_at` (DATETIME NOT NULL).
- **Constraints**:
  - `UniqueConstraint(fields=['user', 'team'], name='unique_user_team_membership')`: Enforces at the database engine level that a user can never be inserted more than once into the same team.

#### `tasks_task`
The central work item domain entity.
- `id` (INTEGER PK AUTOINCREMENT).
- `title` (VARCHAR(200) NOT NULL): Task headline.
- `description` (TEXT): Full details and acceptance criteria.
- `status` (VARCHAR(20) NOT NULL): Choice (`OPEN`, `IN_PROGRESS`, `RESOLVED`).
- `priority` (VARCHAR(20) NOT NULL): Choice (`LOW`, `MEDIUM`, `HIGH`, `URGENT`).
- `team_id` (INTEGER NOT NULL FK -> `tasks_team.id` ON DELETE CASCADE).
- `assignee_id` (INTEGER NULL FK -> `auth_user.id` ON DELETE SET NULL): Nullable; unassigned tasks retain `NULL`. If an assignee account is deleted, the task remains intact with `assignee=NULL`.
- `creator_id` (INTEGER NOT NULL FK -> `auth_user.id` ON DELETE PROTECT): Preserves creator attribution; deletion of a user who created tasks is blocked until ownership is reassigned.
- `due_date` (DATE NULL): Target completion deadline.
- `is_archived` (BOOLEAN NOT NULL DEFAULT 0): Soft deletion flag.
- `archived_at` (DATETIME NULL): Timestamp when archived.
- `version` (INTEGER NOT NULL DEFAULT 1): Monotonically increasing sequence number powering Optimistic Concurrency Control (OCC).
- `created_at` (DATETIME NOT NULL).
- `updated_at` (DATETIME NOT NULL).

#### `tasks_activityevent`
Immutable audit log of all domain alterations.
- `id` (INTEGER PK AUTOINCREMENT).
- `actor_id` (INTEGER NULL FK -> `auth_user.id` ON DELETE SET NULL).
- `team_id` (INTEGER NOT NULL FK -> `tasks_team.id` ON DELETE CASCADE).
- `task_id` (INTEGER NULL FK -> `tasks_task.id` ON DELETE SET NULL).
- `event_type` (VARCHAR(50) NOT NULL): Choice (`TASK_CREATED`, `STATUS_CHANGED`, `PRIORITY_CHANGED`, `ASSIGNMENT_CHANGED`, `TASK_RESOLVED`, `TASK_REOPENED`, `TASK_ARCHIVED`, `MEMBER_ADDED`, `MEMBER_ROLE_CHANGED`, etc.).
- `details` (TEXT NOT NULL): Human-readable changelog entry.
- `previous_state` (JSON NOT NULL DEFAULT '{}'): Snapshot of changed fields prior to the write.
- `new_state` (JSON NOT NULL DEFAULT '{}'): Snapshot of changed fields after the write.
- `created_at` (DATETIME NOT NULL).

#### `tasks_idempotencyrecord`
Guarantees at-most-once execution for state-creating POST operations.
- `id` (INTEGER PK AUTOINCREMENT).
- `key` (VARCHAR(64) NOT NULL): Client-supplied UUID.
- `user_id` (INTEGER NOT NULL FK -> `auth_user.id` ON DELETE CASCADE).
- `action` (VARCHAR(64) NOT NULL): Action identifier (e.g., `TASK_CREATE`).
- `response_code` (INTEGER NOT NULL DEFAULT 200).
- `task_id` (INTEGER NULL FK -> `tasks_task.id` ON DELETE SET NULL).
- `created_at` (DATETIME NOT NULL).
- **Constraints**:
  - `UniqueConstraint(fields=['user', 'key', 'action'], name='unique_user_key_action_idempotency')`.

---

## 2. Indexing Strategy & Performance Trade-offs

Database indexes speed up read queries by maintaining auxiliary B-tree data structures, at the cost of slight overhead on `INSERT` and `UPDATE` operations.

In Newtonite, indexes were tailored specifically for common dashboard filters and query patterns:

| Index Name | Indexed Fields | Query Pattern Accelerated |
| :--- | :--- | :--- |
| `task_team_arch_status_idx` | `(team, is_archived, status)` | Fast retrieval of active team tasks filtered by workflow state. |
| `task_team_arch_prio_idx` | `(team, is_archived, priority)` | Priority distribution aggregations on dashboard. |
| `task_assignee_status_idx` | `(assignee, is_archived, status)` | "My Tasks" query and team workload chart calculation. |
| `task_due_date_idx` | `(due_date)` | Overdue tasks scan (`due_date < today AND status != RESOLVED`). |
| `task_created_at_idx` | `(created_at)` | Chronological sorting of task lists. |
| `activity_team_created_idx` | `(team, created_at)` | Team activity timeline stream. |
| `activity_task_created_idx` | `(task, created_at)` | Task detail audit timeline. |

---

## 3. The Lifecycle of CRUD Operations

### 3.1 Create (Task Creation)
```
Client Browser                  Django Layer                     Database Engine
     |                               |                                  |
     |--- 1. POST /tasks/new/ ------>|                                  |
     |    (Form + CSRF + IdempKey)   |--- 2. Authenticate & Scoping --->|
     |                               |--- 3. Validate Team & Assignee ->|
     |                               |                                  |
     |                               |--- 4. BEGIN TRANSACTION -------->|
     |                               |--- 5. Check IdempotencyKey ----->|
     |                               |--- 6. INSERT into tasks_task --->|
     |                               |--- 7. INSERT into activityevent->|
     |                               |--- 8. COMMIT TRANSACTION ------->|
     |                               |                                  |
     |<-- 9. HTTP 302 Redirect ------|                                  |
     |       (to /tasks/<id>/)       |                                  |
```

### 3.2 Read (Task List & Details)
1. Request hits `task_list_view` or `task_detail_view`.
2. Authorization scoping is applied: `Task.objects.filter(team__memberships__user=request.user)`.
3. Eager loading is executed via `select_related('team', 'assignee', 'creator')`.
4. Resulting records are sliced by `Paginator` using SQL `LIMIT` and `OFFSET`.
5. Data is rendered to the template. Records belonging to teams where the user is not a member are never queried or leaked.

### 3.3 Update (Edit with Optimistic Concurrency Control)
1. User retrieves task at `version = 1`.
2. User submits edits with hidden form field `<input name="version" value="1">`.
3. Backend validates status transition via `TaskService.validate_transition()`.
4. Conditional atomic SQL update executes:
   ```sql
   UPDATE tasks_task
   SET title = %s, description = %s, status = %s, priority = %s,
       assignee_id = %s, due_date = %s, version = version + 1, updated_at = %s
   WHERE id = %s AND version = 1;
   ```
5. If `rowcount == 1`: Update succeeded. Write `ActivityEvent` inside same transaction; commit.
6. If `rowcount == 0`: Another user updated the task concurrently! Rollback; raise `StaleUpdateError`; render HTTP 409 conflict diff.

### 3.4 Delete, Resolve, and Archive Policy
- **Resolution (`status = 'RESOLVED'`)**: Marks the task as completed. It remains visible in active task lists and contributes to resolution metrics.
- **Archiving (`is_archived = True`, `archived_at = NOW()` )**: Soft-deletes the task from standard views and active boards. Audit timeline, historical associations, and metrics are permanently retained.
- **Permanent Deletion**: Physical `DELETE FROM tasks_task` is restricted to System Administrators and strictly audited. Soft archiving is the preferred default.

---

## 4. ACID Properties in Newtonite

| Property | Concept | Implementation in Newtonite |
| :--- | :--- | :--- |
| **Atomicity** | All operations in a transaction succeed or all are rolled back. No partial writes. | Multi-table writes (e.g. `Task` creation + `ActivityEvent` insertion + `IdempotencyRecord` creation) are wrapped inside Python `with transaction.atomic():`. An unhandled exception triggers an immediate `ROLLBACK`. |
| **Consistency** | The database transitions from one valid state to another, satisfying all defined invariants. | Enforced by: 1) Foreign keys with CASCADE / SET_NULL / PROTECT; 2) Unique constraint on `(user_id, team_id)`; 3) Status transition state-machine validation; 4) Model validation ensuring assignees belong to the task's team. |
| **Isolation** | Intermediate states of concurrent transactions are invisible to each other. | Guarded against lost updates using Optimistic Concurrency Control (`version = version + 1 WHERE id = %s AND version = %s`). Handled in SQLite via database-level write locks (`EXCLUSIVE`). |
| **Durability** | Once committed, changes survive crashes and power losses. | Data is committed to non-volatile disk storage. In SQLite, WAL (Write-Ahead Logging) and atomic file-sync system calls (`fsync`) guarantee that committed transactions persist. |

---

## 5. SQL Isolation Levels vs SQLite Concurrency Model

### 5.1 Theoretical ANSI SQL Isolation Levels

| Isolation Level | Dirty Reads? | Non-Repeatable Reads? | Phantom Reads? |
| :--- | :--- | :--- | :--- |
| **Read Uncommitted** | Possible | Possible | Possible |
| **Read Committed** | Prevented | Possible | Possible |
| **Repeatable Read** | Prevented | Prevented | Possible |
| **Serializable** | Prevented | Prevented | Prevented |

### 5.2 SQLite Concurrency & Locking Architecture
Unlike client-server database systems like PostgreSQL which manage row-level multiversion concurrency control (MVCC), SQLite is a serverless, file-based database engine.

Its concurrency is governed by **file locks**:
1. **UNLOCKED**: No process is reading or writing the file.
2. **SHARED**: One or more connections are reading from the database. Readers do not block other readers.
3. **RESERVED**: A connection intends to write in the future. Other connections can continue reading, but no other connection can obtain a RESERVED lock.
4. **PENDING**: A connection is waiting to write and is waiting for existing SHARED locks to release. No new readers may acquire locks.
5. **EXCLUSIVE**: The writer holds exclusive control. No other process may read or write.

**Write-Ahead Logging (WAL)**:
In WAL mode, readers do not block writers, and writers do not block readers. Changes are written to a separate `-wal` file and periodically checkpointed back to the primary database file.

### 5.3 Why Optimistic Locking is Essential Even with Database Locks
Database locks only protect a transaction during the milliseconds that a query is executing. They do **not** prevent a user from opening an edit page, spending 5 minutes writing notes, and saving over changes that a colleague committed 2 minutes ago. 

Newtonite's **version-based OCC** guarantees that whether using SQLite or PostgreSQL, stale updates are detected and intercepted reliably.

---

## 6. Safe Migration Practices

1. **Schema Migrations (`tasks/migrations/0001_initial.py`)**:
   Generated using Django's schema introspection. Uses reversible migration operations (`CreateModel`, `AddIndex`, `AddConstraint`).
2. **Data Safety Guidelines**:
   - Never run `rm db.sqlite3` in production.
   - Run `python manage.py makemigrations --dry-run` to preview DDL before generating files.
   - Execute migrations inside transaction blocks (`atomic = True`, which Django migrations enforce by default on supporting engines).
   - In production deployments, execute `python manage.py migrate` as part of the release pipeline prior to restarting application processes.
