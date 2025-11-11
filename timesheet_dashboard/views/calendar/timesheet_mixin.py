import calendar
import math
from datetime import datetime, timedelta, date

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect
from django.urls.base import reverse

from edc_base.utils import get_utcnow

from timesheet.forms import DailyEntryForm
from timesheet.forms.monthly_entry_form import DailyEntryFormSet


class MonthlyEntryError(Exception):
    pass


class TimesheetMixin:

    daily_entry_model = 'timesheet.dailyentry'

    monthly_entry_model = 'timesheet.monthlyentry'

    ALLOW_FUTURE_MONTHS = getattr(
        settings, "TIMESHEET_ALLOW_FUTURE_MONTHS", False)

    @property
    def daily_entry_model_cls(self):
        return django_apps.get_model(self.daily_entry_model)

    @property
    def monthly_entry_model_cls(self):
        return django_apps.get_model(self.monthly_entry_model)

    def _qs_prefix(self, request, drop=("ym", )):
        """
            Return '?a=1&b=2' (or '' if none), preserving current
            request.GET, optionally dropping keys like 'ym' used by
            the month picker.
        """
        qs = request.GET.copy()
        for k in drop:
            if k in qs:
                del qs[k]
        s = qs.urlencode()
        return f"?{s}" if s else ""

    def _url_with_qs(self, base_url, request, drop=("ym", )):
        return base_url + self._qs_prefix(request, drop=drop)

    def canonical_redirect_if_picker(
            self, request, employee_id, curr_year, curr_month):
        """
            If user submitted ?ym=YYYY-MM via the month picker,
            normalize to /<year>/<month>/.
        """
        ym = request.GET.get("ym")
        if ym:
            try:
                dt = datetime.strptime(ym, '%Y-%m')
                year, month = dt.year, dt.month
                if curr_year != year or curr_month != month:
                    return redirect(
                        reverse(
                            'timesheet_dashboard:timesheet_calendar_table_url',
                            kwargs={'employee_id': employee_id,
                                    'year': year,
                                    'month': month}))
            except ValueError:
                messages.error(request, "Invalid month format.")
        return None

    def _is_supervisor(self, user):
        is_supervisor = user.groups.filter(name='Supervisor').exists()
        return (is_supervisor and self._is_employee_supervisor(user))

    def _is_employee_supervisor(self, user):
        supervisor_email = self.employee.supervisor.email
        return user.email == supervisor_email

    def _is_hr(self, user):
        return user.groups.filter(name='HR').exists()

    def _reviewer(self, user):
        in_group = user.groups.filter(
            name__in=['Supervisor', 'HR']).exists()

        return in_group

    def _target_employee(self, request):
        """ Return employee instance for request user if not
            reviewer otherwise, return supervisee/employee (HR)
            instance that is being reviewed.
        """
        employee_id = self.kwargs.get('employee_id')
        if employee_id and self._reviewer(request.user):
            if self.employee:
                return self.employee

        return self.user_employee

    def _change_calendar_mode(self, request):
        target_employee = self._target_employee(request)
        is_owner = (target_employee.id == self.user_employee.id)
        is_reviewer = (not is_owner) and self._reviewer(request.user)

        return is_owner, is_reviewer

    def get_user_credentials(self, user):
        first_name = getattr(user, 'first_name', '')
        last_name = getattr(user, 'last_name', '')
        if first_name and last_name:
            return f'{first_name[0]}. {last_name}'
        return ''

    def is_future_month(self, year, month, today=None):
        today = today or get_utcnow().date()
        return (year, month) > (today.year, today.month)

    def add_months(self, year, month, delta):
        idx = (year * 12 + (month - 1)) + delta
        new_year, new_month_idx = divmod(idx, 12)
        return new_year, new_month_idx + 1

    def get_current_contract(self, employee_id):

        contract_cls = django_apps.get_model('bhp_personnel.contract')
        try:
            current_contract = contract_cls.objects.get(identifier=employee_id,
                                                        status='Active')
        except contract_cls.DoesNotExist:
            pass
        else:
            return current_contract

    def calculate_monthly_overtime(self, dailyentries, monthly_entry):
        base_time_obj = 8
        holiday_entry_duration = 8
        weekend_entry_duration = None

        weekday_entries = dailyentries.filter(
            Q(day__week_day__lt=7) & Q(day__week_day__gt=1) & Q(entry_type='RH'))

        if self.is_nightwatch:
            base_time_obj = 12

        extra_hours = 0

        for entry in weekday_entries:
            if entry.duration > base_time_obj:
                difference = entry.duration - base_time_obj
                extra_hours += difference

        weekend_entries = dailyentries.filter(
            Q(day__week_day__gte=6) & Q(day__week_day__lte=7) & Q(
                entry_type='WE'))

        holiday_entries = dailyentries.filter(
            Q(entry_type='H'))

        if self.is_nightwatch:
            weekend_entries = dailyentries.filter(
                Q(day__week_day__gte=6) & Q(day__week_day__lte=7)
                | Q(entry_type='H'))

        for weekend_entry in weekend_entries:
            if self.is_nightwatch:
                difference = weekend_entry.duration - base_time_obj
                if difference > 0:
                    extra_hours += difference
            else:
                difference = weekend_entry.duration - weekend_entry_duration
                if difference > 0:
                    extra_hours += difference

        for holiday_entry in holiday_entries:
            difference = holiday_entry.duration - holiday_entry_duration
            if difference > 0:
                extra_hours += difference

        monthly_entry.monthly_overtime = str(extra_hours)

        return monthly_entry

    def get_holidays(self, year, month):
        facility_app_config = django_apps.get_app_config('edc_facility')

        facility = facility_app_config.get_facility('5-day clinic')

        holiday_list = facility.holidays.holidays.filter(
            local_date__year=year,
            local_date__month=month).values_list('local_date', flat=True)
        if not self.is_nightwatch:
            return '|'.join([f'{h.year}/{h.month}/{h.day}' for h in holiday_list])
        else:
            return ''

    def month_day_list(self, year: int, month: int):
        _, last_day = calendar.monthrange(year, month)
        return [date(year, month, day) for day in range(1, last_day + 1)]

    def construct_month_dt(self):
        year = self.kwargs.get('year', '')
        month = self.kwargs.get('month', '')
        return datetime.strptime(f'{year}-{month}-1', '%Y-%m-%d')

    def get_or_create_monthly_obj(self):
        """
            Get existing monthly object or create a draft instance
        """
        dt = self.construct_month_dt()
        monthly_obj, _created = self.monthly_entry_model_cls.objects.get_or_create(
            employee=self.employee,
            month=dt,
            defaults={'status': 'draft'})

        self.ensure_daily_placeholders(monthly_obj)
        return monthly_obj, _created

    def get_monthly_obj(self):
        dt = self.construct_month_dt()
        try:
            monthly_obj = self.monthly_entry_model_cls.objects.get(
                employee=self.employee,
                month=dt)
        except self.monthly_entry_model_cls.DoesNotExist:
            return None
        else:
            return monthly_obj

    def ensure_daily_placeholders(self, monthly_entry):
        """
            Creates any missing DailyEntry rows (no duplicates, idempotent).
        """
        existing_dates = set(
            monthly_entry.daily_entries.values_list('day', flat=True))
        month_day_list = self.month_day_list(
            monthly_entry.month.year, monthly_entry.month.month)
        for _day in month_day_list:
            if _day not in existing_dates:
                # Create empty rows up-front so inline formset is straightforward
                self.daily_entry_model_cls.objects.create(
                    monthly_entry=monthly_entry,
                    day=_day,
                    duration=0)

    def get_formset(self, instance):
        strict = (self.request.method == 'POST' and 'submit' in self.request.POST)
        if self.request.method == 'POST':
            formset = DailyEntryFormSet(
                self.request.POST, instance=instance)
        else:
            formset = DailyEntryFormSet(instance=instance)

        for f in formset.forms:
            f.strict = strict
        return formset

    def get_number_of_weeks(self, year, month):
        return len(calendar.monthcalendar(year, month))

    def get_weekdays(self, currDate=None):
        dates = [(currDate + timedelta(days=i)) for i in range(
            0 - currDate.weekday(), 7 - currDate.weekday())]
        return dates

    @property
    def monthly_entry_cls(self):
        return django_apps.get_model('timesheet.monthlyentry')

    def get_dailyentries(self, year, month):
        entries_dict = {}
        try:
            monthly_entry_obj = self.monthly_entry_cls.objects.get(
                employee=self.employee,
                month=datetime.strptime(f'{year}-{month}-1', '%Y-%m-%d'))
        except self.monthly_entry_cls.DoesNotExist:
            return None
        else:
            daily_entries = monthly_entry_obj.dailyentry_set.all()
            blank_days = self.get_blank_days(int(year), int(month))

            if daily_entries:
                daily_entries = daily_entries.order_by('day')
                rows = math.ceil((daily_entries.count() + blank_days) / 7)
                entries_dict = {}
                for i in range(rows):
                    entries_dict[i] = list(daily_entries.filter(row=i))
        return entries_dict

    def get_blank_days(self, year, month):
        calendar_days = self.calendar_obj.monthdayscalendar(year, month)

        blank_days = 0

        for i in calendar_days[0]:
            if i == 0:
                blank_days += 1
        return blank_days

    @property
    def employee(self):
        employee_cls = django_apps.get_model('bhp_personnel.employee')

        try:
            employee_obj = employee_cls.objects.get(
                identifier=self.kwargs.get('employee_id'))
        except employee_cls.DoesNotExist:
            return None
        return employee_obj

    @property
    def user_employee(self):
        employee_cls = django_apps.get_model('bhp_personnel.employee')

        try:
            employee_obj = employee_cls.objects.get(
                email=self.request.user.email)
        except employee_cls.DoesNotExist:
            return None
        return employee_obj

    def entry_types(self):
        daily_entry_cls = django_apps.get_model('timesheet.dailyentry')
        entry_types = daily_entry_cls._meta.get_field('entry_type').choices

        return entry_types

    @property
    def is_nightwatch(self):
        """
        - returns : True if the employee is a security guard
        """
        if self.user_employee:
            return 'night' in self.user_employee.job_title.lower()
        return False

    def monthly_obj_job_title(self, monthly_obj):
        return monthly_obj.employee.job_title
