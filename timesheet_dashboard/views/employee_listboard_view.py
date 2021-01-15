import re
# from django.apps import apps as django_apps
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.utils.decorators import method_decorator
from edc_base.view_mixins import EdcBaseViewMixin
from edc_dashboard.view_mixins import ListboardFilterViewMixin, SearchFormViewMixin
from edc_dashboard.views import ListboardView
from edc_navbar import NavbarViewMixin

from bhp_personnel_dashboard.model_wrappers import EmployeeModelWrapper


class EmployeeListBoardView(
        NavbarViewMixin, EdcBaseViewMixin, ListboardFilterViewMixin,
        SearchFormViewMixin, ListboardView):

    listboard_template = 'timesheet_employee_listboard_template'
    listboard_url = 'timesheet_employee_listboard_url'
    listboard_panel_style = 'info'
    listboard_fa_icon = "fa-user-plus"

    model = 'bhp_personnel.employee'
    model_wrapper_cls = EmployeeModelWrapper
    navbar_name = 'timesheet'
    navbar_selected_item = 'timesheet_listboard'
    ordering = '-modified'
    paginate_by = 10
    search_form_url = 'timesheet_employee_listboard_url'

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        p_role = self.request.GET.get('p_role')
        context.update(
            p_role=p_role,
            groups=[g.name for g in self.request.user.groups.all()],
            employee_add_url=self.model_cls().get_absolute_url())
        return context

    def get_queryset_filter_options(self, request, *args, **kwargs):
        options = super().get_queryset_filter_options(request, *args, **kwargs)
        if kwargs.get('subject_id'):
            options.update(
                {'identifier': kwargs.get('subject_id')})
        if self.request.GET.get('dept'):
            options.update(
                {'department': kwargs.get('dept')})
        return options


    def extra_search_options(self, search_term):
        q = Q()
        if re.match('^[A-Z]+$', search_term):
            q = Q(first_name__exact=search_term)
        return q