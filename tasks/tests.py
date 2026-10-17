from datetime import timedelta
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError, transaction

from tasks.models import (
    Team,
    TeamMembership,
    TeamRole,
    Task,
    TaskStatus,
    TaskPriority,
    ActivityEvent,
    EventType,
    IdempotencyRecord,
)
from tasks.services import (
    TaskService,
    TeamService,
    DashboardService,
    StaleUpdateError,
    InvalidWorkflowTransitionError,
)
from tasks.permissions import (
    can_view_task,
    can_edit_task,
    can_change_task_status,
    can_manage_team,
    can_change_member_role,
    can_remove_member,
    get_accessible_tasks_qs,
)


class TaskTrackerBaseTestCase(TestCase):
    def setUp(self):
        # Create users
        self.alice = User.objects.create_user(username='alice', password='Password123!', email='alice@test.local')
        self.bob = User.objects.create_user(username='bob', password='Password123!', email='bob@test.local')
        self.charlie = User.objects.create_user(username='charlie', password='Password123!', email='charlie@test.local')
        self.eve = User.objects.create_user(username='eve', password='Password123!', email='eve@test.local') # External user with no team access

        # Create teams
        self.team_alpha = Team.objects.create(name='Alpha Team', description='Alpha Team Description')
        self.team_beta = Team.objects.create(name='Beta Team', description='Beta Team Description')

        # Set up memberships
        # Alice is ADMIN in Alpha
        self.m_alice_alpha = TeamMembership.objects.create(user=self.alice, team=self.team_alpha, role=TeamRole.ADMIN)
        # Bob is MANAGER in Alpha
        self.m_bob_alpha = TeamMembership.objects.create(user=self.bob, team=self.team_alpha, role=TeamRole.MANAGER)
        # Charlie is MEMBER in Alpha, and ADMIN in Beta
        self.m_charlie_alpha = TeamMembership.objects.create(user=self.charlie, team=self.team_alpha, role=TeamRole.MEMBER)
        self.m_charlie_beta = TeamMembership.objects.create(user=self.charlie, team=self.team_beta, role=TeamRole.ADMIN)

        # Clients for web testing
        self.client_alice = Client()
        self.client_alice.login(username='alice', password='Password123!')

        self.client_bob = Client()
        self.client_bob.login(username='bob', password='Password123!')

        self.client_charlie = Client()
        self.client_charlie.login(username='charlie', password='Password123!')

        self.client_eve = Client()
        self.client_eve.login(username='eve', password='Password123!')


class AuthenticationAndSecurityTests(TaskTrackerBaseTestCase):
    def test_unauthenticated_user_redirected_to_login(self):
        anon_client = Client()
        response = anon_client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_login_success_and_session(self):
        c = Client()
        response = c.post(reverse('login'), {'username': 'alice', 'password': 'Password123!'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

    def test_login_invalid_credentials_does_not_leak_user_existence(self):
        c = Client()
        response = c.post(reverse('login'), {'username': 'non_existent_user', 'password': 'WrongPassword!'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid username or password")

    def test_registration_creates_user_and_default_workspace(self):
        c = Client()
        response = c.post(reverse('register'), {
            'username': 'newuser',
            'first_name': 'New',
            'last_name': 'User',
            'email': 'newuser@test.local',
            'password': 'StrongPassword123!',
            'confirm_password': 'StrongPassword123!',
        })
        self.assertEqual(response.status_code, 302)
        u = User.objects.get(username='newuser')
        self.assertTrue(u.is_active)
        # Check automatic workspace provisioning
        membership = TeamMembership.objects.filter(user=u, role=TeamRole.ADMIN).first()
        self.assertIsNotNone(membership)
        self.assertIn("Newuser's Team", membership.team.name)


class TeamAuthorizationAndIsolationTests(TaskTrackerBaseTestCase):
    def test_cross_team_data_isolation(self):
        # Create task in Beta team
        beta_task = TaskService.create_task(
            user=self.charlie,
            team=self.team_beta,
            title="Secret Beta Task",
            priority=TaskPriority.HIGH
        )

        # Alice belongs only to Alpha team. Alice should NOT see beta_task in querysets
        alice_tasks = get_accessible_tasks_qs(self.alice)
        self.assertNotIn(beta_task, alice_tasks)

        # Alice attempting to access Beta task via direct URL (IDOR test)
        response = self.client_alice.get(reverse('task_detail', kwargs={'pk': beta_task.pk}))
        self.assertEqual(response.status_code, 403)

    def test_team_membership_uniqueness_constraint(self):
        with self.assertRaises(IntegrityError):
            TeamMembership.objects.create(user=self.alice, team=self.team_alpha, role=TeamRole.MEMBER)

    def test_assignee_must_belong_to_team(self):
        # Eve is NOT a member of Alpha team
        with self.assertRaises(ValidationError):
            TaskService.create_task(
                user=self.alice,
                team=self.team_alpha,
                title="Invalid Assignee Task",
                assignee=self.eve
            )


class TaskCRUDAndValidationTests(TaskTrackerBaseTestCase):
    def test_create_task_lifecycle_and_audit(self):
        task = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Database Sharding Strategy",
            description="Investigate horizontal partitioning.",
            priority=TaskPriority.HIGH,
            status=TaskStatus.OPEN,
            assignee=self.bob,
            due_date=timezone.localdate() + timedelta(days=5)
        )
        self.assertEqual(task.version, 1)
        self.assertEqual(task.status, TaskStatus.OPEN)

        # Verify audit activity event was created atomically
        event = ActivityEvent.objects.filter(task=task, event_type=EventType.TASK_CREATED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.alice)
        self.assertEqual(event.team, self.team_alpha)

    def test_new_task_cannot_start_in_resolved_status(self):
        with self.assertRaises(ValidationError):
            TaskService.create_task(
                user=self.alice,
                team=self.team_alpha,
                title="Immediate Resolved Task",
                status=TaskStatus.RESOLVED
            )

    def test_member_permission_boundaries(self):
        # Alice (Admin) creates task
        task = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Core Refactoring",
            assignee=self.bob
        )
        # Charlie is MEMBER in team_alpha and is NOT creator nor assignee
        self.assertFalse(can_edit_task(self.charlie, task))

        # Assign Charlie to the task
        task.assignee = self.charlie
        task.save()
        self.assertTrue(can_edit_task(self.charlie, task))


class WorkflowTransitionTests(TaskTrackerBaseTestCase):
    def setUp(self):
        super().setUp()
        self.task = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Workflow Validation Test Task",
            status=TaskStatus.OPEN
        )

    def test_valid_status_flow(self):
        # OPEN -> IN_PROGRESS
        TaskService.transition_status(self.alice, self.task, TaskStatus.IN_PROGRESS)
        self.assertEqual(self.task.status, TaskStatus.IN_PROGRESS)
        self.assertEqual(self.task.version, 2)

        # IN_PROGRESS -> RESOLVED
        TaskService.transition_status(self.alice, self.task, TaskStatus.RESOLVED)
        self.assertEqual(self.task.status, TaskStatus.RESOLVED)
        self.assertEqual(self.task.version, 3)

        # RESOLVED -> OPEN (Reopen)
        TaskService.transition_status(self.alice, self.task, TaskStatus.OPEN)
        self.assertEqual(self.task.status, TaskStatus.OPEN)
        self.assertEqual(self.task.version, 4)

        # Verify audit events recorded
        events = ActivityEvent.objects.filter(task=self.task).order_by('created_at')
        event_types = [e.event_type for e in events]
        self.assertIn(EventType.TASK_CREATED, event_types)
        self.assertIn(EventType.STATUS_CHANGED, event_types)
        self.assertIn(EventType.TASK_RESOLVED, event_types)
        self.assertIn(EventType.TASK_REOPENED, event_types)

    def test_invalid_status_transition_rejected(self):
        # Mark as resolved
        TaskService.transition_status(self.alice, self.task, TaskStatus.RESOLVED)
        self.assertEqual(self.task.status, TaskStatus.RESOLVED)

        # Cannot jump directly from RESOLVED to IN_PROGRESS
        with self.assertRaises(InvalidWorkflowTransitionError):
            TaskService.transition_status(self.alice, self.task, TaskStatus.IN_PROGRESS)


class OptimisticConcurrencyStaleUpdateTests(TaskTrackerBaseTestCase):
    def test_stale_update_protection_raises_error(self):
        task = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Initial Title"
        )

        # User A loads version 1
        user_a_version = task.version

        # User B updates task in between (incrementing version to 2)
        TaskService.update_task(
            user=self.bob,
            task=task,
            title="User B Title Edit",
            description="",
            priority=task.priority,
            status=task.status,
            assignee=None,
            due_date=None,
            submitted_version=1
        )
        self.assertEqual(task.version, 2)

        # User A tries to save their edit using the stale version 1
        with self.assertRaises(StaleUpdateError):
            TaskService.update_task(
                user=self.alice,
                task=task,
                title="User A Overwrite Attempt",
                description="",
                priority=task.priority,
                status=task.status,
                assignee=None,
                due_date=None,
                submitted_version=user_a_version # Stale!
            )

        # Verify the database was NOT overwritten
        task.refresh_from_db()
        self.assertEqual(task.title, "User B Title Edit")
        self.assertEqual(task.version, 2)


class IdempotencyDuplicateSubmissionTests(TaskTrackerBaseTestCase):
    def test_idempotent_task_creation_prevents_duplicate(self):
        key = "idemp-unique-token-12345"

        # First request
        task1 = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Idempotent Task",
            idempotency_key=key
        )

        # Retry request with SAME idempotency key
        task2 = TaskService.create_task(
            user=self.alice,
            team=self.team_alpha,
            title="Idempotent Task (Retried)",
            idempotency_key=key
        )

        # Must return the SAME task without creating a duplicate
        self.assertEqual(task1.id, task2.id)
        self.assertEqual(Task.objects.filter(team=self.team_alpha, title__contains="Idempotent").count(), 1)


class RoleEscalationAndMembershipTests(TaskTrackerBaseTestCase):
    def test_prevent_demoting_sole_admin(self):
        # Alice is the only ADMIN in Alpha team
        # Alice tries to change her own role to MEMBER
        can_demote = can_change_member_role(self.alice, self.m_alice_alpha, TeamRole.MEMBER)
        self.assertFalse(can_demote)

    def test_member_cannot_manage_team_or_add_members(self):
        # Charlie is MEMBER in Alpha team
        self.assertFalse(can_manage_team(self.charlie, self.team_alpha))
        with self.assertRaises(PermissionDenied):
            TeamService.add_member(self.charlie, self.team_alpha, self.eve, TeamRole.MEMBER)


class DashboardMetricsTests(TaskTrackerBaseTestCase):
    def test_dashboard_aggregates_correctly(self):
        today = timezone.localdate()
        # 1 Open task
        TaskService.create_task(user=self.alice, team=self.team_alpha, title="T1", status=TaskStatus.OPEN)
        # 1 In Progress task assigned to Alice
        TaskService.create_task(user=self.alice, team=self.team_alpha, title="T2", status=TaskStatus.IN_PROGRESS, assignee=self.alice)
        # 1 Overdue task
        Task.objects.create(
            title="T3 Overdue",
            team=self.team_alpha,
            creator=self.alice,
            due_date=today - timedelta(days=2),
            status=TaskStatus.OPEN
        )

        data = DashboardService.get_dashboard_data(self.alice)
        self.assertEqual(data['total_active'], 3)
        self.assertEqual(data['open_count'], 2)
        self.assertEqual(data['in_progress_count'], 1)
        self.assertEqual(data['overdue_count'], 1)
        self.assertEqual(data['my_tasks_count'], 1)
