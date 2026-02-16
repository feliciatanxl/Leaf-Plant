from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from models import db, GroupLeader, WhatsAppOrder, Customer, WhatsAppLead, LoyaltyPoints
from datetime import datetime
import pytz
import secrets
from werkzeug.security import generate_password_hash
from whatsapp.app import send_whatsapp_message
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text

leader_bp = Blueprint('leader', __name__)

@leader_bp.route('/leader/dashboard')
def dashboard():
    # 1. Security Checks
    if 'user_id' not in session or session.get('user_role') != 'leader':
        return redirect(url_for('auth.login'))
    
    # 🛑 FIX 1: Get the SPECIFIC leader linked to this login
    leader = GroupLeader.query.filter_by(customer_id=session['user_id']).first()
    
    if not leader: 
        # Fallback if role is 'leader' but no GroupLeader profile exists
        return "Error: No Leader Profile linked to this account.", 404

    # 2. Get Data
    orders = WhatsAppOrder.query.filter_by(leader_id=leader.id).order_by(WhatsAppOrder.timestamp.desc()).all()
    neighbors = Customer.query.filter_by(leader_id=leader.id).all()
    
    # 🛑 FIX 2: Fetch leads assigned specifically to THIS leader by the AI
    leads = WhatsAppLead.query.filter_by(
        leader_id=leader.id, 
        status='Pending Review'
    ).order_by(WhatsAppLead.created_at.desc()).all()

    # 3. Define "Real Money" Statuses
    valid_statuses = ['Confirmed', 'Paid', 'Delivered', 'Received']

    # 4. Calculate Values
    farm_revenue = sum(o.total_price for o in orders if o.order_status in valid_statuses)
    my_earnings = sum(o.commission_earned for o in orders if o.order_status in valid_statuses)
    
    # 5. Calculate Counts
    today = datetime.now(pytz.timezone('Asia/Singapore')).date()
    today_count = sum(1 for o in orders if o.timestamp and o.timestamp.date() == today)
    active_count = sum(1 for o in orders if o.order_status in ['Paid', 'Confirmed', 'Packing', 'Out for Delivery'])

    # 6. Return to HTML
    return render_template('leader.html', 
                           leader=leader, 
                           orders=orders, 
                           neighbors=neighbors, 
                           # 👇 THIS LINE IS THE CRITICAL FIX 👇
                           pending_leads=leads,  # <--- MUST match {% if pending_leads %} in HTML
                           new_leads_count=len(leads), 
                           total_sales=my_earnings, 
                           pending_commission=my_earnings, 
                           today_orders_count=today_count, 
                           active_orders_count=active_count)

@leader_bp.route('/leader/api/update-status', methods=['POST'])
def update_order_status():
    from whatsapp.app import send_whatsapp_message
    import os
    print(f"🛑 I AM RUNNING FROM: {os.path.abspath(__file__)}")

    data = request.get_json()
    order = WhatsAppOrder.query.get(data.get('order_id'))
    new_status = data.get('status')

    if not order: return jsonify({'success': False, 'message': 'Order not found'}), 404

    try:
        original_status = order.order_status
        order.order_status = new_status
        
        # --- THE FIX IS BELOW ---
        if new_status == 'Delivered' and original_status != 'Delivered':
            try:
                # ⬇️ WE ARE WRITING 'order_id' CORRECTLY HERE ⬇️
                review_link = url_for('product.leave_review', order_id=order.id, _external=True)

                msg = f"🥬 *Freshness Check!* \n\nHi! Your order of *{order.product_name}* is delivered. \nRate it here:\n{review_link}"
                send_whatsapp_message(order.customer_phone, msg)
                print(f"✅ Invite sent to {order.customer_phone}")
            except Exception as e:
                print(f"⚠️ WhatsApp Failed: {e}")

        db.session.commit()
        return jsonify({'success': True, 'message': f'Status updated to {new_status}'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    
@leader_bp.route('/leader/claim-lead/<int:lead_id>', methods=['POST'])
def claim_lead(lead_id):
    print(f"\n🚀 --- START CLAIM: Lead ID {lead_id} ---")
    
    # 1. Get the lead
    lead = WhatsAppLead.query.get_or_404(lead_id)
    print(f"📋 Processing Lead: {lead.extracted_name} | Phone: {lead.phone}")

    # 2. Smart Check for Existing User
    clean_lead_phone = ''.join(filter(str.isdigit, str(lead.phone)))
    existing_user = None
    
    for c in Customer.query.all():
        c_clean = ''.join(filter(str.isdigit, str(c.phone)))
        if c_clean == clean_lead_phone or c_clean.endswith(clean_lead_phone[-8:]):
            existing_user = c
            break

    # ======================================================
    # PATH A: USER ALREADY EXISTS
    # ======================================================
    if existing_user:
        print(f"✅ Found Existing User: {existing_user.name}")
        try:
            # FORCE SQL UPDATE
            db.session.execute(
                text("UPDATE whats_app_lead SET status='Converted' WHERE id = :lid"),
                {'lid': lead_id}
            )
            db.session.commit()
            print("✅ SQL Success! Lead status forced to 'Converted'.")
            return jsonify({"success": True, "message": "User linked! Lead cleared."})
        except Exception as e:
            print(f"❌ SQL Failed: {e}")
            return jsonify({"success": False, "message": "Database Lock Error"})

    # ======================================================
    # PATH B: CREATE NEW USER (This is where the fix was needed)
    # ======================================================
    print("👤 User not found. Creating new account...")
    try:
        temp_password = "Leaf" + secrets.token_hex(3).upper()
        
        new_customer = Customer(
            name=lead.extracted_name or "Neighbor",
            phone=lead.phone,
            leader_id=lead.leader_id,
            role='user',
            street_address=lead.neighborhood,
            postal_code=None,
            password_hash=generate_password_hash(temp_password)
        )
        db.session.add(new_customer)
        db.session.flush()
        
        new_loyalty = LoyaltyPoints(customer_id=new_customer.id, current_points=0)
        db.session.add(new_loyalty)
        
        # ---------------------------------------------------------
        # 👇 NUCLEAR FIX APPLIED HERE TOO
        # We force the status update via SQL immediately
        # ---------------------------------------------------------
        db.session.execute(
            text("UPDATE whats_app_lead SET status='Converted' WHERE id = :lid"),
            {'lid': lead_id}
        )
        
        # Notify via WhatsApp
        try:
            login_link = url_for('auth.login', _external=True)
            msg = (
                f"🌿 *Welcome to the Family!*\n"
                f"Your Group Leader has officially added you!\n\n"
                f"🔑 *Login Details:*\n"
                f"Phone: {lead.phone}\n"
                f"Password: {temp_password}\n\n"
                f"Login here: {login_link}"
            )
            send_whatsapp_message(lead.phone, msg)
        except:
            pass

        db.session.commit()
        print("✅ New Account + Status Update Saved via SQL.")
        return jsonify({"success": True, "message": "Account created!"})

    except IntegrityError:
        # ==================================================
        # PATH C: DUPLICATE ERROR (Safety Net)
        # ==================================================
        db.session.rollback()
        print("⚠️ Database Integrity Error (Duplicate). Force cleaning...")
        
        try:
            db.session.execute(
                text("UPDATE whats_app_lead SET status='Converted' WHERE id = :lid"),
                {'lid': lead_id}
            )
            db.session.commit()
            return jsonify({"success": True, "message": "User existed. List cleared."})
        except Exception as sql_e:
            return jsonify({"success": False, "message": str(sql_e)})

    except Exception as e:
        db.session.rollback()
        print(f"❌ General Error: {e}")
        return jsonify({"success": False, "message": str(e)})
    
@leader_bp.route('/leader/reject-lead/<int:lead_id>', methods=['POST'])
def reject_lead(lead_id):
    try:
        lead = WhatsAppLead.query.get_or_404(lead_id)
        
        # Option A: Hard Delete (Removes it completely)
        db.session.delete(lead)
        
        # Option B: Soft Delete (Keeps record but hides it) - Uncomment if preferred
        # lead.status = "Rejected" 
        
        db.session.commit()
        return jsonify({"success": True, "message": "Lead removed."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)})
