import stripe
import json
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib.auth.models import User

from subscriptions.utils import assign_credits_based_on_plan, assign_credits_by_price_id, check_and_expire_subscription, handle_subscription_period_end
from .models import Invoice, StripeCustomer, StripePlan, UserSubscription
from django.utils import timezone as dj_timezone
from datetime import datetime, timezone
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction



stripe.api_key = settings.STRIPE_SECRET_KEY


@login_required
def subscribe_view(request):
    """
    Displays the subscription page with available plans and the user's current plan.
    """
    user = request.user
    user_subscription = None
    current_plan_name = None
    
    try:
        user_subscription = UserSubscription.objects.get(user=user)
        if user_subscription.is_active and user_subscription.plan:
            current_plan_name = user_subscription.plan.name
    except UserSubscription.DoesNotExist:
        #logger.info(f"No existing subscription found for user {user.username}.")
        pass # No active subscription, current_plan_name remains None

    # Fetch active plans from the database
    available_plans = StripePlan.objects.filter(is_active=True).order_by('monthly_credit_allotment')

    context = {
        'current_plan_name': current_plan_name,
        'user_subscription': user_subscription, # Pass user_subscription for conditional rendering
        'available_plans': available_plans,
        #'STRIPE_PUBLIC_KEY': settings.STRIPE_PUBLIC_KEY, # Pass public key for client-side JS
    }
    return render(request, 'subscription.html', context)

def subscription_success(request):
    """
    Handles successful checkout session redirects.
    The actual subscription update is handled by the webhook.
    """
    session_id = request.GET.get("session_id")
    if not session_id:
        messages.error(request, "Invalid session ID provided.")
        return redirect('dashboard') # Redirect to a safe page

    try:
        # Retrieve session to confirm it exists, but rely on webhook for database update
        session = stripe.checkout.Session.retrieve(session_id)
        messages.success(request, "Your subscription process has started successfully! Please allow a moment for it to reflect.")
        #logger.info(f"Checkout session {session_id} retrieved successfully for success page.")
    except stripe.error.StripeError as e:
        messages.error(request, f"Error retrieving Stripe session: {e}. Please check your subscription status.")
        #logger.error(f"Stripe error retrieving session {session_id} on success page: {e}", exc_info=True)
    except Exception as e:
        messages.error(request, "An unexpected error occurred. Please try again or contact support.")
        #logger.critical(f"Unexpected error retrieving session {session_id} on success page: {e}", exc_info=True)
        
    return render(request, "subscription_success.html", {
        "session": session
    })

def subscription_cancel(request):
    """
    Handles cancelled checkout session redirects.
    """
    session_id = request.GET.get("session_id")
    messages.info(request, "Your subscription process was cancelled.")
    #logger.info(f"Subscription cancelled by user for session ID: {session_id}")
    return render(request, "subscription_cancel.html")


@login_required
@require_POST
def create_checkout_session(request):
    """
    Creates a Stripe Checkout Session for new subscriptions or plan changes.
    """
    user = request.user
    price_id = request.POST.get("price_id")

    if not price_id:
        messages.error(request, "No plan selected. Please choose a plan to subscribe.")
        return redirect('subscribe')

    try:
        # Validate the price_id against your Plan model
        selected_plan = StripePlan.objects.get(stripe_price_id=price_id, is_active=True)
    except StripePlan.DoesNotExist:
        messages.error(request, "Invalid or unavailable plan selected.")
        #logger.warning(f"User {user.username} attempted to subscribe to an invalid price ID: {price_id}")
        return redirect('subscribe')
    except Exception as e:
        messages.error(request, "An error occurred while validating the plan. Please try again.")
        #logger.error(f"Error validating plan for user {user.username}: {e}", exc_info=True)
        return redirect('subscribe')
    

    customer_id = None
    old_subscription_id = ""
    try:
        # Check if the user already has a Stripe customer ID
        user_sub = UserSubscription.objects.get(user=user)
        customer_id = user_sub.stripe_customer_id

        # If an active subscription exists, cancel it first before creating a new one.
        # Stripe will handle proration and effective dates.
        if user_sub.is_active and user_sub.stripe_subscription_id:
            try:
                old_subscription_id = user_sub.stripe_subscription_id
                #stripe.Subscription.cancel(user_sub.stripe_subscription_id)

                #messages.info(request, "Your previous subscription is being updated/cancelled.")
                # logger.info(f"User {user.username} cancelling existing subscription {user_sub.stripe_subscription_id} "
                #             f"before creating a new one with price_id: {price_id}")
            except stripe.error.StripeError as e:
                #logger.error(f"Stripe error cancelling subscription {user_sub.stripe_subscription_id} for user {user.username}: {e}")
                messages.warning(request, "Could not immediately cancel your old subscription. Please contact support if issues arise.")
                return redirect('subscribe')
            except Exception as e:
                #logger.critical(f"Unexpected error cancelling subscription for user {user.username}: {e}", exc_info=True)
                messages.warning(request, "Unexpected error cancelling subscription.")
                return redirect('subscribe')

    except UserSubscription.DoesNotExist:
        #logger.info(f"No existing UserSubscription for {user.username}. A new customer will be created if needed.")
        pass # No existing subscription, proceed to create customer/checkout session

    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=user.email, #if not customer_id else None, # Only provide email if creating new customer
            customer=customer_id, # Use existing customer if available
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=request.build_absolute_uri('/success/') + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=request.build_absolute_uri('/cancel/') + '?session_id={CHECKOUT_SESSION_ID}',
            metadata={
                "user_id": str(user.id), # Ensure user_id is a string
                "plan_id": str(selected_plan.id), # Pass internal plan ID for easier lookup in webhook
                "old_subscription_id": old_subscription_id
            }
        )
        return redirect(checkout_session.url)
    except stripe.error.StripeError as e:
        messages.error(request, f"Payment processing error: {e}")
        #logger.error(f"Stripe error creating checkout session for {user.username}: {e}", exc_info=True)
        return redirect('subscribe')
    except Exception as e:
        messages.error(request, "An unexpected error occurred. Please try again.")
        #logger.critical(f"Unexpected error creating checkout session for {user.username}: {e}", exc_info=True)
        return redirect('subscribe')

@require_POST
@csrf_exempt
def stripe_webhook(request):
    """
    Handles Stripe webhook events to keep the local database in sync.
    Uses Django's transaction.atomic for database consistency.
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    endpoint_secret = settings.STRIPE_WEBHOOK_SECRET
    event = None

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        #logger.error(f"Invalid payload for webhook: {e}", exc_info=True)
        return HttpResponse(status=400) # Invalid payload
    except stripe.error.SignatureVerificationError as e:
        #logger.error(f"Invalid signature for webhook: {e}", exc_info=True)
        return HttpResponse(status=400) # Invalid signature
    except Exception as e:
        #logger.critical(f"Unexpected error in webhook signature verification: {e}", exc_info=True)
        return HttpResponse(status=500)


    # Use atomic transactions to ensure database consistency
    with transaction.atomic():
        event_type = event['type']
        data_object = event['data']['object']

        try:
            if event_type == 'checkout.session.completed':
                # This webhook fires for both 'subscription' mode and 'setup' mode sessions.
                session = data_object
                session_mode = session.get("mode")
                user_id = session["metadata"].get("user_id")
                old_subscription_id = session["metadata"].get('old_subscription_id')

                if not user_id:
                    #logger.error(f"checkout.session.completed event missing user_id in metadata: {session.id}")
                    return HttpResponse(status=400)
                
                
                if old_subscription_id:
                    try:
                        stripe.Subscription.cancel(old_subscription_id)
                        #logger.info(f"Canceled old subscription {old_subscription_id} for user {user_id}")
                    except stripe.error.StripeError as e:
                        pass
                        #logger.error(f"Error canceling old subscription {old_subscription_id}: {e}")


                user = get_object_or_404(User, id=user_id)

                if session_mode == 'subscription':
                    # A new subscription was created
                    customer_id = session.get("customer")
                    subscription_id = session.get("subscription")
                    plan_id = session["metadata"].get("plan_id")

                    if not (customer_id and subscription_id and plan_id):
                        #logger.error(f"checkout.session.completed (subscription) missing required data: {session.id}")
                        return HttpResponse(status=400)

                    try:
                        stripe_subscription = stripe.Subscription.retrieve(subscription_id)
                        selected_plan = StripePlan.objects.get(id=plan_id)
                    except stripe.error.StripeError as e:
                        #logger.error(f"Stripe API error retrieving subscription {subscription_id}: {e}", exc_info=True)
                        return HttpResponse(status=500)
                    except StripePlan.DoesNotExist:
                        #logger.error(f"Plan with ID {plan_id} not found for user {user.username}.")
                        return HttpResponse(status=400)

                    UserSubscription.objects.update_or_create(
                        user=user,
                        defaults={
                            'stripe_customer_id': customer_id,
                            'stripe_subscription_id': subscription_id,
                            'plan': selected_plan,
                            'status': stripe_subscription["status"],
                            'is_active': stripe_subscription["status"] == 'active',
                            'current_period_start': datetime.fromtimestamp(stripe_subscription["items"]["data"][0]["current_period_start"], tz=timezone.utc),
                            'current_period_end': datetime.fromtimestamp(stripe_subscription["items"]["data"][0]["current_period_end"], tz=timezone.utc),
                            #'monthly_credit_allotment': selected_plan.monthly_credit_allotment, # Set initial allotment
                            'last_credit_refill_date': dj_timezone.now() # Mark credits refilled
                        }
                    )
                    user_sub = UserSubscription.objects.get(user=user) # Retrieve the updated/created sub
                    assign_credits_based_on_plan(user_sub, selected_plan) # Assign initial credits
                    #logger.info(f"User {user.username} subscription (ID: {subscription_id}) created/updated.")

                elif session_mode == 'setup':
                    # This session was to set up a payment method.
                    # The primary customer object in Stripe will be updated by Stripe itself.
                    # We primarily need to ensure the customer_id exists for the user.
                    customer_id = session.get("customer")
                    if customer_id and user_id:
                        user_sub, created = UserSubscription.objects.get_or_create(
                            user=user,
                            defaults={'stripe_customer_id': customer_id}
                        )
                        if not created:
                             # If subscription already exists, ensure customer ID is correct
                            user_sub.stripe_customer_id = customer_id
                            user_sub.save()
                        #logger.info(f"User {user.username} successfully updated payment method (customer ID: {customer_id}).")
                    else:
                        pass
                        #logger.warning(f"checkout.session.completed (setup) missing customer_id or user_id: {session.id}")


            elif event_type == 'invoice.payment_succeeded':
                invoice = data_object
                # CORRECTED LINE: Access subscription ID directly from the invoice object
                subscription_id = invoice.get('subscription',None) 
                #subscription_id = None
                if 'parent' in invoice and 'subscription_details' in invoice['parent']:
                    subscription_id = invoice['parent']['subscription_details'].get('subscription')
    
                customer_id = invoice.get('customer')

                if not (subscription_id and customer_id):
                    #logger.error(f"invoice.payment_succeeded missing subscription_id or customer_id: {invoice.id}")
                    return HttpResponse(status=400)

                try:
                    user_sub = UserSubscription.objects.get(stripe_subscription_id=subscription_id)#, stripe_customer_id=customer_id)
                except UserSubscription.DoesNotExist:
                    #logger.error(f"UserSubscription not found for sub ID {subscription_id} and customer ID {customer_id}.")
                    return HttpResponse(status=404)

                # Update UserSubscription status and period end
                user_sub.is_active = True
                user_sub.status = 'active'
                # Use current_period_end from the invoice itself, as it reflects the new period
                # Get the subscription line item from the invoice
                line_item = invoice["lines"]["data"][0]
                user_sub.current_period_end = datetime.fromtimestamp(line_item["period"]["end"], tz=timezone.utc)
                #user_sub.current_period_start = datetime.fromtimestamp(invoice["period_start"], tz=timezone.utc)
                user_sub.save()

                # Add credits for the new billing period
                # For yearly plans with monthly credits, this webhook fires yearly.
                # The monthly credit refill is handled by check_and_refill_monthly_credits on login.
                # Only re-assign initial credits if it's the very first payment,
                # or if the plan explicitly dictates a new credit drop on *every* successful payment.
                # For monthly credits, we rely on the `check_and_refill_monthly_credits` utility.
                # If your yearly plan *gives all credits at once*, then call assign_credits_based_on_plan here.
                # Otherwise, this only ensures the subscription is active.
                
                # Create invoice record
                Invoice.objects.create(
                    user=user_sub.user,
                    #user_subscription=user_sub,
                    stripe_invoice_id=invoice['id'],
                    amount_due=invoice['amount_due'] / 100, # Stripe amounts are in cents
                    currency=invoice['currency'],
                    status=invoice['status'],
                    invoice_pdf=invoice.get('invoice_pdf'),
                    invoice_page=invoice.get('hosted_invoice_url'),
                    period_start=datetime.fromtimestamp(line_item["period"]["start"], tz=timezone.utc),
                    period_end=datetime.fromtimestamp(line_item["period"]["end"], tz=timezone.utc),
                    is_successful_payment=True
                )
                #logger.info(f"Invoice {invoice['id']} payment succeeded for user {user_sub.user.username}.")


            elif event_type == 'invoice.payment_failed':
                invoice = data_object
                # CORRECTED LINE: Access subscription ID directly from the invoice object
                subscription_id = None
                if 'parent' in invoice and 'subscription_details' in invoice['parent']:
                    subscription_id = invoice['parent']['subscription_details'].get('subscription')

                customer_id = invoice.get('customer')

                if not (subscription_id and customer_id):
                    #logger.error(f"invoice.payment_failed missing subscription_id or customer_id: {invoice.id}")
                    return HttpResponse(status=400)

                try:
                    user_sub = UserSubscription.objects.get(stripe_subscription_id=subscription_id)#, stripe_customer_id=customer_id)
                    user_sub.is_active = False # Mark as inactive
                    user_sub.status = 'past_due' if invoice['billing_reason'] == 'subscription_cycle' else 'unpaid'
                    user_sub.credits = 0 # Revoke credits
                    user_sub.save()
                except UserSubscription.DoesNotExist:
                    #logger.error(f"UserSubscription not found for sub ID {subscription_id} and customer ID {customer_id}.")
                    return HttpResponse(status=404)

                # Create invoice record for failed payment
                Invoice.objects.create(
                    user=user_sub.user,
                    #user_subscription=user_sub,
                    stripe_invoice_id=invoice['id'],
                    amount_due=invoice['amount_due'] / 100,
                    currency=invoice['currency'],
                    status=invoice['status'],
                    invoice_pdf=invoice.get('invoice_pdf'),
                    invoice_page=invoice.get('hosted_invoice_url'),
                    period_start=datetime.fromtimestamp(line_item["period"]["start"], tz=timezone.utc),
                    period_end=datetime.fromtimestamp(line_item["period"]["end"], tz=timezone.utc),
                    is_successful_payment=False
                )
                #logger.warning(f"Invoice {invoice['id']} payment failed for user {user_sub.user.username}.")


            elif event_type == 'customer.subscription.updated':
                sub_data = data_object
                subscription_id = sub_data['id']
                customer_id = sub_data['customer']

                try:
                    user_sub = UserSubscription.objects.get(stripe_subscription_id=subscription_id, stripe_customer_id=customer_id)
                except UserSubscription.DoesNotExist:
                    #logger.error(f"UserSubscription not found for sub ID {subscription_id} and customer ID {customer_id}.")
                    return HttpResponse(status=404)

                # Update plan if it changed
                current_stripe_price_id = sub_data['items']['data'][0]['price']['id']
                try:
                    new_plan = StripePlan.objects.get(stripe_price_id=current_stripe_price_id)
                    user_sub.plan = new_plan
                    #user_sub.monthly_credit_allotment = new_plan.monthly_credit_allotment
                except StripePlan.DoesNotExist:
                    pass
                    #logger.error(f"Plan with price ID {current_stripe_price_id} not found on subscription update for {user_sub.user.username}.")

                user_sub.status = sub_data['status']
                user_sub.is_active = sub_data['status'] == 'active' or sub_data['status'] == 'trialing'
                user_sub.current_period_start = datetime.fromtimestamp(sub_data["items"]["data"][0]["current_period_start"], tz=timezone.utc)
                user_sub.current_period_end = datetime.fromtimestamp(sub_data["items"]["data"][0]["current_period_end"], tz=timezone.utc)

                # Handle pause/resume related fields
                pause_collection_behavior = sub_data.get('pause_collection', {}).get('behavior')
                if pause_collection_behavior:
                    # When paused, Stripe sets the status to 'paused' and provides pause_collection details
                    user_sub.is_paused = True
                    user_sub.status = 'paused' # Override status for clarity if paused
                    #user_sub.is_active = False # If paused, it's not considered active for billing
                else:
                    # When unpaused, pause_collection will be null

                    # Ensure status is correctly set back if it was paused and now active
                    if user_sub.status == 'paused' and sub_data['status'] == 'active':
                        user_sub.status = 'active'
                        user_sub.is_paused = False
                        #user_sub.is_active = True
                        

                user_sub.save()
                #logger.info(f"User {user_sub.user.username} subscription {subscription_id} updated to status: {user_sub.status}.")


            elif event_type == 'customer.subscription.deleted':
                sub_data = data_object
                subscription_id = sub_data['id']
                customer_id = sub_data['customer']

                try:
                    user_sub = UserSubscription.objects.get(stripe_subscription_id=subscription_id, stripe_customer_id=customer_id)
                    user_sub.is_active = False
                    user_sub.status = 'canceled' # Or 'ended' depending on your lifecycle
                    user_sub.credits = 0 # Clear credits on deletion
                    #user_sub.stripe_subscription_id = None # Clear subscription ID as it's deleted

                    lifetime_plan = get_object_or_404(StripePlan, plan_type='lifetime')
                    user_sub.plan = lifetime_plan
                    user_sub.credits = lifetime_plan.monthly_credit_allotment
                    user_sub.stripe_subscription_id = "Ended"



                    user_sub.save()
                    #logger.info(f"User {user_sub.user.username} subscription {subscription_id} deleted. Deactivated and credits revoked.")
                except UserSubscription.DoesNotExist:
                    #logger.warning(f"customer.subscription.deleted: UserSubscription not found for sub ID {subscription_id} and customer ID {customer_id}.")
                    return HttpResponse(status=404)
            
            else:
                pass
                #logger.info(f"Unhandled webhook event type: {event_type}")
        except Exception as e:
            #logger.critical(f"Error processing Stripe webhook event {event_type} for object {data_object.get('id', 'N/A')}: {e}", exc_info=True)
            print(f"Error processing Stripe webhook event {event_type} for object {data_object.get('id', 'N/A')}: {e}")
            return JsonResponse({'error': str(e)}, status=500) #HttpResponse(status=500) # Internal Server Error for processing issues

    return HttpResponse(status=200)


from django.contrib.auth.decorators import login_required
# Create your views here.


@login_required
def home(request):
    """
    Dashboard view for the user, displaying credits and subscription status.
    Handles credit usage and performs subscription status and credit refills.
    """
    user = request.user
    user_subscription = None
    
    try:
        user_subscription = UserSubscription.objects.get(user=user)
        
        # --- Crucial for "monthly credits for yearly plans without cronjobs" ---
        # 1. Handle subscription period expiration
        handle_subscription_period_end(user_subscription)
        
        # 2. Refill monthly credits if due
        if user_subscription.is_active: # Only refill if subscription is currently active
            pass
            #check_and_refill_monthly_credits(user_subscription)
        # --- End of "lazy cron" logic ---

        # Re-fetch or ensure the object is updated after utility calls
        user_subscription.refresh_from_db() 

    except UserSubscription.DoesNotExist:
        messages.info(request, "You currently don't have an active subscription.")
        #logger.info(f"No subscription found for user {user.username} on dashboard view.")
        # user_subscription remains None

    if request.method == 'POST':
        credits_to_use_str = request.POST.get('credits')
        if not credits_to_use_str:
            messages.error(request, "Please enter the number of credits to use.")
            return redirect('dashboard')

        try:
            credits_to_use = int(credits_to_use_str)
            if credits_to_use <= 0:
                messages.error(request, "Credits to use must be a positive number.")
                return redirect('dashboard')
        except ValueError:
            messages.error(request, "Invalid number of credits.")
            return redirect('dashboard')

        if not user_subscription or not user_subscription.is_active:
            messages.error(request, "You need an active subscription to use credits.")
            return redirect('dashboard')

        if user_subscription.credits < credits_to_use:
            messages.error(request, f"Not enough credits. You have {user_subscription.credits} but tried to use {credits_to_use}.")
            return redirect('dashboard')

        try:
            user_subscription.credits = max(0, user_subscription.credits - credits_to_use)
            user_subscription.save()
            messages.success(request, f"Used {credits_to_use} credits. Remaining: {user_subscription.credits}.")
            #logger.info(f"User {user.username} used {credits_to_use} credits. Remaining: {user_subscription.credits}")
        except Exception as e:
            messages.error(request, "An error occurred while using credits. Please try again.")
            #logger.error(f"Error using credits for user {user.username}: {e}", exc_info=True)
            
        return redirect('dashboard')

    context = {
        "user_subscription": user_subscription, # Pass the object to the template
        # You can add other context variables here if needed
    }
    return render(request, "dashboard.html", context)


def login(request):
    """
    Placeholder for login view.
    """
    return render(request,"login.html")




@login_required
@require_POST
def pause_subscription(request):
    """
    Pauses the user's Stripe subscription.
    """
    user = request.user
    try:
        user_sub = UserSubscription.objects.get(user=user)
        if not user_sub.stripe_subscription_id:
            messages.error(request, "You don't have an active Stripe subscription to pause.")
            return redirect('dashboard')
        
        # Pause the subscription in Stripe.
        # 'void' behavior sets future invoices to void.
        # Stripe will move the subscription to 'paused' status.
        stripe.Subscription.modify(
            user_sub.stripe_subscription_id,
            pause_collection={"behavior": "mark_uncollectible"}
            #pause_collection={'behavior': 'void'}
        )
        
        # Update local model immediately, but webhook will provide final confirmation
        user_sub.status = 'paused'
        user_sub.is_paused = True # Deactivate locally until resumed
        user_sub.save()

        messages.success(request, "Your subscription has been paused.")
        #logger.info(f"Subscription {user_sub.stripe_subscription_id} paused for user {user.username}.")
    except UserSubscription.DoesNotExist:
        messages.error(request, "Subscription not found for your account.")
        #logger.warning(f"Attempt to pause non-existent subscription for user {user.username}.")
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error pausing subscription: {e}")
        #logger.error(f"Stripe error pausing subscription for user {user.username}: {e}", exc_info=True)
    except Exception as e:
        messages.error(request, "An unexpected error occurred while pausing your subscription.")
        #logger.critical(f"Unexpected error pausing subscription for user {user.username}: {e}", exc_info=True)
    return redirect('dashboard')


@login_required
@require_POST
def resume_subscription(request):
    """
    Resumes a paused Stripe subscription.
    """
    user = request.user
    try:
        user_sub = UserSubscription.objects.get(user=user)
        if not user_sub.stripe_subscription_id:
            messages.error(request, "You don't have a Stripe subscription to resume.")
            return redirect('dashboard')
            
        # Clear the pause_collection to resume billing.
        # Stripe will move the subscription back to 'active' status.
        stripe.Subscription.modify(
            user_sub.stripe_subscription_id,
            pause_collection='' # Empty string or None clears the pause
        )
        # Fetch updated subscription details from Stripe to get current_period_end
        # This is important as resuming can sometimes shift billing cycles.
        subscription = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        
        # Update local model immediately, but webhook will provide final confirmation
        user_sub.status = subscription["status"] # Will likely be 'active'
        user_sub.current_period_end = datetime.fromtimestamp(subscription["items"]["data"][0]["current_period_end"], tz=timezone.utc)
        user_sub.is_paused= True
        user_sub.save()

        messages.success(request, "Your subscription has been resumed.")
        #logger.info(f"Subscription {user_sub.stripe_subscription_id} resumed for user {user.username}.")
    except UserSubscription.DoesNotExist:
        messages.error(request, "Subscription not found for your account.")
        #logger.warning(f"Attempt to resume non-existent subscription for user {user.username}.")
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error resuming subscription: {e}")
        #logger.error(f"Stripe error resuming subscription for user {user.username}: {e}", exc_info=True)
    except Exception as e:
        messages.error(request, "An unexpected error occurred while resuming your subscription.")
        #logger.critical(f"Unexpected error resuming subscription for user {user.username}: {e}", exc_info=True)
    return redirect('dashboard')





@login_required
def update_payment_method(request):
    """
    Redirects user to Stripe's hosted page to update their payment method.
    """
    user = request.user
    try:
        user_sub = UserSubscription.objects.get(user=user)
        if not user_sub.stripe_customer_id:
            messages.error(request, "No Stripe customer found for your account.")
            return redirect('dashboard')

        session = stripe.checkout.Session.create(
            customer=user_sub.stripe_customer_id,
            payment_method_types=['card'],
            mode='setup', # Use setup mode for updating payment methods
            success_url=request.build_absolute_uri('/dashboard/'),
            cancel_url=request.build_absolute_uri('/dashboard/'),
        )
        #logger.info(f"User {user.username} redirected to Stripe for payment method update.")
        return redirect(session.url)
    except UserSubscription.DoesNotExist:
        messages.error(request, "Subscription details not found for your account.")
        #logger.warning(f"Attempt to update payment method for non-existent subscription for user {user.username}.")
    except stripe.error.StripeError as e:
        messages.error(request, f"Stripe error creating payment method update session: {e}")
        #logger.error(f"Stripe error creating setup session for user {user.username}: {e}", exc_info=True)
    except Exception as e:
        messages.error(request, "An unexpected error occurred. Please try again.")
        #logger.critical(f"Unexpected error creating setup session for user {user.username}: {e}", exc_info=True)
    return redirect('dashboard')

