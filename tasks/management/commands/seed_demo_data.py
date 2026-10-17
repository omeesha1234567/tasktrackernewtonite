from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from tasks.models import Team, TeamMembership, TeamRole, Task, TaskStatus, TaskPriority, ActivityEvent, EventType
from tasks.services import TaskService, TeamService


class Command(BaseCommand):
    help = "Seeds database with demo teams, realistic users (Admin, Manager, Member), tasks, and audit logs."

    def handle(self, *args, **options):
        self.stdout.write("Seeding demo data...")

        # Create or update superuser
        admin_user, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@newtonite.local',
                'first_name': 'System',
                'last_name': 'Admin',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        admin_user.set_password('AdminPass123!')
        admin_user.save()

        # Create demo users
        users_def = [
            ('alice', 'alice@newtonite.local', 'Alice', 'Morgan', 'Password123!'),
            ('bob', 'bob@newtonite.local', 'Bob', 'Chen', 'Password123!'),
            ('charlie', 'charlie@newtonite.local', 'Charlie', 'Kim', 'Password123!'),
            ('diana', 'diana@newtonite.local', 'Diana', 'Prince', 'Password123!'),
            ('edward', 'edward@newtonite.local', 'Edward', 'Norton', 'Password123!'),
        ]
        users = {}
        for uname, email, fname, lname, pwd in users_def:
            u, _ = User.objects.get_or_create(
                username=uname,
                defaults={'email': email, 'first_name': fname, 'last_name': lname}
            )
            u.set_password(pwd)
            u.save()
            users[uname] = u

        # Create Teams
        core_team, _ = Team.objects.get_or_create(
            name="Core Platform",
            defaults={"description": "Backend architecture, distributed database engineering, APIs, and infrastructure."}
        )
        frontend_team, _ = Team.objects.get_or_create(
            name="Frontend Engineering",
            defaults={"description": "Design systems, accessible interfaces, React/Django templates, and UX polish."}
        )
        mobile_team, _ = Team.objects.get_or_create(
            name="Mobile Apps",
            defaults={"description": "iOS and Android native client applications and offline syncing."}
        )

        # Memberships
        memberships = [
            (users['alice'], core_team, TeamRole.ADMIN),
            (users['bob'], core_team, TeamRole.MANAGER),
            (users['charlie'], core_team, TeamRole.MEMBER),
            (users['diana'], frontend_team, TeamRole.ADMIN),
            (users['bob'], frontend_team, TeamRole.MEMBER),
            (users['edward'], frontend_team, TeamRole.MANAGER),
            (users['charlie'], mobile_team, TeamRole.ADMIN),
        ]

        for u, t, r in memberships:
            m, created = TeamMembership.objects.get_or_create(
                user=u, team=t,
                defaults={'role': r}
            )
            if not created and m.role != r:
                m.role = r
                m.save()

        # Seed Tasks
        today = timezone.localdate()
        tasks_def = [
            # Core Team Tasks
            (
                "Implement Optimistic Concurrency Control on Tasks",
                "Ensure conditional version checks are performed atomically on the DB level to prevent lost updates.",
                core_team, TaskPriority.URGENT, TaskStatus.IN_PROGRESS, users['alice'], users['bob'], today + timedelta(days=2)
            ),
            (
                "Fix Database Connection Pool Leak Under Load",
                "Connections remain open when unhandled exceptions occur in worker threads.",
                core_team, TaskPriority.HIGH, TaskStatus.OPEN, users['alice'], users['charlie'], today - timedelta(days=2)  # Overdue
            ),
            (
                "Design Idempotency Middleware for Create Endpoints",
                "Persist UUID keys in IdempotencyRecord with unique constraint on user + key + action.",
                core_team, TaskPriority.HIGH, TaskStatus.RESOLVED, users['bob'], users['alice'], today - timedelta(days=5)
            ),
            (
                "Migrate SQLite Schema to Support Composite Indexes",
                "Add multi-column indexes on (team, status, priority) to accelerate dashboard aggregations.",
                core_team, TaskPriority.MEDIUM, TaskStatus.OPEN, users['charlie'], None, today + timedelta(days=7)  # Unassigned
            ),
            (
                "Conduct Security Audit of Object-Level Authorization",
                "Verify no Insecure Direct Object References (IDOR) exist in team scoping logic.",
                core_team, TaskPriority.URGENT, TaskStatus.IN_PROGRESS, users['alice'], users['alice'], today - timedelta(days=1)  # Overdue
            ),
            # Frontend Team Tasks
            (
                "Implement Clean SaaS Design System and Responsive Shell",
                "Typography with Inter, calm neutral slate palette, responsive mobile collapsible drawer.",
                frontend_team, TaskPriority.HIGH, TaskStatus.RESOLVED, users['diana'], users['diana'], today - timedelta(days=1)
            ),
            (
                "Add Dynamic Team Member Filtering in Task Creation",
                "Fetch members asynchronously when team changes to prevent illegal cross-team assignments.",
                frontend_team, TaskPriority.MEDIUM, TaskStatus.IN_PROGRESS, users['diana'], users['bob'], today + timedelta(days=3)
            ),
            (
                "Design Stale Update Conflict Modal and Diff View",
                "When a 409 conflict is received, show current DB state alongside user submitted changes.",
                frontend_team, TaskPriority.HIGH, TaskStatus.OPEN, users['edward'], users['edward'], today + timedelta(days=5)
            ),
            (
                "Audit Accessibility Contrast and Keyboard Navigation",
                "Ensure WCAG AA contrast ratio across all badge states and form inputs.",
                frontend_team, TaskPriority.LOW, TaskStatus.OPEN, users['diana'], None, today + timedelta(days=10)
            ),
            # Mobile App Tasks
            (
                "Define Offline Sync Protocol for Task Tracker",
                "Buffer state changes locally in SQLite and reconcile timestamps during reconnect.",
                mobile_team, TaskPriority.HIGH, TaskStatus.OPEN, users['charlie'], users['charlie'], today + timedelta(days=4)
            ),
        ]

        for title, desc, team, prio, status, creator, assignee, due in tasks_def:
            task, created = Task.objects.get_or_create(
                title=title,
                team=team,
                defaults={
                    'description': desc,
                    'priority': prio,
                    'status': status,
                    'creator': creator,
                    'assignee': assignee,
                    'due_date': due,
                    'version': 1
                }
            )
            if created:
                ActivityEvent.objects.create(
                    actor=creator,
                    team=team,
                    task=task,
                    event_type=EventType.TASK_CREATED,
                    details=f"Task '{task.title}' initialized with priority {task.get_priority_display()}.",
                    new_state={'status': task.status, 'priority': task.priority}
                )

        self.stdout.write(self.style.SUCCESS("Demo data seeded successfully!"))
        self.stdout.write("Test Credentials:")
        self.stdout.write("  - Admin user:    'alice' / 'Password123!' (Admin @ Core Platform)")
        self.stdout.write("  - Manager user:  'bob'   / 'Password123!' (Manager @ Core Platform, Member @ Frontend)")
        self.stdout.write("  - Member user:   'charlie' / 'Password123!' (Member @ Core Platform, Admin @ Mobile)")
        self.stdout.write("  - Superuser:     'admin' / 'AdminPass123!'")
