from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # Authentication
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),

    # Password Recovery (Using Django's built-in reset views with console backend)
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='auth/password_reset.html',
        email_template_name='auth/password_reset_email.html',
        subject_template_name='auth/password_reset_subject.txt',
        success_url='/password-reset/done/'
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='auth/password_reset_done.html'
    ), name='password_reset_done'),
    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='auth/password_reset_confirm.html',
        success_url='/password-reset-complete/'
    ), name='password_reset_confirm'),
    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='auth/password_reset_complete.html'
    ), name='password_reset_complete'),

    # Dashboard
    path('', views.dashboard_view, name='dashboard'),

    # Tasks
    path('tasks/', views.task_list_view, name='task_list'),
    path('tasks/new/', views.task_create_view, name='task_create'),
    path('tasks/<int:pk>/', views.task_detail_view, name='task_detail'),
    path('tasks/<int:pk>/edit/', views.task_edit_view, name='task_edit'),
    path('tasks/<int:pk>/transition/', views.task_transition_view, name='task_transition'),
    path('tasks/<int:pk>/archive/', views.task_archive_view, name='task_archive'),
    path('tasks/<int:pk>/restore/', views.task_restore_view, name='task_restore'),

    # Task Attachments
    path('tasks/<int:pk>/attachments/upload/', views.task_attachment_upload_view, name='task_attachment_upload'),
    path('tasks/<int:task_pk>/attachments/<int:attachment_pk>/download/', views.task_attachment_download_view, name='task_attachment_download'),
    path('tasks/<int:task_pk>/attachments/<int:attachment_pk>/delete/', views.task_attachment_delete_view, name='task_attachment_delete'),

    # Teams
    path('teams/', views.team_list_view, name='team_list'),
    path('teams/new/', views.team_create_view, name='team_create'),
    path('teams/<int:pk>/', views.team_detail_view, name='team_detail'),
    path('teams/<int:pk>/edit/', views.team_edit_view, name='team_edit'),
    path('teams/<int:pk>/members/add/', views.team_add_member_view, name='team_add_member'),
    path('teams/<int:pk>/members/<int:membership_id>/role/', views.team_change_role_view, name='team_change_role'),
    path('teams/<int:pk>/members/<int:membership_id>/remove/', views.team_remove_member_view, name='team_remove_member'),

    # Workspace Invitations
    path('teams/<int:pk>/invitations/create/', views.invitation_create_view, name='invitation_create'),
    path('invitations/<int:pk>/accept/', views.invitation_accept_view, name='invitation_accept'),
    path('invitations/<int:pk>/decline/', views.invitation_decline_view, name='invitation_decline'),
    path('invitations/<int:pk>/revoke/', views.invitation_revoke_view, name='invitation_revoke'),

    # Updates Center
    path('updates/', views.updates_center_view, name='updates_center'),
    path('updates/<int:pk>/read/', views.notification_mark_read_view, name='notification_mark_read'),
    path('updates/read-all/', views.notification_mark_all_read_view, name='notification_mark_all_read'),

    # Activity History
    path('activity/', views.activity_list_view, name='activity_list'),

    # Profile & Settings
    path('profile/', views.profile_view, name='profile'),

    # Helper API for dynamic form dropdowns
    path('api/teams/<int:team_id>/members/', views.api_team_members_view, name='api_team_members'),
]
