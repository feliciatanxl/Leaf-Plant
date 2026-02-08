import os

# The exact path you saw in your terminal
file_path = r"C:\Users\legof\Downloads\WDP-WS-AI-2025\leader\route.py"

# The CORRECT code (verified clean)
new_content = """from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from models import db, GroupLeader, WhatsAppOrder, Customer, WhatsAppLead
from datetime import datetime
import pytz

leader_bp = Blueprint('leader', __name__)

@leader_bp.route('/leader/dashboard')
def dashboard():
    if 'user_id' not in session or session.get('user_role') != 'leader':
        return redirect(url_for('auth.login'))
    
    leader = GroupLeader.query.first()
    if not leader: return "Leader profile not found", 404

    orders = WhatsAppOrder.query.filter_by(leader_id=leader.id).order_by(WhatsAppOrder.timestamp.desc()).all()
    neighbors = Customer.query.filter_by(leader_id=leader.id).all()
    leads = WhatsAppLead.query.filter_by(status='Awaiting Assignment').order_by(WhatsAppLead.created_at.desc()).all()

    total_sales = sum(o.total_price for o in orders if o.order_status in ['Confirmed', 'Paid', 'Delivered', 'Received'])
    pending_commission = total_sales * 0.111
    
    today = datetime.now(pytz.timezone('Asia/Singapore')).date()
    today_count = sum(1 for o in orders if o.timestamp and o.timestamp.date() == today)
    active_count = sum(1 for o in orders if o.order_status in ['Paid', 'Confirmed', 'Packing', 'Out for Delivery'])

    return render_template('leader.html', leader=leader, orders=orders, neighbors=neighbors, leads=leads, 
                           new_leads_count=len(leads), total_sales=total_sales, 
                           pending_commission=pending_commission, today_orders_count=today_count, 
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
                review_link = url_for('products.leave_review', order_id=order.id, _external=True)

                msg = f"🥬 *Freshness Check!* \\n\\nHi! Your order of *{order.product_name}* is delivered. \\nRate it here:\\n{review_link}"
                send_whatsapp_message(order.customer_phone, msg)
                print(f"✅ Invite sent to {order.customer_phone}")
            except Exception as e:
                print(f"⚠️ WhatsApp Failed: {e}")

        db.session.commit()
        return jsonify({'success': True, 'message': f'Status updated to {new_status}'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
"""

# Force write the file
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(new_content)

print("✅ SUCCESS: The file 'leader/route.py' has been forcibly overwritten with the correct code.")