import os
import stripe
import requests 
from datetime import datetime
import pytz 
from flask import Blueprint, request, jsonify, render_template, session, flash, redirect, url_for, abort
from dotenv import load_dotenv
from models import db, Product, PromoCode, WhatsAppOrder, Customer, GroupLeader, LoyaltyPoints, LoyaltyTransaction, Voucher

load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
stripe.api_version = '2023-10-16' 
endpoint_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

# --- WHATSAPP CONFIGURATION ---
WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

orders_bp = Blueprint('orders', __name__)

@orders_bp.route('/')
def orders_page():
    if 'user_id' not in session:
        flash("Please log in to view your bag.", "warning")
        return redirect(url_for('account')) 
    
    if session.get('user_role') != 'user':
        abort(403)

    user_vouchers = Voucher.query.filter_by(customer_id=session['user_id'], is_used=False).all()
    pub_key = os.getenv("STRIPE_PUBLISHABLE_KEY")
    
    return render_template('orders.html', pub_key=pub_key, user_vouchers=user_vouchers)

# --- 1. LIVE STOCK API ---
@orders_bp.route('/api/get-stock')
def get_live_stock():
    products = Product.query.with_entities(Product.id, Product.available_qty, Product.image_file).all()
    product_map = {str(p.id): {'stock': p.available_qty, 'image': p.image_file} for p in products}
    return jsonify(product_map)

# --- 2. CUSTOMER PROMO VALIDATOR ---
@orders_bp.route('/api/validate-promo', methods=['POST'])
def validate_promo():
    data = request.get_json()
    user_code = data.get('code', '').strip().upper()
    promo = PromoCode.query.filter_by(code=user_code, is_active=True).first()
    
    if promo:
        if promo.expires_at and promo.expires_at < datetime.now():
            return jsonify({"valid": False, "message": "This code has expired."}), 400
        return jsonify({"valid": True, "discount": promo.discount_percent, "message": f"Code '{user_code}' applied!"})
    
    return jsonify({"valid": False, "message": "Invalid or inactive code."}), 400

# --- 3. CHECKOUT SESSION ---
@orders_bp.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.get_json()
        cart_items = data.get('cart', [])
        promo_code = data.get('promoCode', '').strip().upper()
        selected_voucher_id = data.get('voucherId') 
        
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'User not logged in'}), 401

        line_items = []
        for item in cart_items:
            # Safety: Ensure we actually found the product in our DB
            product = Product.query.get(item['id'])
            if not product:
                print(f"⚠️ Product ID {item['id']} not found in database. Skipping.")
                continue
            
            line_items.append({
                'price_data': {
                    'currency': 'sgd',
                    'product_data': {
                        'name': product.name, 
                        # STRIPE REQUIREMENT: Metadata values MUST be strings
                        'metadata': {'product_id': str(product.id)} 
                    },
                    'unit_amount': int(product.price * 100),
                },
                'quantity': item['qty'],
            })

        if not line_items:
            return jsonify({'error': 'Your cart is empty or products are invalid'}), 400

        # --- DISCOUNT LOGIC ---
        applied_discounts = []
        metadata_voucher_id = ""

        if selected_voucher_id:
            # Ensure voucher belongs to user and is unused
            voucher = Voucher.query.filter_by(id=selected_voucher_id, customer_id=user_id, is_used=False).first()
            if voucher:
                applied_discounts.append({'coupon': voucher.stripe_coupon_id})
                metadata_voucher_id = str(voucher.id)

        elif promo_code:
            promo = PromoCode.query.filter_by(code=promo_code, is_active=True).first()
            if promo:
                applied_discounts.append({'coupon': promo.stripe_coupon_id})

        # --- SESSION PARAMS ---
        session_params = {
            'payment_method_types': ['card'],
            'line_items': line_items,
            'mode': 'payment',
            'client_reference_id': str(user_id),
            'metadata': {
                'user_id': str(user_id),
                'voucher_id': metadata_voucher_id,
                'source': 'website' # This tells the webhook it's a web order
            },
            'success_url': 'http://127.0.0.1:5001/orders/success',
            'cancel_url': 'http://127.0.0.1:5001/orders',
        }

        if applied_discounts:
            session_params['discounts'] = applied_discounts
        else:
            session_params['allow_promotion_codes'] = True

        checkout_session = stripe.checkout.Session.create(**session_params)
        return jsonify({'id': checkout_session.id})

    except Exception as e:
        # This print statement is your best friend. Check your terminal!
        print(f"❌ Stripe Session Error: {str(e)}")
        return jsonify(error=str(e)), 403

# --- 4. WEBHOOK LISTENER ---
@orders_bp.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature')
    
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return jsonify(success=False), 400

    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        # Import inside the block to avoid circular dependencies
        from models import db, Customer, Voucher, LoyaltyPoints 
        
        # 1. FETCH FULL SESSION (Crucial for WhatsApp/Mobile Promo Codes)
        full_session = stripe.checkout.Session.retrieve(
            session_data['id'],
            expand=['total_details.breakdown.discounts']
        )

        user_id_str = session_data.get('metadata', {}).get('user_id')
        voucher_id_str = session_data.get('metadata', {}).get('voucher_id')
        source = session_data.get('metadata', {}).get('source')

        if not user_id_str:
            return jsonify(success=True), 200

        try:
            user_id = int(user_id_str)
            customer = Customer.query.get(user_id)
            
            if customer:
                # --- VOUCHER REMOVAL LOGIC ---
                # A. Handle Website Vouchers (via direct ID in metadata)
                if voucher_id_str and voucher_id_str.strip():
                    v = Voucher.query.filter_by(id=int(voucher_id_str), customer_id=customer.id).first()
                    if v: 
                        v.is_used = True
                        print(f"✅ Website Voucher {v.code} marked as used.")

                # B. Handle WhatsApp/Mobile Vouchers (via Promo Code string detection)
                discounts = full_session.get('total_details', {}).get('breakdown', {}).get('discounts', [])
                for d in discounts:
                    promo_id = d.get('discount', {}).get('promotion_code')
                    if promo_id:
                        # Convert Stripe Promo ID to your RW- string code
                        promo_obj = stripe.PromotionCode.retrieve(promo_id)
                        applied_code = promo_obj.code
                        
                        v_auto = Voucher.query.filter_by(
                            code=applied_code, 
                            customer_id=customer.id, 
                            is_used=False
                        ).first()
                        
                        if v_auto:
                            v_auto.is_used = True
                            print(f"✅ WhatsApp/Mobile Voucher {applied_code} marked as used.")

                # 1. SAVE ORDER & STOCK
                items_summary = handle_successful_order(session_data)

                # 2. AWARD POINTS (Based on actual paid amount)
                total_paid = session_data.get('amount_total', 0) / 100
                
                # Check tier safely
                tier = customer.loyalty.tier if customer.loyalty else 'Seedling'
                multiplier = 1.5 if tier == 'Harvest' else 1.2 if tier == 'Sprout' else 1.0
                
                points_to_add = int(total_paid * multiplier)
                
                if not customer.loyalty:
                    customer.loyalty = LoyaltyPoints(customer_id=customer.id, current_points=0, lifetime_points=0)
                    db.session.add(customer.loyalty)
                    db.session.flush() 
                
                customer.loyalty.add_points(points_to_add, f"Order #{session_data.id[-6:]}")
                
                # 3. NOTIFY WHATSAPP
                if source == 'whatsapp':
                    send_payment_confirmation(customer.phone, total_paid, items_summary, points_to_add)
                
                db.session.commit()
                
        except Exception as e:
            db.session.rollback()
            print(f"🔥 Webhook Logic Error: {str(e)}")
            return jsonify(success=False), 500

    return jsonify(success=True), 200

# --- 5. ORDER PROCESSING HELPER (Hardened Stock Deduction) ---
def handle_successful_order(session_data):
    items_summary = []
    try:
        from models import db, Customer, WhatsAppOrder, Product
        user_id = int(session_data.get('metadata', {}).get('user_id'))
        customer = Customer.query.get(user_id)
        if not customer: return []

        # Fetch items from the completed Stripe session
        line_items = stripe.checkout.Session.list_line_items(session_data['id'], limit=100)

        for item in line_items.data:
            # A. Save the order record
            new_order = WhatsAppOrder(
                customer_id=customer.id,
                leader_id=customer.leader_id, 
                customer_phone=customer.phone,
                product_name=item.description, 
                quantity=item.quantity,
                total_price=item.amount_total / 100,
                commission_earned=(item.amount_total / 100) * 0.111, 
                order_status='Paid',
                timestamp=datetime.now(pytz.timezone('Asia/Singapore'))
            )
            db.session.add(new_order)
            items_summary.append(f"{item.quantity}x {item.description}")

            # B. STOCK DEDUCTION LOGIC
            try:
                # 1. Get the Product ID from Stripe (Retrieving the full product object)
                # This ensures we get the metadata we attached during checkout
                stripe_product = stripe.Product.retrieve(item.price.product)
                db_product_id = stripe_product.metadata.get('product_id')
                
                if db_product_id:
                    # 2. Find the product in YOUR database
                    product = Product.query.get(int(db_product_id))
                    if product:
                        print(f"📦 Found Product: {product.name}. Current Stock: {product.available_qty}")
                        
                        # 3. Deduct the amount and save
                        product.available_qty = max(0, product.available_qty - item.quantity)
                        
                        if product.available_qty == 0:
                            product.status = "Out of Stock"
                        
                        print(f"📉 New Stock for {product.name}: {product.available_qty}")
                else:
                    print(f"⚠️ Metadata 'product_id' missing for: {item.description}")

            except Exception as stock_err:
                print(f"⚠️ Stock deduction failed for {item.description}: {stock_err}")

        return items_summary
    except Exception as e:
        print(f"❌ handle_successful_order error: {e}")
        return []

# --- 6. WHATSAPP SENDER HELPER ---
def send_payment_confirmation(phone, amount, items, points_earned):
    url = f"https://graph.facebook.com/v24.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    clean_phone = ''.join(filter(str.isdigit, str(phone)))
    
    item_list_str = "\n".join([f"• {i}" for i in items])
    message_text = (
        f"🎉 *Payment Received!*\n\n"
        f"Thank you! We've received your payment of *${amount:.2f}*.\n\n"
        f"🛒 *Your Order:*\n{item_list_str}\n\n"
        f"🌱 *Harvest Rewards:*\nYou earned *{points_earned} Leaf Points*!\n\n"
        f"Track your order in your account! 🚚"
    )

    try:
        requests.post(url, headers=headers, json={"messaging_product": "whatsapp", "to": clean_phone, "type": "text", "text": {"body": message_text}})
    except Exception as e:
        print(f"❌ WhatsApp Helper Error: {e}")

@orders_bp.route('/success')
def success():
    return render_template('success.html')