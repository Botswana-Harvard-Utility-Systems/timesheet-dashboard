import calendar
from edc_base.utils import get_utcnow
from edc_base.view_mixins import EdcBaseViewMixin
from edc_dashboard.view_mixins import TemplateRequestContextMixin

from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect
from django.urls.base import reverse
from django.utils.decorators import method_decorator
from django.views.generic.base import TemplateView

from edc_navbar import NavbarViewMixin

from timesheet.forms import MonthlyEntryForm
from timesheet.forms.monthly_entry_form import DailyEntryFormSet

from .timesheet_mixin import TimesheetMixin


class CalendarViewError(Exception):
    pass


class CalendarView(TimesheetMixin, NavbarViewMixin, EdcBaseViewMixin,
                   TemplateRequestContextMixin, TemplateView):
    template_name = 'timesheet_dashboard/calendar/calendar_table.html'
    model = 'timesheet.monthlyentry'
    navbar_name = 'timesheet'
    navbar_selected_item = 'employee_timesheet'
    success_url = 'timesheet_dashboard:timesheet_calendar_table_url'
    calendar_obj = calendar.Calendar(firstweekday=0)
    daily_entry_cls = django_apps.get_model('timesheet.dailyentry')

    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def get(self, request, *args, **kwargs):
        year = int(kwargs.get('year'))
        month = int(kwargs.get('month'))
        employee_id = kwargs.get('employee_id')
        redirect_url = self.canonical_redirect_if_picker(
            request, employee_id, year, month)

        if redirect_url:
            return redirect_url

        if self.is_future_month(year, month) and not self.ALLOW_FUTURE_MONTHS:
            messages.info(
                request,
                'Future months are disabled. Showing current month instead.')
            today = get_utcnow().date()
            return redirect(
                reverse('timesheet_dashboard:timesheet_calendar_table_url',
                        kwargs={'employee_id': employee_id,
                                'year': today.year,
                                'month': today.month}))

        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        year = int(self.kwargs.get('year'))
        month = int(self.kwargs.get('month'))
        employee_id = self.kwargs.get('employee_id')

        # Existing monthly if any (no creation on GET)
        monthly_entry = kwargs.get('monthly_entry')
        if monthly_entry is None:
            monthly_entry = self.monthly_entry_model_cls.objects.filter(
                employee=self.employee, month=self.construct_month_dt()).first()

        if monthly_entry and not kwargs.get('formset'):
            self.ensure_daily_placeholders(monthly_entry)
            formset = DailyEntryFormSet(instance=monthly_entry)
        else:
            formset = kwargs.get('formset')
            if monthly_entry and formset is None:
                formset = DailyEntryFormSet(instance=monthly_entry)

        # Build Mon->Sun calendar weeks as date objects (including spillover days)
        cal = calendar.Calendar(firstweekday=0)
        weeks = list(cal.monthdatescalendar(year, month))

        # Map each in-month date to its form (if monthly entry exists)
        forms_by_date = {}
        if monthly_entry and formset:
            for f in formset.forms:
                forms_by_date[f.instance.day] = f

        # Produce rows for the template: only keep dates that belong to this month, else None
        calendar_rows = []
        for week in weeks:
            row = []
            for d in week:
                if d.month == month:
                    row.append({'date': d, 'form': forms_by_date.get(d)})
                else:
                    row.append({'date': None, 'form': None})
            calendar_rows.append(row)

        weekday_headers = calendar.day_abbr

        # Navigation helpers
        prev_year, prev_month = self.add_months(year, month, -1)
        next_year, next_month = self.add_months(year, month, +1)
        prev_url = reverse('timesheet_dashboard:timesheet_calendar_table_url',
                           kwargs={'employee_id': employee_id,
                                   'year': prev_year,
                                   'month': prev_month})
        next_url = reverse('timesheet_dashboard:timesheet_calendar_table_url',
                           kwargs={'employee_id': employee_id,
                                   'year': next_year,
                                   'month': next_month})

        context.update({
            'year': year,
            'month': month,
            'prev_url': prev_url,
            'next_url': next_url,
            'month_days': self.month_day_list(year, month),
            'monthly_entry': monthly_entry,
            'formset': formset,
            'calendar_rows': calendar_rows,
            'weekday_headers': weekday_headers,
            'can_create_for_month': self.ALLOW_FUTURE_MONTHS or not self.is_future_month(year, month)})

        # If POST injected form/formset/strict, keep them; otherwise defaults for GET
        context.setdefault('strict', False)
        if monthly_entry:
            context.setdefault('form', MonthlyEntryForm(instance=monthly_entry))
            context.setdefault('formset', DailyEntryFormSet(instance=monthly_entry))
        else:
            context.setdefault('form', MonthlyEntryForm())
            context.setdefault('formset', None)

        # Add extra context
        context.update(self._build_extra_context())
        return context

    @transaction.atomic
    def post(self, request, *args, **kwargs):
        # if this is a POST request we need to process the form data
        year = int(kwargs.get('year'))
        month = int(kwargs.get('month'))
        employee_id = kwargs.get('employee_id')

        is_hr = self._is_hr(request.user)
        is_supervisor = self._is_supervisor(request.user)

        base_url = self._url_with_qs(
            reverse('timesheet_dashboard:timesheet_calendar_table_url',
                    kwargs={'employee_id': employee_id,
                            'year': year,
                            'month': month}),
            request)

        if 'start' in request.POST:
            if self.is_future_month(year, month) and not self.ALLOW_FUTURE_MONTHS:
                messages.error(
                    request,
                    'Cannot create or submit timesheets for future months.')
                return redirect(base_url)

            # Get or create only when user explicitly posts
            _mothly_entry, _ = self.get_or_create_monthly_obj()
            messages.success(
                request,
                'Timesheet created. You can now enter your days.')
            return redirect(base_url)

        monthly_entry = self.get_monthly_obj()

        if not monthly_entry:
            # We do not expect to get to this point if monthly entry does
            # not exist. Save/submit/approve/reject only appears for user
            # when monthly entry exists.
            messages.error(
                request,
                'Something went wrong. Please contact your administrator')
            return redirect(base_url)

        # Review actions
        action = request.POST.get('review_action')
        final_message = None
        if action:
            # Only allow HR to retract timesheets once they are on their final state.
            if monthly_entry.is_final and not is_hr:
                messages.error(
                    request,
                    'This timesheet is already verified and cannot be modified further.')
                return redirect(base_url)

            prev_status = monthly_entry.status
            if action == 'retract':
                if not is_hr:
                    messages.error(
                        request,
                        'You are not allowed to retract verification.')
                    return redirect(base_url)
                if prev_status != 'verified':
                    messages.error(
                        request,
                        'You can only retract a verified timesheet')
                    return redirect(base_url)
                monthly_entry.status = 'approved'
                monthly_entry.verified_by = None
                monthly_entry.verified_date = None
                final_message = 'Verification retracted. Status is now "Approved".'
            elif action == 'approve':
                if not is_supervisor:
                    messages.error(
                        request,
                        'You are not allowed to approve.')
                    return redirect(base_url)

                if prev_status not in ('submitted', ):
                    messages.error(
                        request,
                        'Only submitted timesheets can be approved.')
                    return redirect(base_url)
                monthly_entry.status = 'approved'
                monthly_entry.approved_by = self.get_user_credentials(request.user)
                monthly_entry.approved_date = get_utcnow().date()
            elif action == 'verify':
                if not is_hr:
                    messages.error(
                        request,
                        'You are not allowed to verify.')
                    return redirect(base_url)
                if prev_status not in ('approved', ):
                    messages.error(
                        request,
                        'Only approved timesheets can be approved.')
                    return redirect(base_url)
                monthly_entry.status = 'verified'
                monthly_entry.verified_by = self.get_user_credentials(request.user)
                monthly_entry.verified_date = get_utcnow().date()
            elif action == 'reject':
                if not self._reviewer(request.user):
                    messages.error(
                        request,
                        'You are not allowed to reject.')
                    return redirect(base_url)
                if prev_status not in ('submitted', 'approved'):
                    messages.error(
                        request,
                        'Only submitted or approved timesheets can be rejected.')
                    return redirect(base_url)
                monthly_entry.status = 'rejected'
                monthly_entry.rejected_by = self.get_user_credentials(request.user)
                monthly_entry.rejected_date = get_utcnow().date()
            else:
                messages.error(request, 'Unknown review action')
                return redirect(base_url)
            monthly_entry.comment = request.POST.get('review_comment', '').strip()
            monthly_entry.save(
                update_fields=['status', 'approved_by', 'approved_date',
                               'verified_by', 'verified_date', 'rejected_by',
                               'rejected_date', 'comment'])
            messages.success(
                request,
                f'Timesheet {final_message or monthly_entry.status.lower()}')
            return redirect(base_url)

        strict = ('submit' in request.POST)

        # Defensive guard — if management fields missing, treat as a start/open
        probe_prefix = DailyEntryFormSet(instance=monthly_entry).prefix
        management_fields = (f"{probe_prefix}-TOTAL_FORMS",
                             f"{probe_prefix}-INITIAL_FORMS")
        if not all(k in request.POST for k in management_fields):
            messages.info(
                request,
                'No changes submitted. Timesheet is open for editing.')
            return redirect(base_url)

        form = MonthlyEntryForm(request.POST, instance=monthly_entry)
        formset = self.get_formset(instance=monthly_entry)

        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            if strict:
                monthly_entry.status = 'submitted'
                monthly_entry.submitted_datetime = get_utcnow()
                monthly_entry.save(
                    update_fields=['status', 'submitted_datetime'])
                messages.success(
                    request,
                    'Timesheet submitted to Supervisor for review')
            else:
                messages.success(
                    request,
                    'Timesheet saved as draft')
            return redirect(base_url)
        else:
            # Re-render with errors using the same context pipeline
            messages.error(
                request,
                'Please correct errors on highlighted dates and try again.')
            context = self.get_context_data(
                monthly_entry=monthly_entry,
                form=form,
                formset=formset,
                strict=strict)

            return self.render_to_response(context)

    def _build_extra_context(self):
        extra_context = {}

        year = int(self.kwargs.get('year'))
        month = int(self.kwargs.get('month'))
        employee_id = self.kwargs.get('employee_id')

        monthly_obj = self.get_monthly_obj()

        is_owner, is_reviewer = self._change_calendar_mode(self.request)
        is_hr = self._is_hr(self.request.user)
        is_supervisor = self._is_supervisor(self.request.user)
        entry_status = getattr(monthly_obj, 'status', None)

        readonly_status = ['approved', 'verified']
        readonly = is_reviewer or (
            is_owner and entry_status in readonly_status)

        allow_edit = is_owner and entry_status not in readonly_status
        allow_hr_review = not is_owner and is_hr and entry_status in ['approved']
        allow_sv_review = not is_owner and is_supervisor and entry_status in ['submitted']
        allow_reject = (
            not is_owner and (is_supervisor or is_hr) and entry_status in ('submitted', 'approved'))
        allow_retract = not is_owner and is_hr and entry_status == 'verified'

        extra_context.update({'p_role': self.request.GET.get('p_role', None),
                              'is_owner': is_owner,
                              'allow_edit': allow_edit,
                              'allow_hr_review': allow_hr_review,
                              'allow_sv_review': allow_sv_review,
                              'allow_reject': allow_reject,
                              'allow_retract': allow_retract,
                              'is_reviewer': is_reviewer,
                              'readonly': readonly})

        if monthly_obj:
            leave_balance = None
            if self.get_current_contract(employee_id):
                leave_balance = self.get_current_contract(
                    employee_id).leave_balance

            monthly_obj_job_title = self.monthly_obj_job_title(monthly_obj)

            extra_context.update(
                leave_taken=monthly_obj.annual_leave_taken,
                leave_balance=leave_balance,
                overtime_worked=monthly_obj.monthly_overtime,
                comment=monthly_obj.comment,
                timesheet_status=monthly_obj.get_status_display(),
                timesheet_status_badge=monthly_obj.status_badge_color,
                verified_by=monthly_obj.verified_by,
                approved_by=monthly_obj.approved_by,
                submitted_datetime=monthly_obj.submitted_datetime,
                rejected_by=monthly_obj.rejected_by,
                monthly_obj_job_title=monthly_obj_job_title,
            )

        month_name = calendar.month_name[month]

        groups = [g.name for g in self.request.user.groups.all()]

        entry_types = self.entry_types()

        context = dict(employee_id=employee_id,
                       month_name=month_name,
                       groups=groups,
                       user_employee=self.user_employee,
                       holidays=self.get_holidays(year, month),
                       entry_types=entry_types,
                       month_names=list(calendar.month_name)[1:13],
                       is_nightwatch=self.is_nightwatch,
                       **extra_context)
        return context

    def filter_options(self, **kwargs):
        options = super().filter_options(**kwargs)
        if kwargs.get('employee_id'):
            options.update(
                {'employee_id': kwargs.get('employee_id')})
        return options

    @property
    def pdf_template(self):
        return self.get_template_from_context(self.calendar_template)
