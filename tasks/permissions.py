from django.db.models import Q
from .models import Team, TeamMembership, TeamRole, Task


def is_system_admin(user):
    return user.is_authenticated and (user.is_superuser or user.is_staff)


def get_user_team_membership(user, team):
    """
    Returns the TeamMembership instance for the user in the specified team, or None.
    """
    if not user.is_authenticated:
        return None
    return TeamMembership.objects.filter(user=user, team=team).first()


def get_user_team_role(user, team):
    """
    Returns the role ('ADMIN', 'MANAGER', 'MEMBER') of the user in team, or None.
    Superusers are treated as ADMIN.
    """
    if not user.is_authenticated:
        return None
    if is_system_admin(user):
        return TeamRole.ADMIN
    membership = get_user_team_membership(user, team)
    return membership.role if membership else None


def can_view_team(user, team):
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    return TeamMembership.objects.filter(user=user, team=team).exists()


def can_manage_team(user, team):
    """
    Only Team Admins or Superusers can update team settings or manage members.
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    return get_user_team_role(user, team) == TeamRole.ADMIN


def can_create_task(user, team):
    """
    Any authenticated member of the team (or superuser) can create a task.
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    return TeamMembership.objects.filter(user=user, team=team).exists()


def can_view_task(user, task):
    """
    Task can be viewed if the user belongs to the task's team.
    """
    return can_view_team(user, task.team)


def can_edit_task(user, task):
    """
    Admins and Managers can edit any task in their team.
    Members can edit tasks they created or tasks assigned to them.
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    role = get_user_team_role(user, task.team)
    if role in [TeamRole.ADMIN, TeamRole.MANAGER]:
        return True
    if role == TeamRole.MEMBER:
        return task.creator_id == user.id or task.assignee_id == user.id
    return False


def can_change_task_status(user, task):
    """
    Admins and Managers can change status on any task in their team.
    Members can change status on tasks assigned to them or created by them.
    """
    return can_edit_task(user, task)


def can_archive_task(user, task):
    """
    Only Admins and Managers can archive/restore tasks.
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    role = get_user_team_role(user, task.team)
    return role in [TeamRole.ADMIN, TeamRole.MANAGER]


def can_delete_task(user, task):
    """
    Only Team Admins or Superusers can permanently delete tasks.
    (Archiving is the preferred workflow).
    """
    if not user.is_authenticated:
        return False
    if is_system_admin(user):
        return True
    return get_user_team_role(user, task.team) == TeamRole.ADMIN


def can_change_member_role(actor, target_membership, new_role):
    """
    Determines if actor can change target_membership's role.
    Prevents self-demoting the last admin.
    """
    if is_system_admin(actor):
        return True
    actor_role = get_user_team_role(actor, target_membership.team)
    if actor_role != TeamRole.ADMIN:
        return False
    # If modifying self and was ADMIN: check if another admin exists
    if target_membership.user_id == actor.id and new_role != TeamRole.ADMIN:
        admin_count = TeamMembership.objects.filter(
            team=target_membership.team,
            role=TeamRole.ADMIN
        ).count()
        if admin_count <= 1:
            return False
    return True


def can_remove_member(actor, target_membership):
    """
    Determines if actor can remove target_membership from team.
    """
    if is_system_admin(actor):
        return True
    actor_role = get_user_team_role(actor, target_membership.team)
    if actor_role != TeamRole.ADMIN:
        # A user can leave the team themselves if they are not the sole admin
        if target_membership.user_id == actor.id:
            if target_membership.role == TeamRole.ADMIN:
                admin_count = TeamMembership.objects.filter(
                    team=target_membership.team,
                    role=TeamRole.ADMIN
                ).count()
                return admin_count > 1
            return True
        return False
    # Admin removing someone:
    if target_membership.role == TeamRole.ADMIN:
        admin_count = TeamMembership.objects.filter(
            team=target_membership.team,
            role=TeamRole.ADMIN
        ).count()
        if admin_count <= 1:
            return False
    return True


# Queryset Scoping Functions (Anti-IDOR / Data Isolation)

def get_accessible_teams_qs(user):
    """
    Returns only teams where the user has an active membership (or all teams for superuser).
    """
    if not user.is_authenticated:
        return Team.objects.none()
    if is_system_admin(user):
        return Team.objects.all()
    return Team.objects.filter(memberships__user=user).distinct()


def get_accessible_tasks_qs(user, include_archived=False):
    """
    Returns only tasks belonging to teams the user is a member of.
    """
    if not user.is_authenticated:
        return Task.objects.none()
    qs = Task.objects.select_related('team', 'assignee', 'creator')
    if not is_system_admin(user):
        qs = qs.filter(team__memberships__user=user).distinct()
    if not include_archived:
        qs = qs.filter(is_archived=False)
    return qs
