import calendar
import math
from datetime import datetime, timedelta, date
from smtplib import SMTPException

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib import messages
from django.contrib.sites.shortcuts import get_current_site
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db.models import Q
from django.forms import inlineformset_factory
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

    def is_future_month(self, year, month, today=None):
        today = today or get_utcnow().date()
        return (year, month) > (today.year, today.month)

    def add_months(self, year, month, delta):
        idx = (year * 12 + (month - 1)) + delta
        new_year, new_month_idx = divmod(idx, 12)
        return new_year, new_month_idx + 1

    def navigate_table(self, controller, year, month):
        if controller == 'next':
            if month == '12':
                year = int(year) + 1
                month = 1
            else:
                month = int(month) + 1
        elif controller == 'prev':
            if month == '1':
                year = int(year) - 1
                month = 12
            else:
                month = int(month) - 1

        return year, month

    def clean_data(self, data, daily_entries_count, monthly_entry):
        """ Remove duplicate entries from the formset data """
        for i in range(daily_entries_count):
            index = str(i + 1)
            day = data.get(index + '-day')

            day_date = datetime.strptime(day, '%Y-%m-%d')
            try:
                daily_entry_obj = self.daily_entry_cls.objects.get(
                    day=day_date,
                    monthly_entry=monthly_entry)
            except self.daily_entry_cls.DoesNotExist:
                pass
            else:
                duration = data.get(index + '-duration')
                entry_type = data.get(index + '-entry_type')
                if (daily_entry_obj.duration != duration
                        or daily_entry_obj.entry_type != entry_type):
                    daily_entry_obj.duration = duration
                    daily_entry_obj.entry_type = entry_type
                    daily_entry_obj.save()

                data.pop(index + '-duration')
                data.pop(index + '-entry_type')
                for key in data.keys():
                    if (index + '-row') in key:
                        data.pop(key)
                        break
                data.pop(index + '-day')

    def add_daily_entries(self, request, *args, **kwargs):
        monthly_entry_cls = django_apps.get_model(self.model)
        data = request.POST.dict()
        year = self.kwargs.get('year', '')
        month = self.kwargs.get('month', '')
        daily_entries = None

        if request.POST.get('timesheet_review'):
            try:
                monthly_entry = monthly_entry_cls.objects.get(
                    employee=self.employee,
                    month=datetime.strptime(f'{year}-{month}-1', '%Y-%m-%d'))
            except monthly_entry_cls.DoesNotExist:
                raise MonthlyEntryError(
                    f"Missing timesheet being reviewed for {self.employee}",
                    f" for month starting {monthly_entry.month}.")
            else:
                if request.POST.get('comment').strip() != '':
                    monthly_entry.comment = request.POST.get('comment')
                else:
                    monthly_entry.comment = None
                prev_status = monthly_entry.status
                monthly_entry.status = request.POST.get('timesheet_review')

                if (prev_status != request.POST.get('timesheet_review')
                        and request.POST.get('timesheet_review') in
                        ['rejected', 'verified', 'approved']):
                    field_prefix = request.POST.get('timesheet_review')

                    setattr(monthly_entry, (field_prefix + '_date'),
                            get_utcnow().date())
                    setattr(monthly_entry, (field_prefix + '_by'), (
                            request.user.first_name[0] + ' ' + request.user.last_name))
                    if request.POST.get('timesheet_review') == 'rejected':
                        setattr(monthly_entry, ('verified_date'), None)
                        setattr(monthly_entry, ('verified_by'), None)
                        setattr(monthly_entry, ('approved_date'), None)
                        setattr(monthly_entry, ('approved_by'), None)

                    current_site = get_current_site(request=None)

                    subject = f'Timesheet for {monthly_entry.month}'
                    message = (
                        f'Dear {monthly_entry.employee.first_name},\n\nPlease note '
                        f'your timesheet for {monthly_entry.month} has been '
                        f'{monthly_entry.status} by {request.user.first_name} '
                        f'{request.user.last_name} on the BHP Utility system '
                        f'http://{current_site.domain}. \n\n')
                    if request.POST.get('comment').strip() != '':
                        comment_msg = 'Comment: ' + request.POST.get(
                            'comment') + '\n\n'
                        message += comment_msg
                    message += 'Good day :).'
                    from_email = settings.EMAIL_HOST_USER
                    user = monthly_entry.employee.email
                    try:
                        send_mail(subject, message, from_email, [user, ],
                                  fail_silently=False)
                    except SMTPException as e:
                        raise ValidationError(
                            f'There was an error sending an email: {e}')
                monthly_entry.save()
        else:
            try:
                monthly_entry = monthly_entry_cls.objects.get(
                    employee=self.employee,
                    month=datetime.strptime(f'{year}-{month}-1', '%Y-%m-%d'))
            except monthly_entry_cls.DoesNotExist:
                monthly_entry = monthly_entry_cls(employee=self.employee,
                                                  month=datetime.strptime(
                                                      f'{year}-{month}-1',
                                                      '%Y-%m-%d'))
            else:
                daily_entries = self.daily_entry_cls.objects.filter(
                    monthly_entry=monthly_entry)
                monthly_entry.comment = None

            DailyEntryFormSet = inlineformset_factory(monthly_entry_cls,
                                                      self.daily_entry_cls,
                                                      form=DailyEntryForm,
                                                      fields=['day',
                                                              'duration',
                                                              'entry_type',
                                                              'row'],
                                                      can_delete=True)

            if daily_entries:
                self.clean_data(data, daily_entries.count(), monthly_entry)

            total_forms = int(data.get('dailyentry_set-TOTAL_FORMS'))
            for i in range(total_forms):
                index = str(i)
                day = data.get('dailyentry_set-' + index + '-day')
                day = f'{year}-{month}-' + str(day)
                day_date = datetime.strptime(day, '%Y-%m-%d')
                data['dailyentry_set-' + index + '-day'] = day_date

            formset = DailyEntryFormSet(data=data, instance=monthly_entry)
            if formset.is_valid():
                if request.POST.get('save_submit') == '1':
                    monthly_entry.status = 'submitted'
                    monthly_entry.submitted_datetime = get_utcnow()
                # monthly_entry = self.sum_monthly_leave_days(formset.queryset,
                # monthly_entry)
                monthly_entry = self.calculate_monthly_overtime(
                    formset.queryset, monthly_entry)
                monthly_entry.save()
                current_contract = self.get_current_contract(
                    monthly_entry.employee_id)
                if current_contract:
                    current_contract.leave_balance = (
                            current_contract.leave_balance -
                            monthly_entry.annual_leave_taken)
                formset.save()

    # def sum_monthly_leave_days(self, dailyentries, monthly_entry):
    #
    #     leave_types = ['AL', 'STL', 'SL', 'CL', 'ML', 'PL']
    #     leave_taken_types = ['annual_leave_taken', 'study_leave_taken',
    #     'sick_leave_taken',
    #                          'compassionate_leave_taken', 'maternity_leave_taken',
    #                          'paternity_leave_taken']
    #
    #     for leave_type, leave_taken in zip(leave_types, leave_taken_types):
    #         leave_entries = dailyentries.filter(entry_type=leave_type)
    #
    #         for leave_entry in leave_entries:
    #             timeParts = [int(s) for s in leave_entry.duration.split(':')]
    #         totalSecs += (timeParts[0] * 60 + timeParts[1]) * 60
    #         totalSecs, sec = divmod(totalSecs, 60)
    #         hr, min = divmod(totalSecs, 60)
    #
    #         leave_sum_dict = leave_entries.aggregate(Sum('duration'))
    #         if leave_sum_dict.get('duration__sum'):
    #             setattr(monthly_entry, leave_taken, leave_sum_dict.get(
    #             'duration__sum') / 8)
    #
    #     return monthly_entry

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

        if self.is_security:
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

        if self.is_security:
            weekend_entries = dailyentries.filter(
                Q(day__week_day__gte=6) & Q(day__week_day__lte=7)
                | Q(entry_type='H'))

        for weekend_entry in weekend_entries:
            if self.is_security:
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
        if not self.is_security:
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
        if self.is_security:
            return tuple(entry_type for entry_type in entry_types
                         if entry_type[0] not in ['WE'])
        else:
            return tuple(entry_type for entry_type in entry_types
                         if entry_type[0] not in ['H', 'WE', 'OD'])

    @property
    def is_security(self):
        """
        - returns : True if the employee is a security guard
        """
        if self.user_employee:
            return 'Night' in self.user_employee.job_title
        return False

    def monthly_obj_job_title(self, monthly_obj):
        return monthly_obj.employee.job_title
