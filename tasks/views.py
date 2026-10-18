import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import JsonResponse, HttpResponseForbidden, HttpResponseNotFound, FileResponse, Http404
from django.views.decorators.http import require_POST
from django.db.models import Q
from django.utils import timezone

from .models import (
    Team,
    TeamMembership,
    TeamRole,
    Task,
    TaskStatus,
    TaskPriority,
    ActivityEvent,
    EventType,
    TaskAttachment,
    WorkspaceInvitation,
    InvitationStatus,
    InAppNotification,
    NotificationCategory,
)
from .forms import (
    LoginForm,
    UserRegistrationForm,
    TaskCreateForm,
    TaskEditForm,
    TeamForm,
    AddTeamMemberForm,
    ChangeMemberRoleForm,
    TaskAttachmentForm,
    WorkspaceInvitationForm,
)
from .permissions import (
    can_view_team,
    can_manage_team,
    can_create_task,
    can_view_task,
    can_edit_task,
    can_change_task_status,
    can_archive_task,
    get_user_team_role,
    get_accessible_teams_qs,
    get_accessible_tasks_qs,
)
from .services import (
    TaskService,
    TeamService,
    DashboardService,
    NotificationService,
    InvitationService,
    AttachmentService,
    StaleUpdateError,
    InvalidWorkflowTransitionError,
)


# --- Authentication Views ---

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        username = form.cleaned_data['username']
        password = form.cleaned_data['password']
        remember_me = form.cleaned_data.get('remember_me', False)

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            if not remember_me:
                request.session.set_expiry(0)
            else:
                request.session.set_expiry(1209600)
            messages.success(request, f"Welcome back, {user.first_name or user.username}!")
            next_url = request.GET.get('next') or 'dashboard'
            return redirect(next_url)
        else:
            messages.error(request, "Invalid username or password. Please verify your credentials.")

    return render(request, 'auth/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, "You have been securely logged out.")
    return redirect('login')


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = UserRegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.set_password(form.cleaned_data['password'])
        user.save()

        # Onboarding: automatically provision a default team for the user
        default_team_name = f"{user.username.capitalize()}'s Team"
        counter = 1
        base_name = default_team_name
        while Team.objects.filter(name=default_team_name).exists():
            default_team_name = f"{base_name} ({counter})"
            counter += 1

        team = TeamService.create_team(
            user=user,
            name=default_team_name,
            description="Default workspace created during onboarding."
        )

        login(request, user)
        messages.success(request, f"Welcome to Newtonite! Your workspace '{team.name}' has been created.")
        return redirect('dashboard')

    return render(request, 'auth/register.html', {'form': form})


# --- Dashboard ---

@login_required
def dashboard_view(request):
    team_id = request.GET.get('team')
    dashboard_data = DashboardService.get_dashboard_data(request.user, selected_team_id=team_id)

    # Check for upcoming and overdue deadline alerts
    NotificationService.check_and_create_deadline_alerts(request.user)

    # Pending workspace invitations for current user
    pending_invitations = WorkspaceInvitation.objects.filter(
        Q(invitee_user=request.user) | Q(invitee_email__iexact=request.user.email),
        status=InvitationStatus.PENDING
    ).select_related('team', 'inviter').distinct()

    context = {
        'title': 'Dashboard',
        'pending_invitations': pending_invitations,
        **dashboard_data
    }
    return render(request, 'dashboard/index.html', context)


# --- Tasks Views ---

@login_required
def task_list_view(request):
    # Query parameters
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    priority = request.GET.get('priority', '').strip()
    team_id = request.GET.get('team', '').strip()
    assignee_id = request.GET.get('assignee', '').strip()
    is_overdue = request.GET.get('overdue', '').strip() == '1'
    my_tasks = request.GET.get('my', '').strip() == '1'
    show_archived = request.GET.get('archived', '').strip() == '1'
    sort = request.GET.get('sort', '-created_at')

    # Security: Queryset is strictly authorization-scoped
    tasks_qs = get_accessible_tasks_qs(request.user, include_archived=show_archived)

    if my_tasks:
        tasks_qs = tasks_qs.filter(assignee=request.user)

    if q:
        tasks_qs = tasks_qs.filter(
            Q(title__icontains=q) | Q(description__icontains=q) | Q(id__icontains=q)
        )

    if status and status in TaskStatus.values:
        tasks_qs = tasks_qs.filter(status=status)

    if priority and priority in TaskPriority.values:
        tasks_qs = tasks_qs.filter(priority=priority)

    if team_id and team_id.isdigit():
        tasks_qs = tasks_qs.filter(team_id=team_id)

    if assignee_id:
        if assignee_id == 'unassigned':
            tasks_qs = tasks_qs.filter(assignee__isnull=True)
        elif assignee_id.isdigit():
            tasks_qs = tasks_qs.filter(assignee_id=assignee_id)

    if is_overdue:
        tasks_qs = tasks_qs.filter(
            due_date__lt=timezone.localdate()
        ).exclude(status=TaskStatus.RESOLVED)

    allowed_sorts = {
        '-created_at': '-created_at',
        'created_at': 'created_at',
        'due_date': 'due_date',
        '-due_date': '-due_date',
        'priority': 'priority',
        'status': 'status',
        'title': 'title',
    }
    sort_field = allowed_sorts.get(sort, '-created_at')
    tasks_qs = tasks_qs.order_by(sort_field)

    paginator = Paginator(tasks_qs, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    accessible_teams = get_accessible_teams_qs(request.user)

    context = {
        'title': 'My Tasks' if my_tasks else 'All Tasks',
        'page_obj': page_obj,
        'tasks': page_obj.object_list,
        'accessible_teams': accessible_teams,
        'q': q,
        'status': status,
        'priority': priority,
        'team_id': team_id,
        'assignee_id': assignee_id,
        'is_overdue': is_overdue,
        'my_tasks': my_tasks,
        'show_archived': show_archived,
        'sort': sort,
        'status_choices': TaskStatus.choices,
        'priority_choices': TaskPriority.choices,
    }
    return render(request, 'tasks/list.html', context)


@login_required
def task_detail_view(request, pk):
    task = get_object_or_404(
        Task.objects.select_related('team', 'assignee', 'creator'),
        pk=pk
    )

    if not can_view_task(request.user, task):
        raise PermissionDenied("You do not have access to view this task.")

    activity_timeline = ActivityEvent.objects.filter(
        task=task
    ).select_related('actor').order_by('-created_at')

    # Allowed transitions from current state
    allowed_transitions = [
        {'status': s, 'label': TaskStatus(s).label}
        for s in TaskService.VALID_TRANSITIONS.get(task.status, [])
    ]

    can_edit = can_edit_task(request.user, task)
    can_change_status = can_change_task_status(request.user, task)
    can_archive = can_archive_task(request.user, task)
    user_team_role = get_user_team_role(request.user, task.team)

    # Attachments
    attachments = task.attachments.select_related('uploader').order_by('-created_at')
    attachment_form = TaskAttachmentForm() if can_edit else None

    context = {
        'title': f"Task #{task.id}: {task.title}",
        'task': task,
        'activity_timeline': activity_timeline,
        'allowed_transitions': allowed_transitions,
        'can_edit': can_edit,
        'can_change_status': can_change_status,
        'can_archive': can_archive,
        'user_team_role': user_team_role,
        'attachments': attachments,
        'attachment_form': attachment_form,
    }
    return render(request, 'tasks/detail.html', context)


@login_required
def task_create_view(request):
    accessible_teams = get_accessible_teams_qs(request.user)
    if not accessible_teams.exists():
        messages.warning(request, "You need to belong to or create at least one team before creating tasks.")
        return redirect('team_create')

    initial_team_id = request.GET.get('team')
    initial_team = accessible_teams.filter(pk=initial_team_id).first() if initial_team_id else accessible_teams.first()

    if request.method == 'POST':
        form = TaskCreateForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            try:
                task = TaskService.create_task(
                    user=request.user,
                    team=form.cleaned_data['team'],
                    title=form.cleaned_data['title'],
                    description=form.cleaned_data.get('description', ''),
                    priority=form.cleaned_data['priority'],
                    status=form.cleaned_data['status'],
                    assignee=form.cleaned_data.get('assignee'),
                    due_date=form.cleaned_data.get('due_date'),
                    idempotency_key=form.cleaned_data.get('idempotency_key')
                )

                # Process any multiple uploaded files
                uploaded_files = request.FILES.getlist('attachments')
                for f in uploaded_files:
                    try:
                        AttachmentService.add_attachment(request.user, task, f)
                    except ValidationError as ve:
                        messages.warning(request, f"Could not attach '{f.name}': {ve}")

                messages.success(request, f"Task '{task.title}' was created successfully.")
                return redirect('task_detail', pk=task.pk)
            except ValidationError as e:
                form.add_error(None, e)
            except PermissionDenied as e:
                messages.error(request, str(e))
    else:
        form = TaskCreateForm(user=request.user, initial_team=initial_team)

    context = {
        'title': 'Create New Task',
        'form': form,
        'accessible_teams': accessible_teams,
    }
    return render(request, 'tasks/create.html', context)


@login_required
def task_edit_view(request, pk):
    task = get_object_or_404(
        Task.objects.select_related('team', 'assignee', 'creator'),
        pk=pk
    )

    if not can_edit_task(request.user, task):
        raise PermissionDenied("You do not have permission to edit this task.")

    conflict_error = None
    current_task = None

    if request.method == 'POST':
        form = TaskEditForm(request.POST, instance=task)
        if form.is_valid():
            submitted_version = form.cleaned_data['version']
            try:
                updated_task = TaskService.update_task(
                    user=request.user,
                    task=task,
                    title=form.cleaned_data['title'],
                    description=form.cleaned_data['description'],
                    priority=form.cleaned_data['priority'],
                    status=form.cleaned_data['status'],
                    assignee=form.cleaned_data['assignee'],
                    due_date=form.cleaned_data['due_date'],
                    submitted_version=submitted_version
                )
                messages.success(request, f"Task '{updated_task.title}' updated successfully.")
                return redirect('task_detail', pk=updated_task.pk)
            except StaleUpdateError as e:
                conflict_error = str(e)
                current_task = Task.objects.get(pk=task.pk)
                form.initial['version'] = current_task.version
                messages.error(request, "Concurrency Conflict: Someone else updated this task while you were editing.")
            except InvalidWorkflowTransitionError as e:
                form.add_error('status', e)
            except ValidationError as e:
                form.add_error(None, e)
            except PermissionDenied as e:
                messages.error(request, str(e))
    else:
        form = TaskEditForm(instance=task)

    context = {
        'title': f"Edit Task #{task.id}",
        'task': task,
        'form': form,
        'conflict_error': conflict_error,
        'current_task': current_task,
    }
    return render(request, 'tasks/edit.html', context)


@login_required
@require_POST
def task_transition_view(request, pk):
    task = get_object_or_404(Task, pk=pk)
    new_status = request.POST.get('status')
    version = request.POST.get('version')
    submitted_version = int(version) if version and version.isdigit() else None

    if not new_status or new_status not in TaskStatus.values:
        messages.error(request, "Invalid status choice.")
        return redirect('task_detail', pk=task.pk)

    try:
        TaskService.transition_status(
            user=request.user,
            task=task,
            new_status=new_status,
            submitted_version=submitted_version
        )
        messages.success(request, f"Task status updated to '{TaskStatus(new_status).label}'.")
    except StaleUpdateError as e:
        messages.error(request, str(e))
    except InvalidWorkflowTransitionError as e:
        messages.error(request, str(e))
    except PermissionDenied as e:
        messages.error(request, str(e))

    return redirect('task_detail', pk=task.pk)


@login_required
@require_POST
def task_archive_view(request, pk):
    task = get_object_or_404(Task, pk=pk)
    try:
        TaskService.archive_task(request.user, task)
        messages.success(request, f"Task #{task.id} has been archived.")
    except PermissionDenied as e:
        messages.error(request, str(e))

    return redirect('task_list')


@login_required
@require_POST
def task_restore_view(request, pk):
    task = get_object_or_404(Task, pk=pk)
    try:
        TaskService.restore_task(request.user, task)
        messages.success(request, f"Task #{task.id} has been restored to active workflow.")
    except PermissionDenied as e:
        messages.error(request, str(e))

    return redirect('task_detail', pk=task.pk)


# --- Task Attachments Views ---

@login_required
@require_POST
def task_attachment_upload_view(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if not can_edit_task(request.user, task):
        raise PermissionDenied("You do not have permission to attach files to this task.")

    files = request.FILES.getlist('file')
    if not files:
        messages.error(request, "No file was selected for upload.")
        return redirect('task_detail', pk=task.pk)

    uploaded_count = 0
    for f in files:
        try:
            AttachmentService.add_attachment(request.user, task, f)
            uploaded_count += 1
        except ValidationError as ve:
            messages.error(request, f"Failed to upload '{f.name}': {ve}")

    if uploaded_count > 0:
        messages.success(request, f"Successfully uploaded {uploaded_count} file(s).")

    return redirect('task_detail', pk=task.pk)


@login_required
def task_attachment_download_view(request, task_pk, attachment_pk):
    task = get_object_or_404(Task, pk=task_pk)
    if not can_view_task(request.user, task):
        raise PermissionDenied("You do not have permission to view or download files for this task.")

    attachment = get_object_or_404(TaskAttachment, pk=attachment_pk, task=task)
    if not attachment.file:
        raise Http404("Attachment file not found on disk.")

    return FileResponse(attachment.file.open('rb'), as_attachment=True, filename=attachment.filename)


@login_required
@require_POST
def task_attachment_delete_view(request, task_pk, attachment_pk):
    task = get_object_or_404(Task, pk=task_pk)
    try:
        AttachmentService.delete_attachment(request.user, attachment_pk)
        messages.success(request, "Attachment was removed.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect('task_detail', pk=task.pk)


# --- Teams Views ---

@login_required
def team_list_view(request):
    teams = get_accessible_teams_qs(request.user).prefetch_related('memberships__user')
    user_roles = {
        m.team_id: m.get_role_display()
        for m in TeamMembership.objects.filter(user=request.user)
    }

    teams_data = []
    for team in teams:
        teams_data.append({
            'team': team,
            'role': user_roles.get(team.id, 'Admin' if request.user.is_superuser else 'Member'),
            'member_count': team.member_count(),
            'active_tasks': team.active_tasks_count(),
            'open_tasks': team.open_tasks_count(),
            'overdue_tasks': team.overdue_tasks_count(),
        })

    context = {
        'title': 'Teams',
        'teams_data': teams_data,
    }
    return render(request, 'teams/list.html', context)


@login_required
def team_detail_view(request, pk):
    team = get_object_or_404(Team, pk=pk)
    if not can_view_team(request.user, team):
        raise PermissionDenied("You do not have permission to view this team.")

    memberships = team.memberships.select_related('user').order_by('role', 'user__username')
    tasks = team.tasks.filter(is_archived=False).select_related('assignee')[:10]
    recent_activity = team.activity_events.select_related('actor', 'task')[:10]

    user_role = get_user_team_role(request.user, team)
    is_admin = can_manage_team(request.user, team)

    add_member_form = AddTeamMemberForm() if is_admin else None
    invitation_form = WorkspaceInvitationForm() if is_admin else None
    invitations = team.invitations.select_related('inviter', 'invitee_user').order_by('-created_at') if is_admin else []

    context = {
        'title': f"Team: {team.name}",
        'team': team,
        'memberships': memberships,
        'tasks': tasks,
        'recent_activity': recent_activity,
        'user_role': user_role,
        'is_admin': is_admin,
        'add_member_form': add_member_form,
        'invitation_form': invitation_form,
        'invitations': invitations,
        'role_choices': TeamRole.choices,
    }
    return render(request, 'teams/detail.html', context)


@login_required
def team_create_view(request):
    form = TeamForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        team = TeamService.create_team(
            user=request.user,
            name=form.cleaned_data['name'],
            description=form.cleaned_data.get('description', '')
        )
        messages.success(request, f"Team '{team.name}' created successfully. You are designated as Team Admin.")
        return redirect('team_detail', pk=team.pk)

    context = {
        'title': 'Create New Team',
        'form': form,
    }
    return render(request, 'teams/create.html', context)


@login_required
def team_edit_view(request, pk):
    team = get_object_or_404(Team, pk=pk)
    if not can_manage_team(request.user, team):
        raise PermissionDenied("Only Team Admins can edit team settings.")

    form = TeamForm(request.POST or None, instance=team)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f"Team '{team.name}' details updated.")
        return redirect('team_detail', pk=team.pk)

    context = {
        'title': f"Edit Team: {team.name}",
        'team': team,
        'form': form,
    }
    return render(request, 'teams/edit.html', context)


@login_required
@require_POST
def team_add_member_view(request, pk):
    team = get_object_or_404(Team, pk=pk)
    form = AddTeamMemberForm(request.POST)

    if form.is_valid():
        user_to_add = form.cleaned_data['username_or_email']
        role = form.cleaned_data['role']
        try:
            TeamService.add_member(
                actor=request.user,
                team=team,
                user_to_add=user_to_add,
                role=role
            )
            messages.success(request, f"Added {user_to_add.username} to '{team.name}' as {TeamRole(role).label}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)

    return redirect('team_detail', pk=team.pk)


@login_required
@require_POST
def team_change_role_view(request, pk, membership_id):
    team = get_object_or_404(Team, pk=pk)
    membership = get_object_or_404(TeamMembership, pk=membership_id, team=team)
    new_role = request.POST.get('role')

    if not new_role or new_role not in TeamRole.values:
        messages.error(request, "Invalid role selection.")
        return redirect('team_detail', pk=team.pk)

    try:
        TeamService.update_member_role(request.user, membership, new_role)
        messages.success(request, f"Updated role for {membership.user.username} to {TeamRole(new_role).label}.")
    except PermissionDenied as e:
        messages.error(request, str(e))

    return redirect('team_detail', pk=team.pk)


@login_required
@require_POST
def team_remove_member_view(request, pk, membership_id):
    team = get_object_or_404(Team, pk=pk)
    membership = get_object_or_404(TeamMembership, pk=membership_id, team=team)

    try:
        TeamService.remove_member(request.user, membership)
        messages.success(request, f"Removed {membership.user.username} from team.")
    except PermissionDenied as e:
        messages.error(request, str(e))

    return redirect('team_detail', pk=team.pk)


# --- Workspace Invitations Views ---

@login_required
@require_POST
def invitation_create_view(request, pk):
    team = get_object_or_404(Team, pk=pk)
    form = WorkspaceInvitationForm(request.POST)

    if form.is_valid():
        email = form.cleaned_data['email']
        role = form.cleaned_data['role']
        try:
            InvitationService.create_invitation(
                inviter=request.user,
                team=team,
                invitee_email=email,
                role=role
            )
            messages.success(request, f"Invitation sent to '{email}' for role {TeamRole(role).label}.")
        except (ValidationError, PermissionDenied) as e:
            messages.error(request, str(e))
    else:
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)

    return redirect('team_detail', pk=team.pk)


@login_required
@require_POST
def invitation_accept_view(request, pk):
    try:
        membership = InvitationService.accept_invitation(request.user, pk)
        messages.success(request, f"You have successfully joined '{membership.team.name}' as {membership.get_role_display()}!")
        return redirect('team_detail', pk=membership.team.pk)
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))
        return redirect('dashboard')


@login_required
@require_POST
def invitation_decline_view(request, pk):
    try:
        InvitationService.decline_invitation(request.user, pk)
        messages.info(request, "Workspace invitation was declined.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect('dashboard')


@login_required
@require_POST
def invitation_revoke_view(request, pk):
    invitation = get_object_or_404(WorkspaceInvitation, pk=pk)
    team_pk = invitation.team_id
    try:
        InvitationService.revoke_invitation(request.user, pk)
        messages.success(request, f"Invitation to '{invitation.invitee_email}' has been revoked.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e))

    return redirect('team_detail', pk=team_pk)


# --- Updates Center Views ---

@login_required
def updates_center_view(request):
    # Auto sync upcoming and overdue deadlines
    NotificationService.check_and_create_deadline_alerts(request.user)

    filter_type = request.GET.get('filter', 'all')
    notifications_qs = InAppNotification.objects.filter(
        recipient=request.user
    ).select_related('actor', 'task', 'team', 'invitation').order_by('-created_at')

    if filter_type == 'unread':
        notifications_qs = notifications_qs.filter(is_read=False)
    elif filter_type == 'deadlines':
        notifications_qs = notifications_qs.filter(category=NotificationCategory.DEADLINE)
    elif filter_type == 'activity':
        notifications_qs = notifications_qs.filter(category__in=[
            NotificationCategory.ASSIGNMENT,
            NotificationCategory.STATUS,
            NotificationCategory.INVITATION
        ])

    paginator = Paginator(notifications_qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    unread_count = InAppNotification.objects.filter(recipient=request.user, is_read=False).count()
    deadlines_count = InAppNotification.objects.filter(recipient=request.user, category=NotificationCategory.DEADLINE, is_read=False).count()

    context = {
        'title': 'Updates Center',
        'page_obj': page_obj,
        'notifications': page_obj.object_list,
        'filter_type': filter_type,
        'unread_count': unread_count,
        'deadlines_count': deadlines_count,
    }
    return render(request, 'updates/index.html', context)


@login_required
@require_POST
def notification_mark_read_view(request, pk):
    NotificationService.mark_as_read(request.user, pk)
    next_url = request.POST.get('next') or request.GET.get('next') or reverse_url_or_fallback(request, 'updates_center')
    return redirect(next_url)


@login_required
@require_POST
def notification_mark_all_read_view(request):
    updated = NotificationService.mark_all_as_read(request.user)
    messages.success(request, f"Marked {updated} notification(s) as read.")
    return redirect('updates_center')


def reverse_url_or_fallback(request, fallback_name):
    from django.urls import reverse
    referer = request.META.get('HTTP_REFERER')
    return referer if referer else reverse(fallback_name)


# --- Activity History View ---

@login_required
def activity_list_view(request):
    accessible_teams = get_accessible_teams_qs(request.user)
    accessible_team_ids = list(accessible_teams.values_list('id', flat=True))

    events_qs = ActivityEvent.objects.filter(
        team_id__in=accessible_team_ids
    ).select_related('actor', 'team', 'task').order_by('-created_at')

    # Filters
    team_id = request.GET.get('team')
    event_type = request.GET.get('event_type')
    actor_id = request.GET.get('actor')

    if team_id and team_id.isdigit():
        events_qs = events_qs.filter(team_id=team_id)
    if event_type and event_type in EventType.values:
        events_qs = events_qs.filter(event_type=event_type)
    if actor_id and actor_id.isdigit():
        events_qs = events_qs.filter(actor_id=actor_id)

    paginator = Paginator(events_qs, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'title': 'Activity History',
        'page_obj': page_obj,
        'events': page_obj.object_list,
        'accessible_teams': accessible_teams,
        'event_types': EventType.choices,
        'selected_team_id': team_id,
        'selected_event_type': event_type,
    }
    return render(request, 'activity/list.html', context)


# --- User Profile View ---

@login_required
def profile_view(request):
    memberships = TeamMembership.objects.filter(
        user=request.user
    ).select_related('team')
    assigned_tasks_count = Task.objects.filter(
        assignee=request.user,
        is_archived=False
    ).exclude(status=TaskStatus.RESOLVED).count()

    context = {
        'title': 'My Profile & Workspace Memberships',
        'memberships': memberships,
        'assigned_tasks_count': assigned_tasks_count,
    }
    return render(request, 'profile/index.html', context)


# --- Helper API Endpoint for Frontend Dynamic Filter ---

@login_required
def api_team_members_view(request, team_id):
    """
    Returns JSON array of team members for dynamic dropdown updates on task creation.
    Protected: user must belong to the team.
    """
    team = get_object_or_404(Team, pk=team_id)
    if not can_view_team(request.user, team):
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    members = User.objects.filter(
        team_memberships__team=team
    ).values('id', 'username', 'first_name', 'last_name').distinct()

    data = [
        {
            'id': m['id'],
            'username': m['username'],
            'full_name': f"{m['first_name']} {m['last_name']}".strip() or m['username']
        }
        for m in members
    ]
    return JsonResponse({'members': data})
