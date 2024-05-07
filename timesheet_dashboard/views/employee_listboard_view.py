from bhp_personnel_dashboard.model_wrappers import EmployeeModelWrapper
from edc_base.view_mixins import EdcBaseViewMixin
from edc_dashboard.view_mixins import ListboardFilterViewMixin, SearchFormViewMixin
from edc_dashboard.views import ListboardView
import re

from django.apps import apps as django_apps
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.db.models.constants import LOOKUP_SEP
from django.utils.decorators import method_decorator

from edc_navbar import NavbarViewMixin

from .filters import EmployeeListboardViewFilters


class EmployeeListBoardView(
        NavbarViewMixin, EdcBaseViewMixin, ListboardFilterViewMixin,
        SearchFormViewMixin, ListboardView):

    # supervisor_queryset_lookups = []
    listboard_template = 'timesheet_employee_listboard_template'
    listboard_url = 'timesheet_employee_listboard_url'
    listboard_panel_style = 'info'
    listboard_fa_icon = "fa-user-plus"

    model = 'bhp_personnel.employee'
    model_wrapper_cls = EmployeeModelWrapper
    # listboard_view_filters = EmployeeListboardViewFilters()
    navbar_name = 'timesheet'
    navbar_selected_item = 'timesheet_listboard'
    ordering = '-modified'
    paginate_by = 10
    search_form_url = 'timesheet_employee_listboard_url'
    employee_model = 'bhp_personnel.employee'
    

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)
    
    @property
    def employee_cls(self):
        return django_apps.get_model(self.employee_model)


    @property
    def employee(self):
        try:
            employee_obj = self.employee_cls.objects.get(email=self.request.user.email)
        except self.employee_cls.DoesNotExist:
            return None
        else:
            return employee_obj
        
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        p_role = self.request.GET.get('p_role')
        context.update(
            p_role=p_role,
            departments=self.departments,
            groups=[g.name for g in self.request.user.groups.all()],
            employee_add_url=self.model_cls().get_absolute_url(),
            querystring=self.user_id,
            employee = self.employee)
        return context

    @property
    def user_id(self):

        employee_cls = django_apps.get_model('bhp_personnel.employee')
        pi_cls = django_apps.get_model('bhp_personnel.pi')
        consultant_cls = django_apps.get_model('bhp_personnel.consultant')

        employee = self.get_personnel_obj(employee_cls)

        p_role = self.request.GET.get('p_role')

        qs = f'?p_role={p_role}'
        if employee:
            return employee.identifier + qs
        else:
            pi = self.get_personnel_obj(pi_cls)

            if pi:
                return pi.identifier + qs
            else:
                consultant = self.get_personnel_obj(consultant_cls)

                return consultant.identifier if consultant else qs

    def get_personnel_obj(self, personnel_cls):

        try:
            personnel_obj = personnel_cls.objects.get(email=self.request.user.email)
        except personnel_cls.DoesNotExist:
            return None
        except personnel_cls.MultipleObjectsReturned:
            raise
        else:
            return personnel_obj

    # def supervisors(self, supervisor=None):
        # """Return a list of supervisors in the same highrachy.
        # """
        # employee_cls = django_apps.get_model('bhp_personnel.employee')
        # supervisors = [supervisor]
        # employees = employee_cls.objects.filter(supervisor=supervisor)
        # for employee in employees:
            # supervisor_cls = django_apps.get_model('bhp_personnel.supervisor')
            # try:
                # supervisor_obj = supervisor_cls.objects.get(email=employee.email)
            # except supervisor_cls.DoesNotExist:
                # pass
            # else:
                # supervisors.append(supervisor_obj)
        # return supervisors
        #
    # @property
    # def supervisor_lookup_prefix(self):
        # supervisor_lookup_prefix = LOOKUP_SEP.join(self.supervisor_queryset_lookups)
        # return f'{supervisor_lookup_prefix}__' if supervisor_lookup_prefix else ''

    def get_queryset_filter_options(self, request, *args, **kwargs):
        options = super().get_queryset_filter_options(request, *args, **kwargs)
        if kwargs.get('subject_id'):
            options.update(
                {'identifier': kwargs.get('subject_id')})
        return options

    def get_queryset(self):
        usr_groups = [g.name for g in self.request.user.groups.all()]
        qs = super().get_queryset()
        if self.request.GET.get('dept'):
            if 'HR' in usr_groups and self.request.GET.get('p_role') == 'HR':
                qs = qs.filter(department__dept_name=self.request.GET.get('dept'))
        elif('Supervisor' in usr_groups and self.request.GET.get('p_role') == 'Supervisor'):
            supervisor_cls = django_apps.get_model('bhp_personnel.supervisor')
            try:
                supervisor_obj = supervisor_cls.objects.get(email=self.request.user.email)
            except supervisor_cls.DoesNotExist:
                pass
            else:
                initial_supervisor_qs = qs.filter(Q(supervisor=supervisor_obj))  # | Q(supervisor_alt=supervisor_obj))
                supervisees_lists = initial_supervisor_qs.values_list('email',
                                                             flat=True).distinct()
                for email in supervisees_lists:
                    try:
                        supervisee_object = supervisor_cls.objects.get(email=email)
                    except supervisor_cls.DoesNotExist:
                        pass
                    else:
                        supervisee_sub_qs = qs.filter(Q(supervisor=supervisee_object))
                        initial_supervisor_qs = initial_supervisor_qs.union(supervisee_sub_qs)
                qs = initial_supervisor_qs
        return qs

    def extra_search_options(self, search_term):
        q = Q()
        if re.match('^[A-Z]+$', search_term):
            q = Q(first_name__exact=search_term)
        return q

    @property
    def departments(self):
        department_cls = django_apps.get_model('bhp_personnel.department')

        return [dept.dept_name for dept in department_cls.objects.all()]
