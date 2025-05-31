from django.contrib.auth.models import User
from django.db import models

class StripeCustomer(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stripe_customer_id = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    credits = models.IntegerField(default=0)
    last_renewed = models.DateField(null=True, blank=True)
