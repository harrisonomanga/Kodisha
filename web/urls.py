from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="web/login.html", authentication_form=LoginForm, redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("welcome/", views.onboarding, name="onboarding"),
    path("switch-organisation/", views.switch_org, name="switch_org"),
    path("", views.dashboard, name="dashboard"),
    path("search/", views.search, name="search"),
    path("properties/", views.properties_list, name="properties_list"),
    path("properties/new/", views.properties_new, name="properties_new"),
    path("properties/<int:pk>/", views.property_detail, name="property_detail"),
    path("properties/<int:pk>/units/new/", views.unit_new, name="unit_new"),
    path("tenants/", views.tenants_list, name="tenants_list"),
    path("tenants/move-in/", views.move_in, name="move_in"),
    path("tenancies/<int:pk>/", views.lease_detail, name="lease_detail"),
    path("tenancies/<int:pk>/end/", views.lease_end, name="lease_end"),
    path("tenancies/<int:pk>/charges/", views.lease_charge_add, name="lease_charge_add"),
    path("invoices/", views.invoices_list, name="invoices_list"),
    path("invoices/generate/", views.invoices_generate, name="invoices_generate"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("payments/", views.payments_list, name="payments_list"),
    path("payments/new/", views.payment_new, name="payment_new"),
    path("payments/import/", views.payments_import, name="payments_import"),
    path("payments/import/formats/", views.bank_profile_add, name="bank_profile_add"),
    path("payments/import/sample.csv", views.import_sample, name="import_sample"),
    path("payments/simulate/", views.simulate_payment, name="simulate_payment"),
    path("payments/<int:pk>/", views.payment_detail, name="payment_detail"),
    path("payments/<int:pk>/reverse/", views.payment_reverse, name="payment_reverse"),
    path("review/", views.review_list, name="review_list"),
    path("review/<int:pk>/assign/", views.review_assign, name="review_assign"),
    path("reports/arrears/", views.report_arrears, name="report_arrears"),
    path("reports/arrears.csv", views.report_arrears_csv, name="report_arrears_csv"),
    path("settings/", views.settings_page, name="settings_page"),
]
