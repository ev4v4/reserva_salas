from django.urls import path
from django.contrib.auth import views as auth_views
from .views import (
    home, profile_view, events_feed, availability, reserve_view,
    cancel_reservation,
    # Admin agenda
    admin_agenda, admin_events_feed, cancel_bulk,
    # Admin grade fixa
    admin_grade_view, admin_grade_create, admin_grade_update,
    admin_grade_toggle, admin_grade_delete,
    # Painel administrativo e home com avisos
    admin_panel, home_with_notices,   # ✅ ESSENCIAL: importa as duas novas views
)

urlpatterns = [

    # =============================
    # Painel administrativo
    # =============================
    path('painel/', admin_panel, name='admin_panel'),
    path('home/', home_with_notices, name='home_notices'),

    # ==============================
    # 🌐 Páginas principais
    # ==============================
    path("", home, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("profile/", profile_view, name="profile"),

    # ==============================
    # 🧩 API de reservas
    # ==============================
    path("api/events/", events_feed, name="events_feed"),
    path("api/availability/", availability, name="availability"),
    path("reserve/", reserve_view, name="reserve"),
    path("cancel/", cancel_reservation, name="cancel_reservation"),

    # ==============================
    # 🧑‍💼 Administração - Agenda
    # ==============================
    path("admin-agenda/", admin_agenda, name="admin_agenda"),
    path("api/admin-events/", admin_events_feed, name="admin_events_feed"),
    path("api/cancel-bulk/", cancel_bulk, name="cancel_bulk"),

    # ==============================
    # 🗓️ Administração - Grade fixa
    # ==============================
    path("admin-grade/", admin_grade_view, name="admin_grade"),
    path("admin-grade/create/", admin_grade_create, name="admin_grade_create"),
    path("admin-grade/update/", admin_grade_update, name="admin_grade_update"),
    path("admin-grade/toggle/", admin_grade_toggle, name="admin_grade_toggle"),
    path("admin-grade/delete/", admin_grade_delete, name="admin_grade_delete"),

    # ==============================
    # 🔐 Sistema de Reset de Senha
    # ==============================
    path(
        "password_reset/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.txt",
            html_email_template_name="registration/password_reset_email.html",
            subject_template_name="registration/password_reset_subject.txt",
            success_url="/password_reset/done/",
        ),
        name="password_reset",
    ),
    path(
        "password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="registration/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html"
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="registration/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
