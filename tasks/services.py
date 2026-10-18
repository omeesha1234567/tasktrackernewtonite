from datetime import timedelta
from django.db import transaction, IntegrityError
from django.db.models import Count, Q, F
from django.utils import timezone
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied

from .models import (
    Team,
    TeamMembership,
    TeamRole,
    Task,
    TaskStatus,
    TaskPriority,
    ActivityEvent,
    EventType,
    IdempotencyRecord,
    TaskAttachment,
    WorkspaceInvitation,
    InvitationStatus,
    InAppNotification,
    NotificationCategory,
)
from .permissions import (
    can_create_task,
    can_edit_task,
    can_change_task_status,
    can_archive_task,
    can_manage_team,
    can_change_member_role,
    can_remove_member,
    get_accessible_teams_qs,
    get_accessible_tasks_qs,
)


class StaleUpdateError(Exception):
    """Raised when an update fails because the task version has been modified concurrently."""
    pass


class InvalidWorkflowTransitionError(ValidationError):
    """Raised when an illegal status transition is attempted."""
    pass


VALID_TRANSITIONS = {
    TaskStatus.OPEN: [TaskStatus.IN_PROGRESS, TaskStatus.RESOLVED],
    TaskStatus.IN_PROGRESS: [TaskStatus.OPEN, TaskStatus.RESOLVED],
    TaskStatus.RESOLVED: [TaskStatus.OPEN],  # Reopen task
}


class TaskService:
    VALID_TRANSITIONS = VALID_TRANSITIONS

    @staticmethod
    def validate_transition(current_status, new_status):
        if current_status == new_status:
            return True
        allowed = VALID_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            raise InvalidWorkflowTransitionError(
                f"Invalid transition from '{TaskStatus(current_status).label}' to '{TaskStatus(new_status).label}'. "
                f"Permitted transitions from {TaskStatus(current_status).label} are: "
                f"{', '.join(TaskStatus(s).label for s in allowed) if allowed else 'None'}."
            )
        return True

    @classmethod
    def create_task(
        cls,
        user,
        team,
        title,
        description='',
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.OPEN,
        assignee=None,
        due_date=None,
        idempotency_key=None,
    ):
        """
        Creates a new task within an atomic transaction.
        Enforces:
        - Team authorization
        - Assignee team membership validation
        - Status validity (initial creation must be OPEN or IN_PROGRESS)
        - Idempotency deduplication
        - Audit activity event recording
        """
        if not can_create_task(user, team):
            raise PermissionDenied(f"You do not have permission to create tasks in team '{team.name}'.")

        # Assignee validation: must belong to the team
        if assignee and not TeamMembership.objects.filter(team=team, user=assignee).exists():
            raise ValidationError({'assignee': f"User '{assignee.username}' is not a member of '{team.name}'."})

        # Initial status validation
        if status == TaskStatus.RESOLVED:
            raise ValidationError({'status': "A newly created task cannot start in Resolved status."})

        with transaction.atomic():
            # Check idempotency
            if idempotency_key:
                existing_record = IdempotencyRecord.objects.select_for_update().filter(
                    user=user,
                    key=idempotency_key,
                    action='TASK_CREATE'
                ).first()
                if existing_record and existing_record.task:
                    return existing_record.task

            task = Task.objects.create(
                title=title.strip(),
                description=description.strip(),
                team=team,
                priority=priority,
                status=status,
                assignee=assignee,
                creator=user,
                due_date=due_date,
                version=1
            )

            # Record Idempotency
            if idempotency_key:
                try:
                    IdempotencyRecord.objects.create(
                        user=user,
                        key=idempotency_key,
                        action='TASK_CREATE',
                        task=task,
                        response_code=201
                    )
                except IntegrityError:
                    # Race condition caught by unique constraint
                    rec = IdempotencyRecord.objects.get(user=user, key=idempotency_key, action='TASK_CREATE')
                    if rec.task:
                        return rec.task

            # Record audit activity event
            ActivityEvent.objects.create(
                actor=user,
                team=team,
                task=task,
                event_type=EventType.TASK_CREATED,
                details=f"Task '{task.title}' created with status '{task.get_status_display()}' and priority '{task.get_priority_display()}'.",
                new_state={
                    'title': task.title,
                    'status': task.status,
                    'priority': task.priority,
                    'assignee': task.assignee.username if task.assignee else None,
                    'due_date': str(task.due_date) if task.due_date else None,
                }
            )

            # Notify assignee if assigned to someone else
            if task.assignee and task.assignee != user:
                NotificationService.create_notification(
                    recipient=task.assignee,
                    actor=user,
                    category=NotificationCategory.ASSIGNMENT,
                    title=f"New Task Assigned: #{task.id}",
                    message=f"{user.get_full_name() or user.username} assigned you to '{task.title}' in team '{team.name}'.",
                    task=task,
                    team=team,
                    is_urgent=(task.priority == TaskPriority.URGENT)
                )

            return task

    @classmethod
    def update_task(
        cls,
        user,
        task,
        title,
        description,
        priority,
        status,
        assignee,
        due_date,
        submitted_version,
    ):
        """
        Updates task fields with optimistic concurrency control (stale-update protection).
        Atomically updates the database and logs an activity event if version matches.
        """
        if not can_edit_task(user, task):
            raise PermissionDenied("You do not have permission to edit this task.")

        # Assignee validation
        if assignee and not TeamMembership.objects.filter(team=task.team, user=assignee).exists():
            raise ValidationError({'assignee': f"User '{assignee.username}' is not a member of '{task.team.name}'."})

        # Workflow status transition validation
        if status != task.status:
            cls.validate_transition(task.status, status)

        with transaction.atomic():
            # Refresh lock or check version
            # Execute conditional atomic update on version
            updated_rows = Task.objects.filter(
                pk=task.pk,
                version=submitted_version
            ).update(
                title=title.strip(),
                description=description.strip(),
                priority=priority,
                status=status,
                assignee=assignee,
                due_date=due_date,
                version=F('version') + 1,
                updated_at=timezone.now()
            )

            if updated_rows == 0:
                # Reload current task version to inform the user
                current = Task.objects.get(pk=task.pk)
                raise StaleUpdateError(
                    f"This task was modified by another user (current version is {current.version}, "
                    f"you submitted version {submitted_version}). Please refresh the page to reconcile changes."
                )

            # Record changes in activity log
            changes = []
            prev_state = {}
            new_state = {}

            if task.title != title:
                changes.append(f"Title changed from '{task.title}' to '{title}'.")
                prev_state['title'] = task.title
                new_state['title'] = title
            if task.priority != priority:
                changes.append(f"Priority changed from {task.get_priority_display()} to {TaskPriority(priority).label}.")
                prev_state['priority'] = task.priority
                new_state['priority'] = priority
            if task.status != status:
                changes.append(f"Status changed from {task.get_status_display()} to {TaskStatus(status).label}.")
                prev_state['status'] = task.status
                new_state['status'] = status
            if task.assignee != assignee:
                old_assignee_name = task.assignee.username if task.assignee else "Unassigned"
                new_assignee_name = assignee.username if assignee else "Unassigned"
                changes.append(f"Assignee changed from {old_assignee_name} to {new_assignee_name}.")
                prev_state['assignee'] = old_assignee_name
                new_state['assignee'] = new_assignee_name
            if task.due_date != due_date:
                changes.append(f"Due date changed from {task.due_date} to {due_date}.")
                prev_state['due_date'] = str(task.due_date) if task.due_date else None
                new_state['due_date'] = str(due_date) if due_date else None

            event_type = EventType.TASK_UPDATED
            if task.status != status:
                if status == TaskStatus.RESOLVED:
                    event_type = EventType.TASK_RESOLVED
                elif task.status == TaskStatus.RESOLVED and status == TaskStatus.OPEN:
                    event_type = EventType.TASK_REOPENED
                else:
                    event_type = EventType.STATUS_CHANGED
            elif task.assignee != assignee:
                event_type = EventType.ASSIGNMENT_CHANGED
            elif task.priority != priority:
                event_type = EventType.PRIORITY_CHANGED

            if not changes:
                changes.append("Task description or details updated.")

            ActivityEvent.objects.create(
                actor=user,
                team=task.team,
                task=task,
                event_type=event_type,
                details="; ".join(changes),
                previous_state=prev_state,
                new_state=new_state
            )

            # Notifications on reassignment or status change
            if assignee and assignee != task.assignee and assignee != user:
                NotificationService.create_notification(
                    recipient=assignee,
                    actor=user,
                    category=NotificationCategory.ASSIGNMENT,
                    title=f"Task Assigned: #{task.id}",
                    message=f"{user.get_full_name() or user.username} assigned you to '{title}'.",
                    task=task,
                    team=task.team,
                    is_urgent=(priority == TaskPriority.URGENT)
                )
            if status != task.status and task.assignee and task.assignee != user:
                NotificationService.create_notification(
                    recipient=task.assignee,
                    actor=user,
                    category=NotificationCategory.STATUS,
                    title=f"Task #{task.id} Status: {TaskStatus(status).label}",
                    message=f"{user.get_full_name() or user.username} updated status on '{task.title}' to {TaskStatus(status).label}.",
                    task=task,
                    team=task.team,
                )

            # Reload updated task from db
            task.refresh_from_db()
            return task

    @classmethod
    def transition_status(cls, user, task, new_status, submitted_version=None):
        """
        Quick status transition action (e.g. from task detail or list button).
        """
        if not can_change_task_status(user, task):
            raise PermissionDenied("You do not have permission to change the status of this task.")

        cls.validate_transition(task.status, new_status)
        version_to_check = submitted_version if submitted_version is not None else task.version

        with transaction.atomic():
            updated_rows = Task.objects.filter(
                pk=task.pk,
                version=version_to_check
            ).update(
                status=new_status,
                version=F('version') + 1,
                updated_at=timezone.now()
            )

            if updated_rows == 0:
                current = Task.objects.get(pk=task.pk)
                raise StaleUpdateError(
                    f"Conflict: Task was modified concurrently (current version: {current.version})."
                )

            event_type = (
                EventType.TASK_RESOLVED if new_status == TaskStatus.RESOLVED
                else EventType.TASK_REOPENED if task.status == TaskStatus.RESOLVED
                else EventType.STATUS_CHANGED
            )

            ActivityEvent.objects.create(
                actor=user,
                team=task.team,
                task=task,
                event_type=event_type,
                details=f"Status transitioned from {task.get_status_display()} to {TaskStatus(new_status).label}.",
                previous_state={'status': task.status},
                new_state={'status': new_status}
            )

            if task.assignee and task.assignee != user:
                NotificationService.create_notification(
                    recipient=task.assignee,
                    actor=user,
                    category=NotificationCategory.STATUS,
                    title=f"Task #{task.id} Status: {TaskStatus(new_status).label}",
                    message=f"{user.get_full_name() or user.username} transitioned '{task.title}' to {TaskStatus(new_status).label}.",
                    task=task,
                    team=task.team,
                )

            task.refresh_from_db()
            return task

    @classmethod
    def archive_task(cls, user, task):
        if not can_archive_task(user, task):
            raise PermissionDenied("You do not have permission to archive this task.")

        with transaction.atomic():
            task.is_archived = True
            task.archived_at = timezone.now()
            task.version += 1
            task.save(update_fields=['is_archived', 'archived_at', 'version', 'updated_at'])

            ActivityEvent.objects.create(
                actor=user,
                team=task.team,
                task=task,
                event_type=EventType.TASK_ARCHIVED,
                details=f"Task '{task.title}' was archived.",
                previous_state={'is_archived': False},
                new_state={'is_archived': True, 'archived_at': str(task.archived_at)}
            )
            return task

    @classmethod
    def restore_task(cls, user, task):
        if not can_archive_task(user, task):
            raise PermissionDenied("You do not have permission to restore this task.")

        with transaction.atomic():
            task.is_archived = False
            task.archived_at = None
            task.version += 1
            task.save(update_fields=['is_archived', 'archived_at', 'version', 'updated_at'])

            ActivityEvent.objects.create(
                actor=user,
                team=task.team,
                task=task,
                event_type=EventType.TASK_UNARCHIVED,
                details=f"Task '{task.title}' was restored from archive.",
                previous_state={'is_archived': True},
                new_state={'is_archived': False}
            )
            return task


class TeamService:

    @classmethod
    def create_team(cls, user, name, description=''):
        """
        Creates a new team and assigns the creator as ADMIN.
        """
        with transaction.atomic():
            team = Team.objects.create(
                name=name.strip(),
                description=description.strip()
            )
            TeamMembership.objects.create(
                team=team,
                user=user,
                role=TeamRole.ADMIN
            )
            ActivityEvent.objects.create(
                actor=user,
                team=team,
                event_type=EventType.MEMBER_ADDED,
                details=f"Team '{team.name}' created by {user.username} as Team Admin.",
                new_state={'role': TeamRole.ADMIN, 'user': user.username}
            )
            return team

    @classmethod
    def add_member(cls, actor, team, user_to_add, role=TeamRole.MEMBER):
        if not can_manage_team(actor, team):
            raise PermissionDenied(f"Only Team Admins can add members to '{team.name}'.")

        with transaction.atomic():
            membership, created = TeamMembership.objects.get_or_create(
                team=team,
                user=user_to_add,
                defaults={'role': role}
            )
            if not created:
                raise ValidationError(f"User '{user_to_add.username}' is already a member of '{team.name}'.")

            ActivityEvent.objects.create(
                actor=actor,
                team=team,
                event_type=EventType.MEMBER_ADDED,
                details=f"{user_to_add.username} was added to team as {membership.get_role_display()} by {actor.username}.",
                new_state={'user': user_to_add.username, 'role': role}
            )
            return membership

    @classmethod
    def update_member_role(cls, actor, membership, new_role):
        if not can_change_member_role(actor, membership, new_role):
            raise PermissionDenied("You do not have permission to modify this role, or this would remove the sole Admin.")

        with transaction.atomic():
            old_role = membership.role
            membership.role = new_role
            membership.save(update_fields=['role'])

            ActivityEvent.objects.create(
                actor=actor,
                team=membership.team,
                event_type=EventType.MEMBER_ROLE_CHANGED,
                details=f"Role for {membership.user.username} changed from {TeamRole(old_role).label} to {TeamRole(new_role).label}.",
                previous_state={'role': old_role},
                new_state={'role': new_role}
            )
            return membership

    @classmethod
    def remove_member(cls, actor, membership):
        if not can_remove_member(actor, membership):
            raise PermissionDenied("Cannot remove member: insufficient permissions or cannot remove the sole Admin.")

        with transaction.atomic():
            # If member had assigned tasks in this team, unassign them to preserve referential integrity safely
            Task.objects.filter(team=membership.team, assignee=membership.user).update(assignee=None)

            username = membership.user.username
            team = membership.team
            membership.delete()

            ActivityEvent.objects.create(
                actor=actor,
                team=team,
                event_type=EventType.MEMBER_REMOVED,
                details=f"{username} was removed from team '{team.name}' by {actor.username}.",
                previous_state={'user': username}
            )


class DashboardService:

    @classmethod
    def get_dashboard_data(cls, user, selected_team_id=None):
        """
        Computes accurate, permission-scoped dashboard metrics, charts, and activity.
        Handles empty databases cleanly and eliminates N+1 queries.
        """
        accessible_teams = get_accessible_teams_qs(user)
        accessible_team_ids = list(accessible_teams.values_list('id', flat=True))

        tasks_qs = Task.objects.filter(
            team_id__in=accessible_team_ids,
            is_archived=False
        )

        selected_team = None
        if selected_team_id:
            try:
                selected_team = accessible_teams.get(pk=selected_team_id)
                tasks_qs = tasks_qs.filter(team=selected_team)
            except Team.DoesNotExist:
                selected_team = None

        today = timezone.localdate()

        # Summary KPIs
        total_active = tasks_qs.count()
        open_count = tasks_qs.filter(status=TaskStatus.OPEN).count()
        in_progress_count = tasks_qs.filter(status=TaskStatus.IN_PROGRESS).count()
        resolved_count = tasks_qs.filter(status=TaskStatus.RESOLVED).count()
        
        overdue_qs = tasks_qs.filter(
            due_date__lt=today
        ).exclude(status=TaskStatus.RESOLVED).select_related('team', 'assignee')
        overdue_count = overdue_qs.count()

        my_tasks_count = tasks_qs.filter(assignee=user).exclude(status=TaskStatus.RESOLVED).count()

        # Status distribution for charts
        status_chart_data = {
            'labels': ['Open', 'In Progress', 'Resolved'],
            'data': [open_count, in_progress_count, resolved_count],
            'colors': ['#3b82f6', '#f59e0b', '#10b981'],
        }

        # Priority distribution
        priority_counts = {
            p[0]: 0 for p in TaskPriority.choices
        }
        for item in tasks_qs.values('priority').annotate(count=Count('id')):
            priority_counts[item['priority']] = item['count']

        priority_chart_data = {
            'labels': [TaskPriority(p).label for p in TaskPriority.values],
            'data': [priority_counts[p] for p in TaskPriority.values],
            'colors': ['#94a3b8', '#3b82f6', '#f97316', '#ef4444'],
        }

        # Team workload: Tasks by assignee
        workload_query = tasks_qs.exclude(status=TaskStatus.RESOLVED).values(
            'assignee__username'
        ).annotate(count=Count('id')).order_by('-count')[:10]

        workload_labels = []
        workload_values = []
        for row in workload_query:
            workload_labels.append(row['assignee__username'] or 'Unassigned')
            workload_values.append(row['count'])

        workload_chart_data = {
            'labels': workload_labels,
            'data': workload_values,
        }

        # 7-day trend: created vs resolved
        trend_days = []
        created_trend = []
        resolved_trend = []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            trend_days.append(day.strftime('%b %d'))
            day_created = Task.objects.filter(
                team_id__in=accessible_team_ids,
                created_at__date=day
            )
            day_resolved = ActivityEvent.objects.filter(
                team_id__in=accessible_team_ids,
                event_type=EventType.TASK_RESOLVED,
                created_at__date=day
            )
            if selected_team:
                day_created = day_created.filter(team=selected_team)
                day_resolved = day_resolved.filter(team=selected_team)

            created_trend.append(day_created.count())
            resolved_trend.append(day_resolved.count())

        activity_trend_data = {
            'labels': trend_days,
            'created': created_trend,
            'resolved': resolved_trend,
        }

        # Recent activity events
        recent_activity_qs = ActivityEvent.objects.filter(
            team_id__in=accessible_team_ids
        ).select_related('actor', 'team', 'task')
        if selected_team:
            recent_activity_qs = recent_activity_qs.filter(team=selected_team)
        recent_activity = recent_activity_qs[:10]

        # Overdue tasks list
        overdue_tasks = overdue_qs[:6]

        return {
            'accessible_teams': accessible_teams,
            'selected_team': selected_team,
            'total_active': total_active,
            'open_count': open_count,
            'in_progress_count': in_progress_count,
            'resolved_count': resolved_count,
            'overdue_count': overdue_count,
            'my_tasks_count': my_tasks_count,
            'status_chart_data': status_chart_data,
            'priority_chart_data': priority_chart_data,
            'workload_chart_data': workload_chart_data,
            'activity_trend_data': activity_trend_data,
            'recent_activity': recent_activity,
            'overdue_tasks': overdue_tasks,
        }


class NotificationService:
    @classmethod
    def create_notification(cls, recipient, category, title, message, actor=None, task=None, invitation=None, team=None, is_urgent=False):
        return InAppNotification.objects.create(
            recipient=recipient,
            actor=actor,
            category=category,
            title=title,
            message=message,
            task=task,
            invitation=invitation,
            team=team,
            is_urgent=is_urgent
        )

    @classmethod
    def mark_as_read(cls, user, notification_id):
        notification = InAppNotification.objects.filter(pk=notification_id, recipient=user).first()
        if notification and not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
        return notification

    @classmethod
    def mark_all_as_read(cls, user):
        return InAppNotification.objects.filter(recipient=user, is_read=False).update(
            is_read=True,
            read_at=timezone.now()
        )

    @classmethod
    def check_and_create_deadline_alerts(cls, user):
        """
        Scans tasks assigned to user that are overdue or due today,
        and generates InAppNotification alerts if one hasn't been created recently.
        """
        today = timezone.localdate()
        my_tasks = Task.objects.filter(
            assignee=user,
            is_archived=False
        ).exclude(status=TaskStatus.RESOLVED)

        # 1. Overdue alerts
        for task in my_tasks.filter(due_date__lt=today):
            recent_alert = InAppNotification.objects.filter(
                recipient=user,
                task=task,
                category=NotificationCategory.DEADLINE,
                created_at__gte=timezone.now() - timedelta(days=1)
            ).exists()
            if not recent_alert:
                cls.create_notification(
                    recipient=user,
                    actor=None,
                    category=NotificationCategory.DEADLINE,
                    title=f"Overdue Task: #{task.id} {task.title}",
                    message=f"Task was due on {task.due_date}. Please resolve or update milestone.",
                    task=task,
                    team=task.team,
                    is_urgent=True
                )

        # 2. Due today
        for task in my_tasks.filter(due_date=today):
            recent_alert = InAppNotification.objects.filter(
                recipient=user,
                task=task,
                category=NotificationCategory.DEADLINE,
                created_at__gte=timezone.now() - timedelta(days=1)
            ).exists()
            if not recent_alert:
                cls.create_notification(
                    recipient=user,
                    actor=None,
                    category=NotificationCategory.DEADLINE,
                    title=f"Due Today: #{task.id} {task.title}",
                    message=f"Target completion deadline is today ({task.due_date}).",
                    task=task,
                    team=task.team,
                    is_urgent=False
                )


class InvitationService:
    @classmethod
    def create_invitation(cls, inviter, team, invitee_email, role=TeamRole.MEMBER):
        if not can_manage_team(inviter, team):
            raise PermissionDenied("Only Team Admins can invite new members.")

        email_clean = invitee_email.strip().lower()

        # Check if already a member
        if TeamMembership.objects.filter(team=team, user__email__iexact=email_clean).exists():
            raise ValidationError(f"A user with email '{email_clean}' is already a member of '{team.name}'.")

        # Check existing pending invitation
        existing_invite = WorkspaceInvitation.objects.filter(
            team=team,
            invitee_email__iexact=email_clean,
            status=InvitationStatus.PENDING
        ).first()

        if existing_invite:
            if existing_invite.is_expired:
                existing_invite.status = InvitationStatus.EXPIRED
                existing_invite.save(update_fields=['status'])
            else:
                raise ValidationError(f"A pending invitation already exists for '{email_clean}'.")

        # Match existing user if registered
        matching_user = User.objects.filter(email__iexact=email_clean).first()

        with transaction.atomic():
            invite = WorkspaceInvitation.objects.create(
                team=team,
                inviter=inviter,
                invitee_email=email_clean,
                invitee_user=matching_user,
                role=role,
                status=InvitationStatus.PENDING,
                expires_at=timezone.now() + timedelta(days=7)
            )

            ActivityEvent.objects.create(
                actor=inviter,
                team=team,
                event_type=EventType.MEMBER_ADDED,
                details=f"Invitation sent to '{email_clean}' for role {TeamRole(role).label} by {inviter.username}.",
                new_state={'email': email_clean, 'role': role, 'invite_id': invite.id}
            )

            # In-App Notification if user exists
            if matching_user:
                NotificationService.create_notification(
                    recipient=matching_user,
                    actor=inviter,
                    category=NotificationCategory.INVITATION,
                    title=f"Workspace Invitation: {team.name}",
                    message=f"{inviter.get_full_name() or inviter.username} invited you to join '{team.name}' as {TeamRole(role).label}.",
                    invitation=invite,
                    team=team,
                    is_urgent=False
                )

            return invite

    @classmethod
    def accept_invitation(cls, user, invitation_id):
        invite = WorkspaceInvitation.objects.select_for_update().filter(pk=invitation_id).first()
        if not invite:
            raise ValidationError("Invitation not found.")

        if invite.status != InvitationStatus.PENDING:
            raise ValidationError(f"This invitation is {invite.get_status_display().lower()} and can no longer be accepted.")

        if invite.is_expired:
            invite.status = InvitationStatus.EXPIRED
            invite.save(update_fields=['status'])
            raise ValidationError("This invitation has expired.")

        # Verify authorized recipient
        matches_user = (invite.invitee_user_id == user.id)
        matches_email = bool(user.email and invite.invitee_email.lower() == user.email.lower())
        if not (matches_user or matches_email):
            raise PermissionDenied("You are not the designated recipient of this invitation.")

        with transaction.atomic():
            membership, created = TeamMembership.objects.get_or_create(
                team=invite.team,
                user=user,
                defaults={'role': invite.role}
            )
            if not created:
                membership.role = invite.role
                membership.save(update_fields=['role'])

            invite.status = InvitationStatus.ACCEPTED
            invite.invitee_user = user
            invite.save(update_fields=['status', 'invitee_user'])

            ActivityEvent.objects.create(
                actor=user,
                team=invite.team,
                event_type=EventType.MEMBER_ADDED,
                details=f"{user.username} accepted invitation and joined '{invite.team.name}' as {TeamRole(invite.role).label}.",
                new_state={'user': user.username, 'role': invite.role}
            )

            # Notify inviter
            NotificationService.create_notification(
                recipient=invite.inviter,
                actor=user,
                category=NotificationCategory.INVITATION,
                title=f"Invitation Accepted: {invite.team.name}",
                message=f"{user.get_full_name() or user.username} accepted your invitation to join '{invite.team.name}'.",
                team=invite.team
            )

            return membership

    @classmethod
    def decline_invitation(cls, user, invitation_id):
        invite = WorkspaceInvitation.objects.filter(pk=invitation_id).first()
        if not invite:
            raise ValidationError("Invitation not found.")

        matches_user = (invite.invitee_user_id == user.id)
        matches_email = bool(user.email and invite.invitee_email.lower() == user.email.lower())
        if not (matches_user or matches_email):
            raise PermissionDenied("You are not the designated recipient of this invitation.")

        if invite.status != InvitationStatus.PENDING:
            raise ValidationError("This invitation is not pending.")

        with transaction.atomic():
            invite.status = InvitationStatus.DECLINED
            invite.save(update_fields=['status'])

            ActivityEvent.objects.create(
                actor=user,
                team=invite.team,
                event_type=EventType.MEMBER_REMOVED,
                details=f"{user.username} declined invitation to join '{invite.team.name}'.",
                previous_state={'status': InvitationStatus.PENDING},
                new_state={'status': InvitationStatus.DECLINED}
            )
            return invite

    @classmethod
    def revoke_invitation(cls, actor, invitation_id):
        invite = WorkspaceInvitation.objects.filter(pk=invitation_id).first()
        if not invite:
            raise ValidationError("Invitation not found.")

        if not can_manage_team(actor, invite.team):
            raise PermissionDenied("Only Team Admins can revoke invitations.")

        with transaction.atomic():
            invite.status = InvitationStatus.REVOKED
            invite.save(update_fields=['status'])

            ActivityEvent.objects.create(
                actor=actor,
                team=invite.team,
                event_type=EventType.MEMBER_REMOVED,
                details=f"Invitation to '{invite.invitee_email}' was revoked by {actor.username}.",
                new_state={'status': InvitationStatus.REVOKED}
            )
            return invite


class AttachmentService:
    @classmethod
    def add_attachment(cls, user, task, uploaded_file):
        if not can_edit_task(user, task):
            raise PermissionDenied("You do not have permission to attach files to this task.")

        # File validation: max size 25MB
        if uploaded_file.size > 25 * 1024 * 1024:
            raise ValidationError("File size exceeds 25MB limit.")

        with transaction.atomic():
            attachment = TaskAttachment.objects.create(
                task=task,
                uploader=user,
                file=uploaded_file,
                filename=uploaded_file.name,
                file_size=uploaded_file.size,
                content_type=getattr(uploaded_file, 'content_type', '')
            )

            ActivityEvent.objects.create(
                actor=user,
                team=task.team,
                task=task,
                event_type=EventType.TASK_UPDATED,
                details=f"File attached: '{attachment.filename}' ({attachment.human_size}) by {user.username}.",
                new_state={'filename': attachment.filename, 'size': attachment.file_size}
            )

            return attachment

    @classmethod
    def delete_attachment(cls, user, attachment_id):
        attachment = TaskAttachment.objects.filter(pk=attachment_id).select_related('task__team').first()
        if not attachment:
            raise ValidationError("Attachment not found.")

        is_owner = (attachment.uploader_id == user.id)
        can_manage = can_edit_task(user, attachment.task)
        if not (is_owner or can_manage):
            raise PermissionDenied("You do not have permission to delete this attachment.")

        with transaction.atomic():
            filename = attachment.filename
            task = attachment.task
            team = task.team

            # Delete physical file from storage
            if attachment.file:
                attachment.file.delete(save=False)
            attachment.delete()

            ActivityEvent.objects.create(
                actor=user,
                team=team,
                task=task,
                event_type=EventType.TASK_UPDATED,
                details=f"Attachment removed: '{filename}' by {user.username}.",
                previous_state={'filename': filename}
            )

