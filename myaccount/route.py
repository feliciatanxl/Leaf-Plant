from flask import Blueprint, render_template, request, session, redirect, url_for, flash, abort, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, Customer, WhatsAppOrder, GroupLeader, Voucher, find_leader_by_address 
from itertools import groupby
from operator import attrgetter
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from datetime import datetime, timedelta 
import stripe
import os
from urllib.parse import unquote

myaccount_bp = Blueprint('myaccount', __name__)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

def get_neighborhood_name(postal_code):
    """
    Returns the Estate Name based on the first 2 digits of the SG Postal Code.
    """
    if not postal_code or len(str(postal_code)) < 2:
        return "Singapore"

    sector = str(postal_code)[:2]
    
    sector_map = {
        '01': 'Raffles Place', '02': 'Raffles Place', '03': 'Raffles Place', '04': 'Raffles Place', '05': 'Raffles Place', '06': 'Raffles Place',
        '07': 'Tanjong Pagar', '08': 'Tanjong Pagar', '09': 'Harbourfront', '10': 'Harbourfront',
        '14': 'Bukit Merah', '15': 'Bukit Merah', '16': 'Bukit Merah',
        '17': 'High Street', '18': 'Bugis', '19': 'Bugis', '20': 'Little India', '21': 'Little India',
        '22': 'Orchard', '23': 'Orchard', '24': 'Tanglin', '25': 'Tanglin', '26': 'Tanglin', '27': 'Tanglin',
        '28': 'Novena', '29': 'Novena', '30': 'Novena', '31': 'Toa Payoh', '32': 'Toa Payoh', '33': 'Toa Payoh',
        '34': 'Kallang', '35': 'Kallang', '36': 'Kallang', '37': 'Kallang',
        '38': 'Geylang', '39': 'Geylang', '40': 'Geylang', '41': 'Geylang',
        '42': 'Katong', '43': 'Katong', '44': 'Katong', '45': 'Katong',
        '46': 'Bedok', '47': 'Bedok', '48': 'Bedok', '49': 'Changi', '50': 'Changi', '81': 'Changi',
        '51': 'Pasir Ris', '52': 'Pasir Ris', '53': 'Hougang', '54': 'Sengkang', '55': 'Serangoon',
        '56': 'Ang Mo Kio', '57': 'Ang Mo Kio', '58': 'Bukit Timah', '59': 'Bukit Timah',
        '60': 'Jurong East', '61': 'Jurong West', '62': 'Jurong West', '63': 'Jurong West', '64': 'Jurong West',
        '65': 'Bukit Batok', '66': 'Bukit Batok', '67': 'Bukit Panjang', '68': 'Choa Chu Kang',
        '69': 'Tengah', '70': 'Tuas', '71': 'Tuas', '72': 'Woodlands', '73': 'Woodlands',
        '75': 'Sembawang', '76': 'Yishun', '77': 'Yishun', '78': 'Yishun',
        '79': 'Seletar', '80': 'Seletar', '82': 'Punggol'
    }
    return sector_map.get(sector, "Singapore")

def get_coordinates(postal_code):
    try:
        geolocator = Nominatim(user_agent="my_flask_app")
        location = geolocator.geocode(f"{postal_code}, Singapore", timeout=10)
        if location:
            return [location.latitude, location.longitude]
    except:
        pass
    return None

# ==============================================================================
# MAIN ACCOUNT ROUTE
# ==============================================================================

@myaccount_bp.route('/myaccount') 
def myaccount():
    # 1. Basic session check
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # 2. Database lookup
    user = Customer.query.get(session['user_id'])
    
    # 3. SAFETY CHECK: Handle "Orphaned" sessions (User ID exists in cookie but not DB)
    if not user:
        session.clear() # Clear the invalid cookie
        flash("Please log in again.", "info")
        return redirect(url_for('auth.login'))

    # 4. Fetch Orders & Group Logic
    raw_orders = WhatsAppOrder.query.filter_by(customer_id=user.id)\
        .order_by(WhatsAppOrder.timestamp.desc()).all()

    display_orders = []
    raw_orders.sort(key=lambda x: x.timestamp, reverse=True)
    
    for timestamp, items in groupby(raw_orders, key=attrgetter('timestamp')):
        items_list = list(items)
        first_item = items_list[0]
        group_total = sum(item.total_price for item in items_list)
        order_summary = {
            'display_id': first_item.id,
            'timestamp': timestamp,
            'status': first_item.order_status,
            'total_price': group_total,
            'order_items': items_list 
        }
        display_orders.append(order_summary)

    # 5. Community Goal Logic (Safe neighborhood lookup)
    community_progress = 0
    community_target = 50
    
    # Safety: Only run if postal_code exists
    leader_area_name = "Unknown Area"
    if user.postal_code:
        leader_area_name = get_neighborhood_name(user.postal_code) 

    if user.leader_id:
        leader = GroupLeader.query.get(user.leader_id)
        if leader:
            seven_days_ago = datetime.now() - timedelta(days=7)
            order_count = WhatsAppOrder.query.filter(
                WhatsAppOrder.leader_id == leader.id,
                WhatsAppOrder.timestamp >= seven_days_ago
            ).count()
            community_progress = order_count

    # 6. Rewards & Vouchers logic
    available_vouchers = Voucher.query.filter_by(customer_id=user.id, is_used=False).all()

    return render_template('myaccount.html', 
                            user=user, 
                            orders=display_orders,
                            community_progress=community_progress,
                            community_target=community_target,
                            leader_area_name=leader_area_name,
                            vouchers=available_vouchers)

# ==============================================================================
# VOUCHER & REWARDS LOGIC
# ==============================================================================

@myaccount_bp.route('/claim-voucher', methods=['POST'])
def claim_voucher():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Please log in first"}), 401

    data = request.get_json()
    voucher_type = data.get('type') # e.g., '5_OFF'

    reward_config = {
        '5_OFF': {'pts': 500, 'amt': 5.00},
        '10_OFF': {'pts': 1000, 'amt': 10.00},
        '20_OFF': {'pts': 2000, 'amt': 20.00}
    }

    if voucher_type not in reward_config:
        return jsonify({"success": False, "message": "Invalid voucher"}), 400

    config = reward_config[voucher_type]
    user = Customer.query.get(session['user_id'])

    if not user.loyalty or user.loyalty.current_points < config['pts']:
        return jsonify({"success": False, "message": "Not enough points"}), 400

    try:
        # 1. Set the Campaign Deadline (7 days from now)
        expiry_date = datetime.now() + timedelta(days=7)
        expires_at_timestamp = int(expiry_date.timestamp())

        # 2. Automatically create the Stripe Coupon (The Rule)
        stripe_coupon = stripe.Coupon.create(
            amount_off=int(config['amt'] * 100), # Stripe uses cents
            currency="sgd",
            duration="once",
            name=f"{user.name}'s {int(config['amt'])} Reward"
        )

        # 3. Automatically create the Promo Code (The Trigger)
        unique_code = f"RW-{os.urandom(3).hex().upper()}"
        promo_code = stripe.PromotionCode.create(
            coupon=stripe_coupon.id,
            code=unique_code,
            expires_at=expires_at_timestamp, # 👈 The "Deadline"
            max_redemptions=1                # 👈 Security: Use only once
        )

        # 4. Save to your DB so the user can see it in their wallet
        new_v = Voucher(
            customer_id=user.id,
            code=unique_code,
            discount_amount=config['amt'],
            stripe_coupon_id=stripe_coupon.id,
            is_used=False,
            expiry_date=expiry_date # Make sure you have this column in your Voucher model!
        )
        db.session.add(new_v)

        # 5. Deduct points and commit
        user.loyalty.redeem_points(config['pts'], f"Redeemed {unique_code}")
        db.session.commit()

        return jsonify({"success": True, "message": f"Voucher {unique_code} created!"})

    except Exception as e:
        db.session.rollback()
        print(f"🔥 Campaign Error: {e}")
        return jsonify({"success": False, "message": "Stripe could not create voucher"}), 500

# ==============================================================================
# SETTINGS & PROFILE MANAGEMENT
# ==============================================================================

@myaccount_bp.route('/settings/change_password', methods=['POST'])
def change_password():
    if 'user_id' not in session:
        abort(401)
    user = Customer.query.get(session['user_id'])
    current_pw = request.form.get('currentPassword')
    new_pw = request.form.get('newPassword')
    confirm_pw = request.form.get('confirmPassword')

    if new_pw != confirm_pw:
        flash("New passwords do not match!", "danger")
        return redirect(url_for('myaccount.myaccount'))
    if not check_password_hash(user.password_hash, current_pw):
        flash("Incorrect current password.", "danger")
        return redirect(url_for('myaccount.myaccount'))
    try:
        user.password_hash = generate_password_hash(new_pw)
        db.session.commit()
        flash("Success! Your password has been updated.", "success")
    except Exception as e:
        db.session.rollback()
        flash("A system error occurred. Please try again later.", "danger")
    return redirect(url_for('myaccount.myaccount'))

@myaccount_bp.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        abort(401)
    user = Customer.query.get(session['user_id'])
    new_name = request.form.get('name')
    new_email = request.form.get('email')
    new_phone = request.form.get('phone')
    new_postal = request.form.get('postal_code')
    new_street = request.form.get('street_address')
    new_unit = request.form.get('unit_number')
    
    if new_postal != user.postal_code or new_street != user.street_address:
        new_leader_id = find_leader_by_address(new_postal, new_street)
        if new_leader_id and new_leader_id != user.leader_id:
            user.leader_id = new_leader_id
            flash("Address updated! You have been assigned to a new Group Leader.", "info")
    
    try:
        user.name = new_name
        user.email = new_email
        user.phone = new_phone
        user.postal_code = new_postal
        user.street_address = new_street
        user.unit_number = new_unit
        db.session.commit()
        flash("Profile updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash("Failed to update profile.", "danger")
    return redirect(url_for('myaccount.myaccount', tab='my-profile'))

@myaccount_bp.route('/delete_account', methods=['POST'])
def delete_account():
    # 1. Security Check
    if 'user_id' not in session:
        abort(401)
    
    user = Customer.query.get(session['user_id'])
    
    if not user:
        session.clear()
        return redirect(url_for('auth.login'))

    try:
        # 👇 STEP 1: Delete Loyalty Data
        if user.loyalty:
            # Delete transactions first
            for transaction in user.loyalty.transactions:
                db.session.delete(transaction)
            
            # Delete the loyalty wallet
            db.session.delete(user.loyalty)
            
            # ⚡ CRITICAL FIX: Force the DB to process these deletes NOW
            # This prevents the "0 rows matched" warning later
            db.session.flush() 

        # 👇 STEP 2: Delete Vouchers
        vouchers = Voucher.query.filter_by(customer_id=user.id).all()
        for v in vouchers:
            db.session.delete(v)
            
        # ⚡ Force flush again to be safe
        db.session.flush()

        # 👇 STEP 3: Delete the User
        db.session.delete(user)
        
        # Commit everything permanently
        db.session.commit()
        
        # 4. Success!
        session.clear()
        flash("Your account has been deleted.", "info")
        return redirect(url_for('account')) 
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Delete Account Error: {e}")
        flash("Could not delete account. Please contact support.", "danger")
        return redirect(url_for('myaccount.myaccount'))
    
# ==============================================================================
# ORDER API (Clean Integer Version)
# ==============================================================================

@myaccount_bp.route('/myaccount/api/order-details/<int:order_id>')
def get_order_details(order_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    # 1. Query by ID directly (No strings, no decoding needed)
    first_item = WhatsAppOrder.query.get(order_id)
    
    if not first_item or first_item.customer_id != session['user_id']:
        return jsonify({'error': 'Order not found'}), 404

    # 2. Fetch the group using timestamp
    # (Since your DB stores items individually, we group by the exact time they were bought)
    batch_items = WhatsAppOrder.query.filter_by(
        customer_id=session['user_id'], 
        timestamp=first_item.timestamp
    ).all()

    total_price = sum(item.total_price for item in batch_items)
    items_data = [{'name': i.product_name, 'qty': i.quantity, 'price': i.total_price} for i in batch_items]

    status_map = {
        'Paid': 1, 'Confirmed': 1, 'Harvesting': 2, 
        'Packing': 3, 'Out for Delivery': 4, 'Delivered': 5
    }
    
    return jsonify({
        'id': f"#{first_item.id}", 
        'date': first_item.timestamp.strftime('%d %b %Y, %I:%M %p'),
        'status': first_item.order_status,
        'current_step': status_map.get(first_item.order_status, 1),
        'total': total_price,
        'items': items_data,
        'farm_coords': [1.4173, 103.7255], 
        'user_coords': get_coordinates(session.get('user_postal', '')) or [1.3521, 103.8198],
        'invoice_url': first_item.invoice_url
    })