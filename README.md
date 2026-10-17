# Newtonite Task Tracker 🚀

> A production-grade, collaborative, multi-tenant task management web application built with Python and Django for the **Newtonite Software Engineering Challenge**.

![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12%20%7C%203.14-blue)
![Django](https://img.shields.io/badge/Django-6.1-green)
![Tests](https://img.shields.io/badge/Tests-17%20Passed%20(100%25)-success)
![Architecture](https://img.shields.io/badge/Architecture-Modular%20Monolith-indigo)

---

## 1. Project Overview

**Newtonite Task Tracker** is designed to solve real-world engineering team challenges:
- **Prevent Lost Updates**: Multi-user optimistic concurrency control (OCC) prevents users from overwriting each other's edits.
- **Deduplicate Submissions**: Idempotency token architecture ensures network retries and double clicks never create duplicate tasks.
- **Team-Scoped RBAC & Anti-IDOR**: Strict team isolation guarantees users cannot access, view, or alter work items from teams they do not belong to.
- **Enforced Workflow Lifecycles**: Backend state-machine validates all status progressions (`OPEN` &rarr; `IN_PROGRESS` &rarr; `RESOLVED` &rarr; `OPEN`).
- **Immutable Audit Logging**: Every state change, reassignment, priority shift, and membership update is recorded atomically with full field diffs.
- **Interactive Visualizations**: Real-time KPI summary cards and Chart.js analytics for status breakdown, priority distribution, velocity trends, and team workload.

---

## 2. Technology Stack

- **Backend**: Python 3.14, Django 6.1 (Modular Monolith)
- **Database**: SQLite 3 with Write-Ahead Logging (WAL) and composite B-tree indexes
- **Frontend**: Django Templates, HTML5 Semantic Markup, Vanilla CSS Design System (`app.css`), Vanilla JavaScript (`app.js`)
- **Visual Analytics**: Chart.js 4.4 (integrated with accessible table and filter drill-downs)
- **Testing**: Django Test Framework (`TestCase` with in-memory test database and fast hasher optimization)

---

## 3. Quick Start (Windows Setup Guide)

### Step 1: Open Terminal in Workspace
Open PowerShell or Command Prompt in the project folder:
```powershell
cd "c:\Users\Omeesha Dwivedi\Desktop\newt task-tracker"
```

### Step 2: Activate the Virtual Environment
```powershell
.\venv\Scripts\activate
# Or directly execute using the virtual environment python:
# .\venv\Scripts\python.exe
```

### Step 3: Run Database Migrations
```powershell
.\venv\Scripts\python.exe manage.py migrate
```

### Step 4: Seed Demo Data (Users, Teams, Tasks & Audit Logs)
A custom management command is provided to instantly populate realistic demo scenarios:
```powershell
.\venv\Scripts\python.exe manage.py seed_demo_data
```

### Step 5: Start the Development Server
```powershell
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
```
Open **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)** in your browser.

---

## 4. Demo Accounts & Evaluator Credentials

All test accounts share the password: `Password123!`

| Username | Role | Team Membership | Focus Area |
| :--- | :--- | :--- | :--- |
| **`alice`** | **Team Admin** | Core Platform | Full team management, member roles, task management, archiving. |
| **`bob`** | **Team Manager** | Core Platform (Manager), Frontend (Member) | Task creation, assignment, priority and status updates across teams. |
| **`charlie`** | **Team Member** | Core Platform (Member), Mobile Apps (Admin) | Contributor workflows; editing assigned tasks; testing role constraints. |
| **`admin`** | **System Superuser** | Organization-wide | Password: `AdminPass123!`. Access to Django Admin at `/admin/`. |

---

## 5. Running Automated Tests

Execute the comprehensive automated test suite (17 tests covering RBAC, concurrency, idempotency, workflows, and metrics):

```powershell
.\venv\Scripts\python.exe manage.py test
```

Expected output:
```text
Creating test database for alias 'default'...
.................
----------------------------------------------------------------------
Ran 17 tests in 0.504s

OK
Destroying test database for alias 'default'...
```

---

## 6. Key Application Features & Navigation

- **Dashboard (`/`)**:
  - Summary KPI cards: Active, Open, In Progress, Resolved, Overdue, and Assigned to Me.
  - Interactive charts: Status breakdown, Priority distribution, 7-day velocity trend, and Team workload. Clicking chart segments filters the task list!
  - Overdue tasks alert box highlighting items requiring urgent action.
  - Recent workspace activity stream.
- **Task Management (`/tasks/`)**:
  - Filterable, sortable, and paginated task table.
  - Search by task title, description, or `#ID`.
  - Filter by Status, Priority, Team, Overdue status, and Archived toggle.
- **Create Task (`/tasks/new/`)**:
  - Form validation with dynamic assignee dropdown that automatically queries `/api/teams/<id>/members/` to prevent cross-team assignment errors.
  - Idempotency key protection prevents double submissions.
- **Task Detail & Workflow (`/tasks/<id>/`)**:
  - Displays full task metadata, due date, creator, assignee, and optimistic version indicator (`v1`).
  - Action buttons for allowed status transitions based on current workflow state.
  - Complete per-task activity audit log showing who changed what, when, and previous/new values.
- **Edit Task (`/tasks/<id>/edit/`)**:
  - Optimistic concurrency control. If another user modified the task concurrently, a **Stale Update Conflict Banner** shows a side-by-side diff.
- **Team Workspace Management (`/teams/` & `/teams/<id>/`)**:
  - Member roster with role badges (Admin, Manager, Member).
  - Admins can add members, promote/demote roles, and remove members.
  - Protected against demoting or removing the sole team administrator.
- **Activity History (`/activity/`)**:
  - Immutable, chronological audit log filterable by team workspace and event type.
- **User Profile (`/profile/`)**:
  - Account information, workspace memberships, and active workload summary.

---

## 7. Project Documentation Sitemap

| Document | Purpose |
| :--- | :--- |
| **[PROJECT_ARCHITECTURE.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/PROJECT_ARCHITECTURE.md)** | Complete system architecture, user roles, request lifecycles, and Mermaid diagrams. |
| **[DATABASE_ENGINEERING.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/DATABASE_ENGINEERING.md)** | Schema design, indexing, CRUD lifecycles, ACID properties, isolation levels, and concurrency. |
| **[PERMISSIONS.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/PERMISSIONS.md)** | Scoped RBAC permissions matrix, privilege escalation prevention, and anti-IDOR policies. |
| **[ENGINEERING_DECISIONS.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/ENGINEERING_DECISIONS.md)** | Architecture Decision Records (ADRs) explaining technical trade-offs. |
| **[TESTING.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/TESTING.md)** | Automated testing strategy, test suites, and verified execution reports. |
| **[KNOWN_LIMITATIONS.md](file:///c:/Users/Omeesha%20Dwivedi/Desktop/newt%20task-tracker/KNOWN_LIMITATIONS.md)** | Production scaling recommendations and roadmap. |

---

## 8. Troubleshooting

- **Port 8000 in use**: Run on another port: `.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8080`.
- **Resetting Demo Data**: Run `.\venv\Scripts\python.exe manage.py seed_demo_data` at any time to re-populate standard demo data without resetting user accounts.
