# Newtonite Task Tracker: Permissions & Authorization Specification

## 1. Overview & Security Model

Newtonite implements a **multi-tenant, team-scoped Role-Based Access Control (RBAC)** architecture. 

In Newtonite:
- **Authentication** verifies *identity* ("Who are you?"). Handled by Django's `auth` framework with secure password hashing.
- **Authorization** governs *privileges* ("What actions are you permitted to execute on this specific resource?"). Handled by `tasks/permissions.py` and enforced on every backend view and service method.

A user may belong to multiple teams with distinct roles in each. For instance, a user can be an `ADMIN` in the "Frontend Team" while being an ordinary `MEMBER` in the "Core Platform Team".

---

## 2. Complete Permission Matrix

| Operation / Capability | System Admin (Superuser) | Team Admin | Team Manager | Team Member | Non-Member / Anonymous |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **View Team Dashboard & Analytics** | Allowed | Allowed | Allowed | Allowed | Denied (403/Redirect) |
| **View Team Tasks** | Allowed | Allowed | Allowed | Allowed | Denied (403) |
| **Create Task in Team** | Allowed | Allowed | Allowed | Allowed | Denied (403) |
| **Edit Any Task in Team** | Allowed | Allowed | Allowed | Denied | Denied (403) |
| **Edit Assigned / Created Tasks** | Allowed | Allowed | Allowed | Allowed | Denied (403) |
| **Progress Task Status (Workflow)** | Allowed | Allowed | Allowed | If Assigned/Creator | Denied (403) |
| **Change Task Priority** | Allowed | Allowed | Allowed | Denied | Denied (403) |
| **Assign / Reassign Tasks** | Allowed | Allowed | Allowed | Denied | Denied (403) |
| **Archive / Restore Tasks** | Allowed | Allowed | Allowed | Denied | Denied (403) |
| **Permanently Delete Task** | Allowed | Allowed | Denied | Denied | Denied (403) |
| **View Audit / Activity History** | Allowed | Allowed | Allowed | Allowed | Denied (403) |
| **Edit Team Settings (Name/Desc)** | Allowed | Allowed | Denied | Denied | Denied (403) |
| **Invite / Add Team Members** | Allowed | Allowed | Denied | Denied | Denied (403) |
| **Change Member Roles** | Allowed | Allowed | Denied | Denied | Denied (403) |
| **Remove Member from Team** | Allowed | Allowed | Denied | Self-Leave Only | Denied (403) |
| **Create New Team Workspace** | Allowed | Allowed | Allowed | Allowed | Denied (Redirect) |

---

## 3. Privilege Escalation Defenses & Security Invariants

### 3.1 Protection of Sole Administrator
A critical failure mode in multi-tenant workspaces is the accidental or malicious removal of the last remaining administrator, leaving the workspace orphaned and unmanageable.

Newtonite guards this in `can_change_member_role()` and `can_remove_member()`:
```python
if target_membership.role == TeamRole.ADMIN:
    admin_count = TeamMembership.objects.filter(
        team=target_membership.team,
        role=TeamRole.ADMIN
    ).count()
    if admin_count <= 1:
        return False # Demotion or removal blocked!
```

### 3.2 Anti-IDOR (Insecure Direct Object Reference) Protection
Endpoints taking an object ID in the URL (e.g. `/tasks/42/edit/`, `/teams/5/members/2/role/`) do NOT perform naive `Task.objects.get(pk=pk)` lookups.

Instead, they enforce ownership scoping:
```python
task = get_object_or_404(
    Task.objects.select_related('team', 'assignee', 'creator'),
    pk=pk
)
if not can_edit_task(request.user, task):
    raise PermissionDenied("You do not have permission to edit this task.")
```
If an unauthorized user tampers with the URL to view or edit another team's task, Django raises a `PermissionDenied` exception and renders a clean HTTP 403 page.

### 3.3 Assignee Team-Membership Invariant
A task cannot be assigned to a user who does not belong to that task's team workspace. This invariant is enforced both in the model `clean()` method and in `TaskService.create_task()` / `TaskService.update_task()`:
```python
if assignee and not TeamMembership.objects.filter(team=team, user=assignee).exists():
    raise ValidationError({'assignee': f"User '{assignee.username}' is not a member of '{team.name}'."})
```
Furthermore, the task creation interface dynamically queries `/api/teams/<team_id>/members/` to asynchronously populate only valid team members into the assignee dropdown.
