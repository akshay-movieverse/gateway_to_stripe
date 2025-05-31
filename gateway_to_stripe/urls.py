from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView
urlpatterns = [
        # Redirect signup and login views to Google login
    path('accounts/signup/', RedirectView.as_view(url='/accounts/google/login/')),  
    path('accounts/login/', RedirectView.as_view(url='/accounts/google/login/')),
    # Include only the Google login and logout views
    path('accounts/', include('allauth.urls')),  # Google login

    path('admin/', admin.site.urls),
    path('', include('subscriptions.urls')),
]
