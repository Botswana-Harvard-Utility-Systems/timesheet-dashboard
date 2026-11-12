from datetime import datetime
from functools import cached_property

from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from edc_base.utils import get_utcnow
from edc_base.view_mixins import EdcBaseViewMixin
from edc_dashboard.view_mixins import ListboardFilterViewMixin, SearchFormViewMixin
from edc_dashboard.views import ListboardView as BaseListboardView
from edc_navbar import NavbarViewMixin
from timesheet.choices import STATUS

from ..access import is_hr, is_supervisor, visible_employees
from ..model_wrappers import MonthlyEntryModelWrapper
from .filters import ListboardViewFilters


class ListboardView(EdcBaseViewMixin, NavbarViewMixin,
                    ListboardFilterViewMixin, SearchFormViewMixin, BaseListboardView):
    listboard_template = 'timesheet_listboard_template'
    listboard_url = 'timesheet_listboard_url'
    listboard_panel_style = 'success'
    listboard_fa_icon = 'fa fa-list-alt'
    model = 'timesheet.monthlyentry'
    model_wrapper_cls = MonthlyEntryModelWrapper
    listboard_view_filters = ListboardViewFilters()
    navbar_name = 'timesheet'
    navbar_selected_item = 'employee_timesheet'
    search_form_url = 'timesheet_listboard_url'

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        if self.request.GET.get('status') and self.request.GET['status'] not in dict(STATUS):
            raise Http404('Unknown timesheet status.')
        # Evaluate permissions before rendering either a list or a POST response.
        self.review_role
        if self.selected_employee_id and not visible_employees(self.request.user).filter(
                identifier=self.selected_employee_id).exists():
            raise PermissionDenied('You cannot view timesheets for this employee.')
        return super().dispatch(*args, **kwargs)

    @cached_property
    def own_employee(self):
        return apps.get_model('bhp_personnel.employee').objects.filter(
            email__iexact=self.request.user.email).first()

    @property
    def selected_employee_id(self):
        value = self.kwargs.get('employee_id') or self.request.GET.get('employee_id')
        return None if value in (None, '', 'None', 'null') else value

    @cached_property
    def review_role(self):
        requested = self.request.GET.get('p_role')
        user = self.request.user
        if requested == 'HR':
            if not is_hr(user):
                raise PermissionDenied('HR access is required.')
            return 'HR'
        if requested == 'Supervisor':
            if not is_supervisor(user):
                raise PermissionDenied('Supervisor access is required.')
            return 'Supervisor'
        if self.selected_employee_id and (
                not self.own_employee or self.selected_employee_id != self.own_employee.identifier):
            if is_hr(user):
                return 'HR'
            if is_supervisor(user):
                return 'Supervisor'
        return ''

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.review_role:
            employees = visible_employees(
                self.request.user, team_only=self.review_role == 'Supervisor')
        else:
            employees = visible_employees(self.request.user).filter(
                pk=self.own_employee.pk) if self.own_employee else visible_employees(self.request.user).none()
        queryset = queryset.filter(employee__in=employees)
        if self.selected_employee_id:
            queryset = queryset.filter(employee__identifier=self.selected_employee_id)
        status = self.request.GET.get('status')
        if status:
            queryset = queryset.filter(status=status)
        if self.request.GET.get('dept'):
            queryset = queryset.filter(employee__department__dept_name=self.request.GET['dept'])
        return queryset.select_related('employee', 'employee__supervisor').order_by('-month', 'employee__last_name', 'pk')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        employee_id = self.selected_employee_id
        if not self.review_role and self.own_employee:
            employee_id = self.own_employee.identifier
        now = get_utcnow()
        can_add = bool(self.own_employee and employee_id == self.own_employee.identifier and not self.review_role)
        add_url = reverse('timesheet_dashboard:timesheet_calendar_table_url',
                          args=[employee_id, now.year, now.month]) if can_add else None
        selected_employee = visible_employees(self.request.user).filter(identifier=employee_id).first() if employee_id else None
        title = 'All Employee Timesheets' if self.review_role == 'HR' else (
            'Team Timesheets' if self.review_role == 'Supervisor' else 'My Timesheets')
        context.update(
            p_role=self.review_role, timesheet_title=title, employee_id=employee_id,
            selected_employee=selected_employee, timesheet_add_url=add_url,
            selected_status=self.request.GET.get('status', ''), status_choices=STATUS,
            selected_department=self.request.GET.get('dept', ''),
            selected_search=self.request.GET.get('q', ''),
            departments=visible_employees(self.request.user).order_by('department__dept_name').values_list(
                'department__dept_name', flat=True).distinct(),
            groups=list(self.request.user.groups.values_list('name', flat=True)),
            querystring=('?' + self.request.GET.urlencode()) if self.request.GET else '')
        return context

    def post(self, request, *args, **kwargs):
        if not self.own_employee or self.selected_employee_id != self.own_employee.identifier:
            raise PermissionDenied('You can only submit your own timesheet.')
        try:
            month = datetime.strptime(f"{request.GET.get('year')}-{request.GET.get('month')}-1", '%Y-%m-%d').date()
        except ValueError:
            return HttpResponseBadRequest('Choose a valid month and year.')
        entry = self.model_cls.objects.filter(employee=self.own_employee, month=month).first()
        if entry and entry.status in ('draft', 'new', 'rejected'):
            entry.status = 'submitted'
            entry.submitted_datetime = get_utcnow()
            entry.save()
        suffix = '?' + request.GET.urlencode() if request.GET else ''
        return HttpResponseRedirect(request.path + suffix)

    def extra_search_options(self, search_term):
        return (Q(employee__first_name__icontains=search_term)
                | Q(employee__last_name__icontains=search_term)
                | Q(employee__email__icontains=search_term))
