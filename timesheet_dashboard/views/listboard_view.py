from datetime import datetime, date
from edc_base.utils import get_utcnow
from edc_base.view_mixins import EdcBaseViewMixin
from edc_dashboard.view_mixins import ListboardFilterViewMixin, SearchFormViewMixin
from edc_dashboard.views import ListboardView
import re

from django.apps import apps as django_apps
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db.models.constants import LOOKUP_SEP
from django.http.response import HttpResponseRedirect
from django.utils.decorators import method_decorator

from edc_navbar import NavbarViewMixin

from ..model_wrappers import MonthlyEntryModelWrapper
from .filters import ListboardViewFilters


class ListboardView(EdcBaseViewMixin, NavbarViewMixin,
                    ListboardFilterViewMixin, SearchFormViewMixin,
                    ListboardView):

    supervisor_queryset_lookups = []
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
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.get_employee:
            model_obj = self.model_cls(employee=self.get_employee)
        else:
            model_obj = self.model_cls()

        groups = [g.name for g in self.request.user.groups.all()]
        timesheet_add_url = None

        if not bool(self.request.GET) or self.request.GET.get('p_role') not in groups:
            timesheet_add_url = self.model_wrapper_cls(model_obj=model_obj).href

        p_role = self.request.GET.get('p_role')

        employee_id = self.request.GET.get('employee_id') or self.kwargs.get('employee_id')
        querystring = f'?p_role={p_role}'
        dept = self.request.GET.get('dept')
        q = self.request.GET.get('q')

        if employee_id:
            querystring = querystring + f'&&employee_id={employee_id}'

        if dept:
            querystring = querystring + f'&&dept={dept}'

        if q:
            querystring = querystring + f'&&q={q}'

        context.update(
            p_role=p_role,
            groups=groups,
            departments=self.departments,
            employee_id=employee_id,
            employee=self.get_employee,
            supervisor_obj=self.supervisor_obj,
            timesheet_add_url=timesheet_add_url,
            curr_year=get_utcnow().year,
            curr_month=get_utcnow().month,
            querystring=querystring)
        return context

    def post(self, request, *args, **kwargs):
        if request.method == 'POST':
            month = self.request.GET.get('month')
            year = self.request.GET.get('year')
            try:
                monthly_entry_obj = self.model_cls.objects.get(
                    employee__identifier=self.kwargs['employee_id'],
                    month=datetime.strptime(f'{year}-{month}-1', '%Y-%m-%d'))
            except self.model_cls.DoesNotExist:
                pass
            else:
                monthly_entry_obj.status = 'submitted'
                monthly_entry_obj.submitted_datetime = get_utcnow()
                monthly_entry_obj.save()

        return HttpResponseRedirect(self.request.path)

    @property
    def get_employee(self):
        employee_cls = django_apps.get_model('bhp_personnel.employee')

        try:
            employee_obj = employee_cls.objects.get(email=self.request.user.email)
        except employee_cls.DoesNotExist:
            return None
        except employee_cls.MultipleObjectsReturned:
            return None
        else:
            return employee_obj

    @property
    def departments(self):
        department_cls = django_apps.get_model('bhp_personnel.department')

        return [dept.dept_name for dept in department_cls.objects.all()]

    @property
    def supervisor_cls(self):
        return django_apps.get_model('bhp_personnel.supervisor')

    @property
    def supervisor_obj(self):
        supervisor_obj = self.supervisor_cls.objects.filter(
            email=self.request.user.email).exists()
        return supervisor_obj

    @property
    def supervisor_lookup_prefix(self):
        supervisor_lookup_prefix = LOOKUP_SEP.join(self.supervisor_queryset_lookups)
        return f'{supervisor_lookup_prefix}__' if supervisor_lookup_prefix else ''

    def get_queryset_filter_options(self, request, *args, **kwargs):
        options = super().get_queryset_filter_options(request, *args, **kwargs)
        usr_groups = [g.name for g in self.request.user.groups.all()]

        if self.kwargs.get('employee_id') or self.request.GET.get('employee_id'):
            options.update({'employee__identifier': self.kwargs.get('employee_id')
                            or self.request.GET.get('employee_id')})

        elif ('Supervisor' in usr_groups and request.GET.get('p_role') == 'Supervisor'):

            try:
                supervisor_obj = self.supervisor_cls.objects.get(email=self.request.user.email)
            except self.supervisor_cls.DoesNotExist:
                options.update({'user_created': None})
            else:
                options.update(
                    {f'{self.supervisor_lookup_prefix}employee__supervisor': supervisor_obj})

        return options

    def get_queryset(self):
        qs = super().get_queryset()
        usr_groups = [g.name for g in self.request.user.groups.all()]
        if (('Supervisor' in usr_groups and self.request.GET.get('p_role')
             == 'Supervisor') or ('HR' in usr_groups and self.request.GET.get('p_role')
                                  == 'HR')):
            qs = qs.filter(
                status__in=['approved', 'verified', 'rejected', 'submitted'],
                month__gte=date(2022, 7, 1))

        if self.request.GET.get('dept'):
            usr_groups = [g.name for g in self.request.user.groups.all()]

            if 'HR' in usr_groups and self.request.GET.get('p_role') == 'HR':
                qs = qs.filter(employee__department__dept_name=self.request.GET.get('dept'))
        return qs.order_by('-month')

    def extra_search_options(self, search_term):
        q = Q()
        if re.match('^[A-Z]+$', search_term):
            q = Q(first_name__exact=search_term)
        return q
