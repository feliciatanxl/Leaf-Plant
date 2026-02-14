import os
import stripe
import requests 
from datetime import datetime
import pytz 
from flask import Blueprint, request, jsonify, render_template, session, flash, redirect, url_for, abort
from dotenv import load_dotenv
from models import db, Product, PromoCode, WhatsAppOrder, Customer, GroupLeader, LoyaltyPoints, LoyaltyTransaction, Voucher
from whatsapp.app import send_whatsapp_message

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

# --- 3. CHECKOUT SESSION (CLEANED & FIXED) ---
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

        # 1. Prepare Line Items & Calculate Total
        line_items = []
        cart_total_amount = 0.0  # <--- Initialize total counter

        for item in cart_items:
            product = Product.query.get(item['id'])
            if not product:
                print(f"⚠️ Product ID {item['id']} not found. Skipping.")
                continue
            
            # <--- Add to running total
            cart_total_amount += (float(product.price) * item['qty'])

            line_items.append({
                'price_data': {
                    'currency': 'sgd',
                    'product_data': {
                        'name': product.name, 
                        'metadata': {'product_id': str(product.id)} 
                    },
                    'unit_amount': int(product.price * 100),
                },
                'quantity': item['qty'],
            })

        if not line_items:
            return jsonify({'error': 'Your cart is empty or products are invalid'}), 400

        # 2. Handle Discounts
        applied_discounts = []
        metadata_voucher_id = ""

        if selected_voucher_id:
            voucher = Voucher.query.filter_by(id=selected_voucher_id, customer_id=user_id, is_used=False).first()
            if voucher:
                # --- Minimum Spend Rules ---
                min_spend_required = 0.0
                
                if voucher.discount_amount == 5:
                    min_spend_required = 20.00  # $5 Off needs $20 spend
                elif voucher.discount_amount == 10:
                    min_spend_required = 50.00  # $10 Off needs $50 spend
                elif voucher.discount_amount == 20:
                    min_spend_required = 100.00 # $20 Off needs $100 spend

                # --- The Check ---
                # Check if total is LESS than required (allows exact match)
                if cart_total_amount < min_spend_required:
                    return jsonify({
                        'error': f"The ${int(voucher.discount_amount)} voucher requires a minimum spend of ${min_spend_required:.2f}. (Current: ${cart_total_amount:.2f})"
                    }), 400
                
                # If passed, apply the coupon
                applied_discounts.append({'coupon': voucher.stripe_coupon_id})
                metadata_voucher_id = str(voucher.id)

        elif promo_code:
            promo = PromoCode.query.filter_by(code=promo_code, is_active=True).first()
            if promo:
                applied_discounts.append({'coupon': promo.stripe_coupon_id})

        # 3. Create Session
        customer = Customer.query.get(user_id)
        
        session_params = {
            'payment_method_types': ['card'],
            'line_items': line_items,
            'mode': 'payment',
            'client_reference_id': str(user_id),
            'customer_email': customer.email if customer.email else None,
            'invoice_creation': {'enabled': True},
            'metadata': {
                'user_id': str(user_id),
                'voucher_id': metadata_voucher_id,
                'source': 'website'
            },
            'success_url': 'http://127.0.0.1:5001/orders/success?session_id={CHECKOUT_SESSION_ID}',
            'cancel_url': 'http://127.0.0.1:5001/orders',
        }

        if applied_discounts:
            session_params['discounts'] = applied_discounts
        else:
            session_params['allow_promotion_codes'] = True

        checkout_session = stripe.checkout.Session.create(**session_params)
        return jsonify({'id': checkout_session.id})

    except Exception as e:
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
        from models import db, Customer, Voucher, LoyaltyPoints 
        
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
                if voucher_id_str and voucher_id_str.strip():
                    v = Voucher.query.filter_by(id=int(voucher_id_str), customer_id=customer.id).first()
                    if v: 
                        v.is_used = True
                        print(f"✅ Website Voucher {v.code} marked as used.")

                discounts = full_session.get('total_details', {}).get('breakdown', {}).get('discounts', [])
                for d in discounts:
                    promo_id = d.get('discount', {}).get('promotion_code')
                    if promo_id:
                        promo_obj = stripe.PromotionCode.retrieve(promo_id)
                        applied_code = promo_obj.code
                        v_auto = Voucher.query.filter_by(
                            code=applied_code, customer_id=customer.id, is_used=False
                        ).first()
                        if v_auto:
                            v_auto.is_used = True
                            print(f"✅ WhatsApp/Mobile Voucher {applied_code} marked as used.")

                # 1. SAVE ORDER & STOCK (Returns summary + invoice_url)
                # We need to capture the invoice URL here if we want to pass it to WhatsApp
                # But handle_successful_order saves it to DB. We can grab it from session_data directly.
                invoice_pdf_url = None
                if session_data.get('invoice'):
                    invoice_obj = stripe.Invoice.retrieve(session_data['invoice'])
                    invoice_pdf_url = invoice_obj.invoice_pdf

                items_summary = handle_successful_order(session_data)

                # 2. AWARD POINTS
                total_paid = session_data.get('amount_total', 0) / 100
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
                    # ✅ FIXED: Now safely passes the invoice PDF link
                    send_payment_confirmation(customer.phone, total_paid, items_summary, points_to_add, invoice_pdf=invoice_pdf_url)
                
                db.session.commit()
                
        except Exception as e:
            db.session.rollback()
            print(f"🔥 Webhook Logic Error: {str(e)}")
            return jsonify(success=False), 500

    return jsonify(success=True), 200

# --- 5. ORDER PROCESSING HELPER (Calculations Fixed) ---
def handle_successful_order(session_data):
    items_summary = []
    try:
        from models import db, Customer, WhatsAppOrder, Product
        
        user_id = int(session_data.get('metadata', {}).get('user_id'))
        customer = Customer.query.get(user_id)
        if not customer: return []

        invoice_pdf_url = None
        if session_data.get('invoice'):
            invoice_obj = stripe.Invoice.retrieve(session_data['invoice'])
            invoice_pdf_url = invoice_obj.invoice_pdf 

        line_items = stripe.checkout.Session.list_line_items(session_data['id'], limit=100)

        for item in line_items.data:
            # ✅ CALCULATION IS NOW INSIDE THE LOOP
            price_in_dollars = item.amount_total / 100
            commission = round(price_in_dollars * 0.111, 2)

            new_order = WhatsAppOrder(
                customer_id=customer.id,
                leader_id=customer.leader_id, 
                customer_phone=customer.phone,
                product_name=item.description, 
                quantity=item.quantity,
                total_price=price_in_dollars,
                commission_earned=commission, 
                order_status='Paid',
                invoice_url=invoice_pdf_url, 
                timestamp=datetime.now(pytz.timezone('Asia/Singapore')),
                delivery_address=customer.street_address,       
                delivery_postal=customer.postal_code,    
                delivery_unit=customer.unit_number
            )
            db.session.add(new_order)
            items_summary.append(f"{item.quantity}x {item.description}")

            # Stock Deduction
            try:
                stripe_product = stripe.Product.retrieve(item.price.product)
                db_product_id = stripe_product.metadata.get('product_id')
                
                if db_product_id:
                    product = Product.query.get(int(db_product_id))
                    if product:
                        # Deduct Stock
                        product.available_qty = max(0, product.available_qty - item.quantity)
                        if product.available_qty == 0:
                            product.status = "Out of Stock"

                        # 👇 NEW: STOCK ALERT LOGIC STARTS HERE 👇
                        # -------------------------------------------------
                        LOW_STOCK_THRESHOLD = 5
                        
                        if product.available_qty <= LOW_STOCK_THRESHOLD:
                            print(f"⚠️ Low Stock Detected: {product.name} ({product.available_qty} left)")
                            
                            # 1. Find all Admins
                            admins = Customer.query.filter_by(role='admin').all()
                            
                            # 2. Send WhatsApp to EACH Admin
                            for admin in admins:
                                if admin.phone:
                                    try:
                                        msg = (
                                            f"🚨 *LOW STOCK ALERT* 🚨\n\n"
                                            f"📦 Product: *{product.name}*\n"
                                            f"📉 Remaining: *{product.available_qty}*\n"
                                            f"⚠️ Status: *LOW STOCK*\n\n"
                                            f"Please restock immediately!"
                                        )
                                        # Use the imported helper function
                                        send_whatsapp_message(admin.phone, msg)
                                        print(f"   ✅ Alert sent to admin {admin.name}")
                                    except Exception as e:
                                        print(f"   ❌ Failed to notify admin {admin.name}: {e}")
                        # -------------------------------------------------
                        # 👆 NEW LOGIC ENDS HERE 👆

            except Exception as stock_err:
                print(f"⚠️ Stock deduction failed: {stock_err}")

        return items_summary
    except Exception as e:
        print(f"❌ handle_successful_order error: {e}")
        return []

# --- 6. WHATSAPP SENDER HELPER  ---
def send_payment_confirmation(phone, amount, items, points_earned, invoice_pdf=None):
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

    # ✅ Now this check works because invoice_pdf is a valid variable
    if invoice_pdf:
        message_text += f"\n\n📄 *Download Invoice:*\n{invoice_pdf}"

    try:
        requests.post(url, headers=headers, json={"messaging_product": "whatsapp", "to": clean_phone, "type": "text", "text": {"body": message_text}})
    except Exception as e:
        print(f"❌ WhatsApp Helper Error: {e}")

@orders_bp.route('/success')
def success():
    # Fetch session ID for the receipt page logic
    session_id = request.args.get('session_id')
    if not session_id:
        return render_template('success.html')
        
    try:
        session_details = stripe.checkout.Session.retrieve(session_id)
        line_items = stripe.checkout.Session.list_line_items(session_id, limit=10)
        
        invoice_url = None
        if session_details.invoice:
            inv = stripe.Invoice.retrieve(session_details.invoice)
            invoice_url = inv.invoice_pdf

        return render_template('success.html', 
                               customer_name=session_details.customer_details.name,
                               total_amount=session_details.amount_total / 100,
                               items=line_items.data,
                               invoice_url=invoice_url)
    except:
        return render_template('success.html')