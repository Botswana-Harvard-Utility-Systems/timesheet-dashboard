from django.urls import path
from edc_dashboard import UrlConfig
from django.contrib.auth import get_user_model
from .patterns import identifier
from .views import ListboardView

app_name = 'timesheet_dashboard'

User = get_user_model()

timesheet_listboard_url_config = UrlConfig(
    url_name='timesheet_listboard_url',
    view_class=ListboardView,
    label='timesheet_listboard',
    identifier_label='employee_id',
    identifier_pattern=identifier)


urlpatterns = []
urlpatterns += timesheet_listboard_url_config.listboard_urls