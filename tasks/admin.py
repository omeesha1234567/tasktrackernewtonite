from django.contrib import admin
from .models import (
    Team,
    TeamMembership,
    Task,
    ActivityEvent,
    IdempotencyRecord,
    TaskAttachment,
    WorkspaceInvitation,
    InAppNotification,
)


class TeamMembershipInline(admin.TabularInline):
    model = TeamMembership
    extra = 1
    autocomplete_fields = ['user']


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at', 'member_count', 'active_tasks_count')
    search_fields = ('name', 'description')
    inlines = [TeamMembershipInline]


@admin.register(TeamMembership)
class TeamMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'team', 'role', 'created_at')
    list_filter = ('role', 'team')
    search_fields = ('user__username', 'user__email', 'team__name')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'team', 'status', 'priority', 'assignee', 'creator', 'due_date', 'version', 'is_archived')
    list_filter = ('status', 'priority', 'is_archived', 'team')
    search_fields = ('title', 'description', 'assignee__username', 'creator__username')
    readonly_fields = ('version', 'created_at', 'updated_at', 'archived_at')


@admin.register(ActivityEvent)
class ActivityEventAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'actor', 'team', 'task', 'event_type', 'details')
    list_filter = ('event_type', 'team')
    search_fields = ('details', 'actor__username', 'task__title')
    readonly_fields = ('actor', 'team', 'task', 'event_type', 'details', 'previous_state', 'new_state', 'created_at')


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    list_display = ('key', 'user', 'action', 'response_code', 'task', 'created_at')
    list_filter = ('action',)
    search_fields = ('key', 'user__username')
    readonly_fields = ('key', 'user', 'action', 'response_code', 'task', 'created_at')


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(admin.ModelAdmin):
    list_display = ('filename', 'task', 'uploader', 'file_size', 'created_at')
    search_fields = ('filename', 'task__title', 'uploader__username')
    readonly_fields = ('filename', 'file_size', 'content_type', 'created_at')


@admin.register(WorkspaceInvitation)
class WorkspaceInvitationAdmin(admin.ModelAdmin):
    list_display = ('team', 'invitee_email', 'role', 'status', 'inviter', 'created_at', 'expires_at')
    list_filter = ('status', 'role', 'team')
    search_fields = ('invitee_email', 'team__name', 'inviter__username')


@admin.register(InAppNotification)
class InAppNotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'recipient', 'category', 'is_read', 'is_urgent', 'created_at')
    list_filter = ('category', 'is_read', 'is_urgent')
    search_fields = ('title', 'message', 'recipient__username')
