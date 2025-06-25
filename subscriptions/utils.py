PRICES = {
    '5': 'price_1RS9OPSEv1tl6ISPdv59qBYR',
    '10': 'price_1RS9PpSEv1tl6ISPgkliHHER',
    '20': 'price_1RS9Q6SEv1tl6ISP6u6RFTWx',
}


def assign_credits_by_price_id(user_sub, price_id):
    price_to_credits = {
        PRICES['5']: 50,
        PRICES['10']: 100,
        PRICES['20']: 9999,
    }

    credits = price_to_credits.get(price_id)
    if credits is not None:
        user_sub.credits = credits
        user_sub.save()
    else:
        raise ValueError(f"Invalid price_id: {price_id}")
    

from django.utils import timezone

def check_and_expire_subscription(user_sub):
    if user_sub.current_period_end < timezone.now():
        user_sub.is_active = False
        user_sub.credits = 0
        user_sub.save()