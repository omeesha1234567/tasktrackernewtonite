from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from django.core.exceptions import ValidationError


class TeamRole(models.TextChoices):
    ADMIN = 'ADMIN', 'Admin'
    MANAGER = 'MANAGER', 'Manager'
    MEMBER = 'MEMBER', 'Member'


class TaskStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    RESOLVED = 'RESOLVED', 'Resolved'


class TaskPriority(models.TextChoices):
    LOW = 'LOW', 'Low'
    MEDIUM = 'MEDIUM', 'Medium'
    HIGH = 'HIGH', 'High'
    URGENT = 'URGENT', 'Urgent'


class EventType(models.TextChoices):
    TASK_CREATED = 'TASK_CREATED', 'Task Created'
    TASK_UPDATED = 'TASK_UPDATED', 'Task Updated'
    STATUS_CHANGED = 'STATUS_CHANGED', 'Status Changed'
    PRIORITY_CHANGED = 'PRIORITY_CHANGED', 'Priority Changed'
    ASSIGNMENT_CHANGED = 'ASSIGNMENT_CHANGED', 'Assignment Changed'
    TASK_RESOLVED = 'TASK_RESOLVED', 'Task Resolved'
    TASK_REOPENED = 'TASK_REOPENED', 'Task Reopened'
    TASK_ARCHIVED = 'TASK_ARCHIVED', 'Task Archived'
    TASK_UNARCHIVED = 'TASK_UNARCHIVED', 'Task Restored'
    MEMBER_ADDED = 'MEMBER_ADDED', 'Member Added'
    MEMBER_ROLE_CHANGED = 'MEMBER_ROLE_CHANGED', 'Member Role Changed'
    MEMBER_REMOVED = 'MEMBER_REMOVED', 'Member Removed'


class Team(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="Unique name for the team.")
    description = models.TextField(blank=True, help_text="Short description of the team's mandate.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Team'
        verbose_name_plural = 'Teams'

    def __str__(self):
        return self.name

    def member_count(self):
        return self.memberships.count()

    def active_tasks_count(self):
        return self.tasks.filter(is_archived=False).count()

    def open_tasks_count(self):
        return self.tasks.filter(is_archived=False, status=TaskStatus.OPEN).count()

    def overdue_tasks_count(self):
        today = timezone.localdate()
        return self.tasks.filter(
            is_archived=False,
            due_date__lt=today
        ).exclude(status=TaskStatus.RESOLVED).count()


class TeamMembership(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='team_memberships')
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(
        max_length=20,
        choices=TeamRole.choices,
        default=TeamRole.MEMBER,
        help_text="Role of the user within this team."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['team', 'user']
        verbose_name = 'Team Membership'
        verbose_name_plural = 'Team Memberships'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'team'],
                name='unique_user_team_membership'
            )
        ]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()}) @ {self.team.name}"


class Task(models.Model):
    title = models.CharField(max_length=200, help_text="Concise title for the task.")
    description = models.TextField(blank=True, help_text="Detailed description of what needs to be done.")
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.OPEN,
        help_text="Current state in the task workflow."
    )
    priority = models.CharField(
        max_length=20,
        choices=TaskPriority.choices,
        default=TaskPriority.MEDIUM,
        help_text="Relative urgency or importance."
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='tasks',
        help_text="Team responsible for this task."
    )
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
        help_text="Team member currently assigned to execute the task."
    )
    creator = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_tasks',
        help_text="User who created the task."
    )
    due_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional deadline for completion."
    )
    is_archived = models.BooleanField(
        default=False,
        help_text="Archived tasks are retained for audit history but hidden from active workflows."
    )
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(
        default=1,
        help_text="Incremented on every update for optimistic concurrency control (stale-update protection)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Task'
        verbose_name_plural = 'Tasks'
        indexes = [
            models.Index(fields=['team', 'is_archived', 'status'], name='task_team_arch_status_idx'),
            models.Index(fields=['team', 'is_archived', 'priority'], name='task_team_arch_prio_idx'),
            models.Index(fields=['assignee', 'is_archived', 'status'], name='task_assignee_status_idx'),
            models.Index(fields=['due_date'], name='task_due_date_idx'),
            models.Index(fields=['created_at'], name='task_created_at_idx'),
        ]

    def __str__(self):
        return f"[{self.team.name}] #{self.id} {self.title}"

    @property
    def is_overdue(self):
        if not self.due_date or self.status == TaskStatus.RESOLVED or self.is_archived:
            return False
        return self.due_date < timezone.localdate()

    def clean(self):
        super().clean()
        # Invariant: If an assignee is selected, they MUST be an active member of the selected team.
        if self.assignee and self.team_id:
            if not TeamMembership.objects.filter(team_id=self.team_id, user=self.assignee).exists():
                raise ValidationError({
                    'assignee': f"The selected assignee ({self.assignee.username}) is not a member of team '{self.team.name}'."
                })


class ActivityEvent(models.Model):
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_events',
        help_text="User who initiated this action (null if automated or user deleted)."
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='activity_events',
        help_text="Team context for this activity event."
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_events',
        help_text="Associated task if applicable."
    )
    event_type = models.CharField(
        max_length=50,
        choices=EventType.choices,
        help_text="Type of activity recorded."
    )
    details = models.TextField(help_text="Human-readable description of the action.")
    previous_state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured snapshot of fields prior to change."
    )
    new_state = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured snapshot of fields after change."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Activity Event'
        verbose_name_plural = 'Activity Events'
        indexes = [
            models.Index(fields=['team', 'created_at'], name='activity_team_created_idx'),
            models.Index(fields=['task', 'created_at'], name='activity_task_created_idx'),
            models.Index(fields=['actor', 'created_at'], name='activity_actor_created_idx'),
        ]

    def __str__(self):
        actor_name = self.actor.username if self.actor else "System"
        return f"{self.created_at.strftime('%Y-%m-%d %H:%M')} - {actor_name}: {self.get_event_type_display()}"


class IdempotencyRecord(models.Model):
    """
    Prevents duplicate form submissions (e.g. repeated task creations)
    using an idempotency key generated on client or form rendering.
    Enforces atomic, unique processing per user and logical action.
    """
    key = models.CharField(max_length=64, help_text="Unique client-supplied idempotency key.")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='idempotency_records',
        help_text="User performing the request."
    )
    action = models.CharField(
        max_length=64,
        default='TASK_CREATE',
        help_text="Logical action identifier (e.g. TASK_CREATE)."
    )
    response_code = models.IntegerField(default=200)
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Task created as a result of this idempotent execution."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Idempotency Record'
        verbose_name_plural = 'Idempotency Records'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'key', 'action'],
                name='unique_user_key_action_idempotency'
            )
        ]

    def __str__(self):
        return f"IdempotencyRecord({self.user.username}, {self.key}, {self.action})"


class TaskAttachment(models.Model):
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='attachments',
        help_text="Task this file is attached to."
    )
    uploader = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_attachments',
        help_text="User who uploaded the attachment."
    )
    file = models.FileField(upload_to='attachments/%Y/%m/')
    filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0, help_text="File size in bytes.")
    content_type = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Task Attachment'
        verbose_name_plural = 'Task Attachments'

    def __str__(self):
        return f"{self.filename} (#{self.task_id})"

    @property
    def human_size(self):
        bytes_val = self.file_size
        if bytes_val < 1024:
            return f"{bytes_val} B"
        elif bytes_val < 1024 * 1024:
            return f"{bytes_val / 1024:.1f} KB"
        else:
            return f"{bytes_val / (1024 * 1024):.1f} MB"

    @property
    def is_image(self):
        return self.content_type.startswith('image/') or self.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'))


class InvitationStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    ACCEPTED = 'ACCEPTED', 'Accepted'
    DECLINED = 'DECLINED', 'Declined'
    REVOKED = 'REVOKED', 'Revoked'
    EXPIRED = 'EXPIRED', 'Expired'


class WorkspaceInvitation(models.Model):
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='invitations',
        help_text="Target workspace team."
    )
    inviter = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_invitations',
        help_text="Team Admin or Owner who initiated the invitation."
    )
    invitee_email = models.EmailField(help_text="Email of the invited user.")
    invitee_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='received_invitations',
        help_text="User account matching the email if one exists."
    )
    role = models.CharField(
        max_length=20,
        choices=TeamRole.choices,
        default=TeamRole.MEMBER,
        help_text="Proposed role for the user upon accepting."
    )
    status = models.CharField(
        max_length=20,
        choices=InvitationStatus.choices,
        default=InvitationStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(help_text="Deadline for accepting invitation.")

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Workspace Invitation'
        verbose_name_plural = 'Workspace Invitations'

    def __str__(self):
        return f"Invite: {self.invitee_email} -> {self.team.name} ({self.get_status_display()})"

    @property
    def is_expired(self):
        if self.status != InvitationStatus.PENDING:
            return False
        return timezone.now() > self.expires_at


class NotificationCategory(models.TextChoices):
    ASSIGNMENT = 'ASSIGNMENT', 'Task Assignment'
    DEADLINE = 'DEADLINE', 'Deadline Alert'
    STATUS = 'STATUS', 'Task Status'
    INVITATION = 'INVITATION', 'Workspace Invitation'
    GENERAL = 'GENERAL', 'General Update'


class InAppNotification(models.Model):
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='inapp_notifications',
        help_text="Target recipient of the notification."
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='triggered_notifications',
        help_text="User whose action triggered the notification (or null if system)."
    )
    category = models.CharField(
        max_length=30,
        choices=NotificationCategory.choices,
        default=NotificationCategory.GENERAL
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications'
    )
    invitation = models.ForeignKey(
        WorkspaceInvitation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications'
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications'
    )
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    is_urgent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'In-App Notification'
        verbose_name_plural = 'In-App Notifications'
        indexes = [
            models.Index(fields=['recipient', 'is_read', 'created_at'], name='notif_recip_read_idx'),
            models.Index(fields=['recipient', 'category'], name='notif_recip_cat_idx'),
        ]

    def __str__(self):
        return f"[{self.get_category_display()}] for {self.recipient.username}: {self.title}"

