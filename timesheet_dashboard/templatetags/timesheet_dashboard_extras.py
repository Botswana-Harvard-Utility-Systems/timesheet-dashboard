from django import template
from datetime import datetime, date
import calendar
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.models import Group 

register = template.Library()


@register.filter
def month_name(month_number):
    return calendar.month_name[month_number]


@register.filter
def is_weekend(dt):
    if isinstance(dt, datetime):
        return dt.date().weekday() in [5, 6]
    elif isinstance(dt, date):
        return dt.weekday() in [5, 6]


@register.filter
def is_holiday(dt):

    facility_app_config = django_apps.get_app_config('edc_facility')

    facility = facility_app_config.get_facility('5-day clinic')

    holiday_list = facility.holidays.holidays.all().values_list('local_date', flat=True)

    if isinstance(dt, datetime):
        return dt.date() in holiday_list
    elif isinstance(dt, date):
        return dt in holiday_list


@register.inclusion_tag('timesheet_dashboard/buttons/submit_timesheet_button.html')
def submit_timesheet_button(model_wrapper):
    title = ['Submit Timesheet']
    return dict(
        employee_id=model_wrapper.object.employee.identifier,
        status=model_wrapper.object.status,
        month=model_wrapper.object.month.month,
        year=model_wrapper.object.month.year,
        title=' '.join(title))


@register.inclusion_tag('timesheet_dashboard/buttons/view_timesheet_button.html')
def view_timesheet_button(model_wrapper):
    title = ['View Timesheet']
    return dict(
        employee_id=model_wrapper.object.employee.identifier,
        status=model_wrapper.object.status,
        month=model_wrapper.object.month,
        read_only=True,
        title=' '.join(title))


@register.inclusion_tag('timesheet_dashboard/buttons/timesheets_button.html')
def timesheets_button(model_wrapper, p_role, user):
    title = ['View Employee Timesheets.']
    return dict(
        employee_id=model_wrapper.object.identifier,
        href=model_wrapper.href,
        timesheet_listboard_url=settings.DASHBOARD_URL_NAMES.get('timesheet_listboard_url'),
        groups=[g.name for g in user.groups.all()],
        p_role=p_role,
        title=' '.join(title))


@register.inclusion_tag('timesheet_dashboard/demographics.html')
def demographics(employee_id):
    title = ['View Employee Timesheets.']
    employee_cls = django_apps.get_model('bhp_personnel.employee')

    try:
        employee_obj = employee_cls.objects.get(identifier=employee_id)
    except employee_cls.DoesNotExist:
        consultant_cls = django_apps.get_model('bhp_personnel.consultant')
        try:
            employee_obj = consultant_cls.objects.get(identifier=employee_id)
        except consultant_cls.DoesNotExist:
            employee_obj = None

    if employee_obj:
        personnel_dict = {'job_title': 'Consultant'}
        if employee_obj._meta.label_lower == 'bhp_personnel.employee':
            personnel_dict['employee_code'] = employee_obj.employee_code
            personnel_dict['job_title'] = employee_obj.job_title

        return dict(
            supervisor=(employee_obj.supervisor.first_name + " " +
                        employee_obj.supervisor.last_name),
            first_name=employee_obj.first_name,
            last_name=employee_obj.last_name,
            initials=employee_obj.first_name[0] + employee_obj.last_name[0],
            title=' '.join(title),
            **personnel_dict)


@register.simple_tag(takes_context=True)
def departments(context):
    department_cls = django_apps.get_model('bhp_personnel.department')

    return [dept.dept_name for dept in department_cls.objects.all()]

@register.filter(name='has_group') 
def has_group(user, group_name):
    """
    Tag for checking permissions, it returns either False or True for a particular user
    """
    group = Group.objects.filter(name__iexact=group_name)
    if group:
        group = group.first()
        return group in user.groups.all()
    else:
        return False
