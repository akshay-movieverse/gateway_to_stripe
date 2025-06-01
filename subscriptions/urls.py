from django.urls import path
from . import views

urlpatterns = [
    path('subscribe/', views.subscribe_view, name='subscription'),
    path('success/', views.subscription_success, name='subscription-success'),
    path('cancel/', views.subscription_cancel, name='subscription-cancel'),
    path('create-checkout-session/', views.create_checkout_session, name='create-checkout-session'),
    path('webhook/', views.stripe_webhook, name='stripe-webhook'),

            path('login/', views.login, name='login'),

        path('', views.home, name='dashboard'),
]
