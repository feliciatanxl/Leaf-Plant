import os
import requests
from dotenv import load_dotenv

load_dotenv()

stripe_key = os.getenv('STRIPE_SECRET_KEY')
if not stripe_key:
    print("❌ ERROR: Key not found.")
    exit()

def force_create_code_stable(code_name, discount_percent):
    print(f"\n🚀 Creating {code_name} (Using Stable Version 2023-10-16)...")

    # 1. Define Headers to FORCE the API version
    headers = {
        "Authorization": f"Bearer {stripe_key}",
        "Stripe-Version": "2023-10-16"  # <--- THIS IS THE MAGIC FIX
    }

    # STEP 1: Create Coupon
    print("🔹 Creating Coupon...")
    response1 = requests.post(
        "https://api.stripe.com/v1/coupons",
        headers=headers, # Use the headers here
        data={
            "percent_off": discount_percent,
            "duration": "forever",
            "name": f"{discount_percent}% Off ({code_name})"
        }
    )
    
    if response1.status_code != 200:
        print(f"❌ Coupon Failed: {response1.text}")
        return

    coupon_id = response1.json()['id']
    print(f"   -> Coupon ID: {coupon_id}")

    # STEP 2: Create Promo Code
    print("🔹 Creating Promotion Code...")
    response2 = requests.post(
        "https://api.stripe.com/v1/promotion_codes",
        headers=headers, # AND use the headers here
        data={
            "coupon": coupon_id, # The old version definitely accepts this!
            "code": code_name
        }
    )

    if response2.status_code == 200:
        print(f"✅ SUCCESS! Code '{code_name}' is live!")
    else:
        print(f"❌ Promo Code Failed: {response2.text}")

# RUN IT
force_create_code_stable("FIXEDFOREVER", 25)