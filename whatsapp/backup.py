# ==============================================================================
# 0. PATH
# ==============================================================================
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ==============================================================================
# 1. Standard Imports 
# ==============================================================================
import requests
import re
import io
from datetime import datetime
from flask import Flask, request, jsonify
from openai import OpenAI
from dotenv import load_dotenv
import pytz
from models import db, ContactInquiry, Product, Customer, WhatsAppOrder, WhatsAppLead, StockAlert, set_sqlite_pragma, GroupLeader
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import event

# ==============================================================================
# 2. Configuration & Security 
# ==============================================================================
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env')) 

YOUR_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID") 
YOUR_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN") 
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 

app = Flask(__name__)

# --- DATABASE CONFIGURATION ---
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, '..', 'leafplant.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

processed_messages = set()
conversation_history = {} 
pending_alerts_dict = {}  # Stores which product the user wants an alert for

try:
    client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    client = None

# ==============================================================================
# 3. Database Helper Logic 
# ==============================================================================
def get_inventory_string():
    db.session.expire_all() 
    products = Product.query.all()
    if not products: return "No stock data available."
    output = "CURRENT FARM INVENTORY:\n"
    for p in products:
        status_label = "AVAILABLE" if (p.status == "In Stock" and p.available_qty > 0) else "SOLD OUT"
        output += f"- {p.name}: ${p.price} | {p.available_qty} units ({status_label})\n"
    return output

def deduct_stock_db(product_name, qty_to_deduct):
    product = Product.query.filter(Product.name.ilike(f"%{product_name}%")).first()
    if product and product.available_qty >= int(qty_to_deduct):
        product.available_qty -= int(qty_to_deduct)
        product.status = "Out of Stock" if product.available_qty <= 0 else "In Stock"
        flag_modified(product, "status")
        db.session.commit() 
        return True
    return False

# ==============================================================================
# 4. Outgoing Message Helper
# ==============================================================================
def send_whatsapp_message(to_phone, message_text):
    url = f"https://graph.facebook.com/v24.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {YOUR_ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": str(to_phone),
        "type": "text",
        "text": {"body": message_text}
    }
    try:
        response = requests.post(url, headers=headers, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Error sending WhatsApp: {e}")
        return False

# ==============================================================================
# 5. New Prospect Handling
# ==============================================================================
def handle_new_prospect(customer_number, customer_message, history):
    sg_now = datetime.now(pytz.timezone('Asia/Singapore')).strftime("%Y-%m-%d %H:%M:%S")
    leader = GroupLeader.query.first()
    leader_info = f"{leader.name} (+{str(leader.phone).split('.')[0]})" if leader else "a local delivery representative"

    system_prompt = f"""
    You are 'Leaf Plant Onboarding AI'. Time: {sg_now}. 
    GOAL: Collect NAME and NEIGHBORHOOD. 
    INSTRUCTIONS:
    1. Ask for name and neighborhood.
    2. Inform them their Leader ({leader_info}) will verify them.
    3. State: "Orders can only be placed after registration."
    [[NAME: user_name]] [[ADDRESS: neighborhood]]
    """
    messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": customer_message}]
    try:
        completion = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        ai_reply = completion.choices[0].message.content
        name_match = re.search(r"\[\[NAME:\s*(.*?)\]\]", ai_reply)
        addr_match = re.search(r"\[\[ADDRESS:\s*(.*?)\]\]", ai_reply)
        lead = WhatsAppLead.query.filter_by(phone=customer_number).first()
        if not lead:
            lead = WhatsAppLead(phone=customer_number, extracted_name="New Prospect", neighborhood="Pending")
            db.session.add(lead)
        if name_match: lead.extracted_name = name_match.group(1).strip()
        if addr_match: lead.neighborhood = addr_match.group(1).strip()
        db.session.commit()
        return ai_reply.split('[[')[0].strip()
    except Exception:
        db.session.rollback()
        return "Welcome! May I have your name and neighborhood to register you?"

# ==============================================================================
# 6. AI Sales Engine 
# ==============================================================================
def get_openai_response(customer_message, customer_number, customer_obj):
    if not client: return "AI Offline."

    db.session.expire_all() 
    stock_list = get_inventory_string()
    user_input_low = customer_message.lower().strip()

    # --- USER-CENTRIC FLOW: QUERY PURCHASE HISTORY ---
    past_orders = WhatsAppOrder.query.filter_by(customer_id=customer_obj.id).order_by(WhatsAppOrder.timestamp.desc()).limit(3).all()
    if past_orders:
        history_text = "\n".join([f"- {o.product_name} ({o.quantity} units) on {o.timestamp.strftime('%d %b')}" for o in past_orders])
        user_profile_context = f"CUSTOMER PROFILE: {customer_obj.name} has bought:\n{history_text}"
    else:
        user_profile_context = f"CUSTOMER PROFILE: {customer_obj.name} has no previous orders yet."

    # --- 1. STOCK ALERT CONFIRMATION LOGIC ---
    affirmative_words = ["yes", "ok", "alert", "notify", "sure", "want", "yep", "please", "confirm"]
    if any(word in user_input_low for word in affirmative_words):
        product_name = pending_alerts_dict.get(customer_number)
        if product_name:
            try:
                new_alert = StockAlert(customer_phone=str(customer_number), product_name=product_name, is_notified=False)
                db.session.add(new_alert)
                db.session.commit()
                pending_alerts_dict.pop(customer_number, None)
                return (f"Done! ✅ I've added you to the list for *{product_name}*. 🌿\n\n"
                        "I'll message you here the moment it's back in stock! 😊")
            except Exception:
                db.session.rollback()

    # --- 2. DETECT MENTIONED PRODUCT & OOS CHECK ---
    all_products = Product.query.all()
    mentioned_product = None
    for p in all_products:
        if p.name.lower() in user_input_low:
            mentioned_product = p
            break

    if mentioned_product:
        if mentioned_product.available_qty <= 0 or mentioned_product.status == "Out of Stock":
            pending_alerts_dict[customer_number] = mentioned_product.name
            return (f"Oh, I'm so sorry, {customer_obj.name}! 🌱 *{mentioned_product.name}* is sold out. 😕\n\n"
                    f"Would you like me to set a *Stock Alert* and notify you the second it's back? Just say *YES*! 🌿")

    # --- 3. RESTOCK CONTEXT OVERRIDE ---
    recent_notif = StockAlert.query.filter_by(customer_phone=str(customer_number), is_notified=True).order_by(StockAlert.id.desc()).first()
    restock_override = ""
    if recent_notif:
        p_check = Product.query.filter_by(name=recent_notif.product_name).first()
        if p_check and p_check.available_qty > 0:
            restock_override = f"🚨 SYSTEM ALERT: {p_check.name} is NOW IN STOCK. Ignore history saying OOS."

    # --- 4. HUMAN THANKS ---
    thanks_words = ["thank you", "thanks", "thx", "received", "noted"]
    if user_input_low in thanks_words:
        return f"You're so welcome, {customer_obj.name}! 😊 Have a wonderful day ahead! 🌿"

    # --- 5. AI GENERATION ---
    history = conversation_history.get(customer_number, [])
    is_already_finalized = any("ORDER CONFIRMED" in m["content"] for m in history[-2:])
    
    leader_name = customer_obj.leader.name if customer_obj.leader else "Test Leader"
    leader_phone = str(customer_obj.leader.phone).split(".")[0] if customer_obj.leader else "6500000000"
    sg_now = datetime.now(pytz.timezone("Asia/Singapore")).strftime("%A, %d %B %Y")

    system_prompt = f"""
    You are 'Leaf Plant Sales AI'. Today: {sg_now}. Customer: {customer_obj.name}.
    {user_profile_context}
    LIVE INVENTORY: {stock_list}
    {restock_override}

    TONE: Neighborly farm assistant.
    
    STEP 1: ORDER SUMMARY FORMAT (When user asks for items but has NOT confirmed yet):
    ✅ Order Summary
    
    Great choice! You’re ordering [Qty] units of [Item] 🌱
    Here’s the breakdown:
    
    🛒 Item Details
    • [Item] — [Qty] units × $[Price] = $[LineTotal]
    
    💰 Subtotal: $[GrandTotal]
    
    Would you like me to proceed with this order? 🌟

    STEP 2: FINAL CONFIRMATION:
    Once user says yes/agrees, output ONLY:
    [[STATUS: CONFIRMED]]
    [[DATA: ItemName | Qty | TotalPrice]]
    """

    messages = [{"role": "system", "content": system_prompt}, *history[-6:], {"role": "user", "content": customer_message}]

    try:
        completion = client.chat.completions.create(model="gpt-4o-mini", messages=messages)
        ai_reply = completion.choices[0].message.content
        is_ai_confirmed = "[[STATUS: CONFIRMED]]" in ai_reply
        clean_reply = re.sub(r"\[\[DATA:.*?\]\]", "", ai_reply)
        clean_reply = re.sub(r"\[\[STATUS:.*?\]\]", "", clean_reply).strip()

        # --- 6. ORDER PROCESSING ---
        order_matches = re.findall(r"\[\[DATA:\s*(.*?)\s*\]\]", ai_reply)
        if order_matches and is_ai_confirmed and not is_already_finalized:
            order_summary_text, grand_total = "", 0.0
            for match in order_matches:
                parts = [p.strip() for p in match.split("|")]
                if len(parts) != 3: continue 
                item_name, qty, total_price = parts[0], int(parts[1]), float(parts[2])
                
                if deduct_stock_db(item_name, qty):
                    db.session.add(WhatsAppOrder(
                        customer_id=customer_obj.id, leader_id=customer_obj.leader_id,
                        customer_phone=str(customer_number), product_name=item_name,
                        quantity=qty, total_price=total_price,
                        commission_earned=total_price * 0.111, order_status="Confirmed"
                    ))
                    order_summary_text += f"• {item_name} × {qty} units — ${total_price:.2f}\n"
                    grand_total += total_price

            if grand_total > 0:
                db.session.commit()
                return (f"✨ ORDER CONFIRMED ✨\n\n"
                        f"Hi {customer_obj.name}! 🌟\n"
                        f"Thank you for your order — it’s been successfully secured!\n\n"
                        f"🛒 Order Details\n"
                        f"{order_summary_text}\n"
                        f"💰 Total Amount: ${grand_total:.2f}\n\n"
                        f"🚚 Delivery Method:\n"
                        f"{leader_name}\n"
                        f"📞 +{leader_phone}\n\n"
                        f"We appreciate your support and look forward to serving you again! 😊🌱")

        return clean_reply
    except Exception as e:
        db.session.rollback()
        print(f"❌ CRITICAL ERROR IN ORDER PROCESSING: {e}") # This will tell you exactly what's wrong in your terminal
        return "I'm just refreshing my notes, one moment! 😊"

# ==============================================================================
# 7. Webhook Handling
# ==============================================================================
@app.route('/webhook', methods=['POST'])
def handle_message():
    data = request.get_json()
    value = data.get('entry', [{}])[0].get('changes', [{}])[0].get('value', {})
    if 'messages' not in value: return jsonify({"status": "ignored"}), 200
    try:
        msg = value['messages'][0]
        customer_number, msg_id = msg['from'], msg['id']
        if msg.get('type') != 'text': return jsonify({"status": "non_text"}), 200
        customer_message = msg['text']['body']
        if msg_id in processed_messages: return jsonify({"status": "duplicate"}), 200
        processed_messages.add(msg_id) 

        db.session.expire_all() 
        customer = Customer.query.filter_by(phone=customer_number).first()
        if customer:
            reply = get_openai_response(customer_message, customer_number, customer)
        else:
            reply = handle_new_prospect(customer_number, customer_message, conversation_history.get(customer_number, [])[-4:])

        if customer_number not in conversation_history: conversation_history[customer_number] = []
        conversation_history[customer_number].append({"role": "user", "content": customer_message})
        conversation_history[customer_number].append({"role": "assistant", "content": reply})
        send_whatsapp_message(customer_number, reply)
        return jsonify({"status": "ok"}), 200 
    except Exception: return jsonify({"status": "error"}), 200

if __name__ == '__main__':
    with app.app_context():
        event.listen(db.engine, "connect", set_sqlite_pragma)
        db.create_all() 
    app.run(port=5000, debug=True)