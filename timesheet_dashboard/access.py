"""Timesheet visibility is based on account roles and reporting relationships."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Q, Exists, OuterRef


def is_hr(user):
    return user.is_superuser or user.groups.filter(name='HR').exists()


def is_supervisor(user):
    return user.groups.filter(name='Supervisor').exists()


def visible_employees(user, team_only=False):
    active_accounts = get_user_model().objects.filter(
        email__iexact=OuterRef('email'), is_active=True)
    employees = apps.get_model('bhp_personnel.employee').objects.annotate(
        has_active_account=Exists(active_accounts)).filter(has_active_account=True)
    if team_only:
        if not is_supervisor(user):
            return employees.none()
        return employees.filter(supervisor__email__iexact=user.email)
    if is_hr(user):
        return employees
    own = Q(email__iexact=user.email)
    if is_supervisor(user):
        return employees.filter(own | Q(supervisor__email__iexact=user.email))
    return employees.filter(own)
