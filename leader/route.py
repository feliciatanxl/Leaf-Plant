from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from models import db, GroupLeader, WhatsAppOrder, Customer, WhatsAppLead
from datetime import datetime
import pytz

leader_bp = Blueprint('leader', __name__)

@leader_bp.route('/leader/dashboard')
def dashboard():
    # 1. Security Checks
    if 'user_id' not in session or session.get('user_role') != 'leader':
        return redirect(url_for('auth.login'))
    
    leader = GroupLeader.query.first()
    if not leader: return "Leader profile not found", 404

    # 2. Get Data
    orders = WhatsAppOrder.query.filter_by(leader_id=leader.id).order_by(WhatsAppOrder.timestamp.desc()).all()
    neighbors = Customer.query.filter_by(leader_id=leader.id).all()
    leads = WhatsAppLead.query.filter_by(status='Awaiting Assignment').order_by(WhatsAppLead.created_at.desc()).all()

    # 3. Define "Real Money" Statuses
    valid_statuses = ['Confirmed', 'Paid', 'Delivered', 'Received']

    # 4. Calculate Values
    # Revenue = Total money the farm collected
    farm_revenue = sum(o.total_price for o in orders if o.order_status in valid_statuses)

    # Earnings = Sum of the 'commission_earned' column from your database
    # (This fixes the 0.111 math issue and uses the exact saved value)
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
                           leads=leads, 
                           new_leads_count=len(leads), 
                           
                           # 👇 TRICK: We pass 'my_earnings' to the 'total_sales' slot.
                           # This makes the dashboard display YOUR COMMISSION as the main number.
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
