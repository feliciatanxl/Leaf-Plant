from flask import Blueprint, render_template, redirect, url_for, request, session, flash, jsonify, abort
from models import db, ContactInquiry, Product, Category, StockAlert, Customer, WhatsAppOrder, GroupLeader, PromoCode
from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified
from datetime import datetime, timedelta
from firebase_admin import storage
import calendar
import pytz
import os
import json
import shutil
import re
import stripe
from openai import OpenAI
from dotenv import load_dotenv
import uuid


stripe.api_version = '2023-10-16'

# Load environment variables
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# Initialize OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)

# Import WhatsApp Helper
try:
    from whatsapp.app import send_whatsapp_message
except ImportError:
    def send_whatsapp_message(to, msg): 
        print(f"⚠️ Simulation Mode (WhatsApp not configured). To: {to} | Msg: {msg}")
        return True

admin_bp = Blueprint('admin', __name__)

# --- ACCESS CONTROL ---
@admin_bp.before_request
def check_admin_access():
    if request.endpoint and 'admin' in request.endpoint:
        if 'user_id' not in session:
            abort(401)
        if session.get('user_role') != 'admin':
            abort(403)

# --- DASHBOARD ROUTE ---
@admin_bp.route('/dashboard')
def dashboard():
    # Inquiry Logic
    max_id_query = db.session.query(func.max(ContactInquiry.id)).scalar()
    current_real_max_id = int(max_id_query) if max_id_query else 0
    
    if 'visible_threshold_id' not in session:
        session['visible_threshold_id'] = current_real_max_id
        session['last_seen_id'] = current_real_max_id

    if request.args.get('refresh') == 'true':
        session['last_seen_id'] = int(session['visible_threshold_id'])
        session['visible_threshold_id'] = current_real_max_id
    
    visible_threshold = int(session['visible_threshold_id'])
    inquiries = ContactInquiry.query.filter(ContactInquiry.id <= visible_threshold).order_by(ContactInquiry.created_at.desc()).all()
    last_seen_id = int(session.get('last_seen_id', 0))

    # General Data Fetching
    products = Product.query.order_by(Product.id.desc()).all()
    categories = Category.query.all()
    active_alerts_count = StockAlert.query.filter_by(is_notified=False).count()
    total_orders_count = WhatsAppOrder.query.count()
    total_sales_value = db.session.query(func.sum(WhatsAppOrder.total_price)).scalar() or 0.0
    total_commissions = db.session.query(func.sum(WhatsAppOrder.commission_earned)).scalar() or 0.0
    group_leaders = GroupLeader.query.all()
    customers = Customer.query.order_by(Customer.id.desc()).all()
    
    # Fetch Promo Codes for the Dashboard Table
    promos = PromoCode.query.order_by(PromoCode.created_at.desc()).all()
    
    # Counts
    admin_count = Customer.query.filter_by(role='admin').count()
    user_count = Customer.query.filter_by(role='user').count()
    leader_count = Customer.query.filter_by(role='leader').count()
    
    total_profit_value = total_sales_value * 0.30
    sgt = pytz.timezone('Asia/Singapore')
    sync_time = datetime.now(sgt).strftime("%d %b, %H:%M:%S")

    return render_template('admin.html', 
                           inquiries=inquiries, 
                           products=products,
                           categories=categories,
                           last_seen_id=last_seen_id,
                           active_alerts_count=active_alerts_count,
                           total_orders_count=total_orders_count,
                           total_sales_value=total_sales_value,
                           total_profit_value=total_profit_value,
                           total_commissions=total_commissions,
                           group_leaders=group_leaders,
                           customers=customers,
                           promos=promos,
                           admin_count=admin_count,
                           user_count=user_count,
                           leader_count=leader_count,
                           sync_time=sync_time)

# --- LIVE SYNC API ---
@admin_bp.route('/api/products')
def get_products_api():
    products = Product.query.all()
    active_alerts_count = StockAlert.query.filter_by(is_notified=False).count()
    total_orders_count = WhatsAppOrder.query.count()
    total_sales_value = db.session.query(func.sum(WhatsAppOrder.total_price)).scalar() or 0.0
    total_commissions = db.session.query(func.sum(WhatsAppOrder.commission_earned)).scalar() or 0.0
    admin_count = Customer.query.filter_by(role='admin').count()
    user_count = Customer.query.filter_by(role='user').count()
    active_coupons_count = PromoCode.query.filter_by(is_active=True).count()
    total_profit_value = total_sales_value * 0.30
    sgt = pytz.timezone('Asia/Singapore')
    sync_time = datetime.now(sgt).strftime("%d %b, %H:%M:%S")
    
    return jsonify({
        "products": [{'id': p.id, 'available_qty': p.available_qty, 'status': p.status} for p in products],
        "stats": {
            "active_alerts_count": active_alerts_count,
            "total_orders_count": total_orders_count,
            "total_sales_value": f"{total_sales_value:,.2f}",
            "total_commissions": f"{total_commissions:,.2f}",
            "total_profit_value": f"{total_profit_value:,.2f}",
            "admin_count": admin_count,
            "user_count": user_count,
            "active_coupons_count": active_coupons_count,
            "sync_time": sync_time
        }
    })

@admin_bp.route('/api/validate-promo', methods=['POST'])
def validate_promo():
    data = request.get_json()
    user_code = data.get('code', '').strip().upper()
    promo = PromoCode.query.filter_by(code=user_code, is_active=True).first()
    
    if promo:
        return jsonify({
            "valid": True,
            "discount": promo.discount_percent,
            "message": f"Promo code '{user_code}' applied successfully!"
        })
    return jsonify({"valid": False, "message": "Invalid code."}), 400

# --- PRODUCT MANAGEMENT ---
@admin_bp.route("/products/add", methods=['POST'])
def add_product():
    try:
        # 1. Handle the Image Upload first
        image_file = request.files.get('image_file')
        firebase_url = 'default_product.jpg' 

        if image_file and image_file.filename != '':
            bucket = storage.bucket()
            ext = image_file.filename.split('.')[-1]
            unique_filename = f"products/{uuid.uuid4()}.{ext}"
            
            blob = bucket.blob(unique_filename)
            blob.upload_from_string(
                image_file.read(),
                content_type=image_file.content_type
            )
            blob.make_public()
            firebase_url = blob.public_url

        # 2. Create the Product with the new URL
        stock_val = int(request.form.get('stock', 0))
        new_product = Product(
            name=request.form.get('name'),
            description=request.form.get('description'),
            available_qty=stock_val,
            price=float(request.form.get('price', 0.0)),
            category=request.form.get('category', 'leafy'),
            status="In Stock" if stock_val > 0 else "Out of Stock",
            image_file=firebase_url 
        )
        
        db.session.add(new_product)
        db.session.commit()
        
    except Exception as e:
        db.session.rollback()
        print(f"Error: {e}")
        
    return redirect(url_for('admin.dashboard', tab='products'))

@admin_bp.route("/products/edit/<int:id>", methods=['POST'])
def edit_product(id):
    product = Product.query.get_or_404(id)
    try:
        new_qty = int(request.form.get('stock', 0))
        old_status = product.status
        
        product.name = request.form.get('name')
        product.description = request.form.get('description')
        product.available_qty = new_qty
        product.price = float(request.form.get('price', 0.0))
        product.category = request.form.get('category')

        # --- NEW FIREBASE IMAGE LOGIC START ---
        file = request.files.get('image_file')
        if file and file.filename != '':
            # 1. DELETE OLD CLOUD IMAGE
            if product.image_file and "storage.googleapis.com" in product.image_file:
                try:
                    # Extract filename from the end of the URL
                    old_filename = product.image_file.split('/')[-1]
                    bucket = storage.bucket()
                    bucket.blob(f"products/{old_filename}").delete()
                except Exception as e:
                    print(f"Cleanup of old image failed: {e}")

            # 2. UPLOAD NEW IMAGE
            unique_filename = f"{uuid.uuid4()}_{file.filename}"
            bucket = storage.bucket()
            blob = bucket.blob(f"products/{unique_filename}")
            
            # Reset file pointer and upload
            file.seek(0) 
            blob.upload_from_string(file.read(), content_type=file.content_type)
            blob.make_public()
            
            # Save the new public URL to the DB
            product.image_file = blob.public_url
        # --- NEW FIREBASE IMAGE LOGIC END ---

        # Status logic remains the same
        if new_qty <= 0:
            product.status = "Out of Stock"
        elif new_qty > 0 and old_status == "Out of Stock":
            product.status = "In Stock"
        else:
            product.status = request.form.get('status')

        # WhatsApp alert logic remains the same
        if product.status == "In Stock" and old_status == "Out of Stock":
            pending_alerts = StockAlert.query.filter_by(product_name=product.name, is_notified=False).all()
            for alert in pending_alerts:
                if send_whatsapp_message(alert.customer_phone, f"🌿 *Good News!*\n*{product.name}* is back in stock!"):
                    alert.is_notified = True
                    alert.notified_at = datetime.now(pytz.timezone('Asia/Singapore'))

        flag_modified(product, "status")
        db.session.commit()
        flash("Product updated successfully!", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {e}", "danger")
        
    return redirect(url_for('admin.dashboard', tab='products'))

@admin_bp.route("/products/delete/<int:id>", methods=['POST'])
def delete_product(id):
    product = Product.query.get_or_404(id)
    try:
        # 1. FIREBASE CLEANUP
        # Check if the path is a cloud URL (prevents trying to delete local static files)
        if product.image_file and "storage.googleapis.com" in product.image_file:
            try:
                # Extract the filename from the end of the URL
                # Example: .../products/uuid_name.jpg -> products/uuid_name.jpg
                filename = product.image_file.split('/')[-1]
                bucket = storage.bucket()
                blob = bucket.blob(f"products/{filename}")
                
                if blob.exists():
                    blob.delete()
                    print(f"Cloud image {filename} deleted.")
            except Exception as e:
                # We print the error but continue so the DB record still gets deleted
                print(f"Firebase delete error: {e}")

        # 2. DATABASE DELETE
        db.session.delete(product)
        db.session.commit()
        flash("Product and image removed successfully.", "success")
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {e}", "danger")
        
    return redirect(url_for('admin.dashboard', tab='products'))

# --- CATEGORY MANAGEMENT ---
@admin_bp.route("/categories/add", methods=['POST'])
def add_category():
    cat_name = request.form.get('cat_name')
    if cat_name:
        slug = cat_name.lower().strip().replace(" ", "-")
        if not Category.query.filter_by(slug=slug).first():
            db.session.add(Category(name=cat_name, slug=slug))
            db.session.commit()
    return redirect(url_for('admin.dashboard', tab='products'))
# EDIT CATEGORY
@admin_bp.route("/categories/edit/<int:id>", methods=['POST'])
def edit_category(id):
    category = Category.query.get_or_404(id)
    new_name = request.form.get('cat_name')
    if new_name:
        category.name = new_name
        category.slug = new_name.lower().strip().replace(" ", "-")
        db.session.commit()
    return redirect(url_for('admin.dashboard', tab='products'))

@admin_bp.route("/categories/delete/<int:id>", methods=['POST'])
def delete_category(id):
    category = Category.query.get_or_404(id)
    db.session.delete(category)
    db.session.commit()
    return redirect(url_for('admin.dashboard',tab='products'))

#ai description: add product
@admin_bp.route('/generate-description', methods=['POST'])
def generate_description():
    data = request.get_json()
    name = data.get('name')
    category = data.get('category')

    prompt = (
        f"You are a professional copywriter for 'Leaf Plant', an organic grocery store. "
        f"Write a short, appetizing, and engaging product description (2-3 sentences) "
        f"for a product named '{name}' in the '{category}' category. "
        f"Include a tip on taste or how to use it. Do not use emojis."
    )

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt}],
            temperature=0.7,
            max_tokens=150
        )
        ai_description = completion.choices[0].message.content.strip()
        return jsonify({"description": ai_description})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- MARKETING CAMPAIGN ENGINE ---
@admin_bp.route('/campaign/analyze', methods=['POST'])
def analyze_campaign():
    data = request.get_json()
    target_p_id = data.get('product_id')
    campaign_type = data.get('campaign_type', 'general')
    custom_goal = data.get('custom_goal', '')
    
    # 1. CALCULATE END OF CURRENT MONTH (For text context only)
    now = datetime.now()
    last_day = calendar.monthrange(now.year, now.month)[1]
    expiry_str = datetime(now.year, now.month, last_day).strftime("%d %B %Y")

    # 2. GET EXISTING CODES (To prevent duplicates)
    # We fetch all codes to tell OpenAI what NOT to use.
    existing_codes = [c.code for c in PromoCode.query.with_entities(PromoCode.code).all()]
    exclusion_list = ", ".join(existing_codes)

    # 3. FETCH PRODUCT CONTEXT
    all_products = Product.query.all()
    if target_p_id == 'all':
        base_prompt = f"You are a marketing manager for Leaf Plant. Sending broadcast to ALL customers."
        product_name = "our store"
    else:
        target_product = Product.query.get(target_p_id)
        if not target_product: return jsonify({"error": "Product not found"}), 404
        base_prompt = f"You are a friendly manager for Leaf Plant. Targeting buyers of '{target_product.name}'."
        product_name = target_product.name

    # 4. DEFINE CAMPAIGN INSTRUCTIONS (Business Logic Layer)
    if campaign_type == 'custom':
        # Let the user decide via their custom input
        instruction = f"{base_prompt} Goal: {custom_goal}. Mention offer ends {expiry_str}."
    
    elif campaign_type == 'surplus':
        # Surplus = High Supply -> NEED Discount to clear stock
        instruction = f"{base_prompt} Task: Flash Deal 20% Off {product_name}. Suggest a unique promo_code. Expires {expiry_str}."
    
    elif campaign_type == 'recipe':
        # Recipe = Engagement -> NO Discount needed
        instruction = f"{base_prompt} Task: Share a delicious recipe idea using {product_name}. Focus on taste and health. Do NOT offer a discount code. Return promo_code as null."
    
    elif campaign_type == 'restock':
        # Restock = High Demand -> NO Discount needed (Scarcity)
        instruction = f"{base_prompt} Task: Announce that {product_name} is back in stock! Create urgency (limited stock). Do NOT offer a discount code. Return promo_code as null."
        
    else:
        # General Broadcast -> Optional Discount
        instruction = f"{base_prompt} Task: Warm message about {product_name}. Only offer a discount if it feels necessary for a general greeting."

    # 5. SYSTEM PROMPT (With Exclusion List)
    system_instruction = (
        f"{instruction} "
        f"IMPORTANT: Do NOT use these existing codes: [{exclusion_list}]. "
        "If you generate a promo_code, you MUST explicitly write it inside the 'message' text (e.g., 'Use code SAVE20'). "
        "Return JSON: {'triggers': string, 'message': string, 'promo_code': string_or_null, 'discount_percent': number_or_0}"
    )
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_instruction}],
            response_format={ "type": "json_object" },
            temperature=0.3 
        )
        ai_data = json.loads(completion.choices[0].message.content)
        
        # 🛑 STOP! We do NOT save to DB here anymore.
        # We just return the suggestion to the frontend.
        
        return jsonify(ai_data)

    except Exception as e:
        print(f"AI Error: {e}")
        return jsonify({"error": str(e)}), 500


@admin_bp.route('/campaign/send', methods=['POST'])
def launch_campaign():
    data = request.get_json()
    product_id = data.get('product_id') 
    final_message = data.get('message')
    
    # Data from Frontend
    promo_code = data.get('promo_code')
    discount_percent = data.get('discount_percent')
    
    # 1. HANDLE PROMO CODE CREATION
    if promo_code and discount_percent:
        clean_code = promo_code.upper().strip()
        existing = PromoCode.query.filter_by(code=clean_code).first()
        
        if not existing:
            try:
                # A. Calculate Expiry
                now = datetime.now()
                last_day = calendar.monthrange(now.year, now.month)[1]
                expiry_date = datetime(now.year, now.month, last_day, 23, 59, 59)
                redeem_by_ts = int(expiry_date.timestamp())

                # B. Create Stripe Coupon (The Math)
                stripe_coupon = stripe.Coupon.create(
                    percent_off=int(discount_percent),
                    duration='once',
                    name=f"{discount_percent}% Off ({clean_code})",
                    redeem_by=redeem_by_ts
                )

                # 👇 C. Create Stripe Promotion Code (THE MISSING "PASSWORD") 👇
                stripe.PromotionCode.create(
                    coupon=stripe_coupon.id,
                    code=clean_code
                )
                
                # D. Create in DB
                new_promo = PromoCode(
                    code=clean_code,
                    stripe_coupon_id=stripe_coupon.id,
                    discount_percent=int(discount_percent),
                    is_active=True,
                    created_at=datetime.utcnow(),
                    expires_at=expiry_date
                )
                db.session.add(new_promo)
                db.session.commit()
                print(f"✅ Auto-Generated Code {clean_code} LIVE in Stripe & DB!")

            except Exception as e:
                print(f"⚠️ Promo Creation Failed: {e}")
                # We continue sending even if promo creation fails to ensure message goes out
    
    # 2. SEND MESSAGES
    cohort = []
    if product_id == 'all':
        cohort = Customer.query.filter(Customer.phone != None, Customer.role == 'user').all()
    else:
        product = Product.query.get(product_id)
        if product:
            cohort = db.session.query(Customer).join(WhatsAppOrder).filter(
                WhatsAppOrder.product_name == product.name, Customer.role == 'user'
            ).distinct().all()

    sent_count = 0
    for person in cohort:
        if person.phone and send_whatsapp_message(person.phone, final_message):
            sent_count += 1
            
    return jsonify({"status": "success", "sent_count": sent_count})


# --- PROMO CODE MANAGEMENT ---
@admin_bp.route('/promos/add', methods=['POST'])
def add_promo():
    print("🚀 STARTING ADD_PROMO...") # Look for this in terminal!
    
    code = request.form.get('code').upper().strip()
    percent = request.form.get('percent')
    expiry_input = request.form.get('expiry_date') 
    
    existing = PromoCode.query.filter_by(code=code).first()
    if existing:
        flash(f'Code {code} already exists!', 'danger')
        return redirect(url_for('admin.dashboard', tab='promo-code'))

    try:
        # Expiration Logic
        expiry_dt = None
        redeem_by_ts = None
        if expiry_input:
            expiry_dt = datetime.strptime(expiry_input, '%Y-%m-%d')
            expiry_dt = expiry_dt.replace(hour=23, minute=59, second=59)
            redeem_by_ts = int(expiry_dt.timestamp())

        # STEP 3: Create Coupon (Logic) - THIS IS CORRECT
        print("🔹 Step 3: Creating Coupon...")
        coupon = stripe.Coupon.create(
            percent_off=int(percent),
            duration='forever',
            name=f"{percent}% Off ({code})",
            redeem_by=redeem_by_ts
        )

        # STEP 4: Create Password (FIXED HERE)
        print("🔹 Step 4: Creating PromotionCode...")
        stripe.PromotionCode.create(   # <--- FIX THIS WORD
            coupon=coupon.id,
            code=code
        )

        # STEP 5: Save to DB
        print("🔹 Step 5: Saving to DB...")
        new_promo = PromoCode(
            code=code,
            stripe_coupon_id=coupon.id,
            discount_percent=percent,
            expires_at=expiry_dt,
            is_active=True,
            created_at=datetime.utcnow()
        )
        db.session.add(new_promo)
        db.session.commit()
        
        print(f"✅ SUCCESS! {code} is live.")
        flash(f'✅ Success! Code "{code}" created.', 'success')
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ ERROR: {e}")
        flash(f"Error: {str(e)}", "danger")

    return redirect(url_for('admin.dashboard', tab='promo-code'))

@admin_bp.route('/promos/delete/<int:id>')
def delete_promo(id):
    promo = PromoCode.query.get(id)
    if promo:
        try:
            # 1. Delete from Stripe
            stripe.Coupon.delete(promo.stripe_coupon_id)
            print(f"✅ Deleted Stripe Coupon: {promo.stripe_coupon_id}")
        except stripe.error.InvalidRequestError:
            print("⚠️ Coupon already deleted or not found in Stripe.")
        except Exception as e:
            print(f"❌ Stripe Delete Error: {e}")

        # 2. Delete from DB
        db.session.delete(promo)
        db.session.commit()
        flash('Promo Code deleted from Database & Stripe.', 'warning')
        
    return redirect(url_for('admin.dashboard', tab='promo-code'))

@admin_bp.route('/promos/sync')
def sync_stripe_promos():
    try:
        # 1. Fetch Promotion Codes from Stripe
        stripe_promos = stripe.PromotionCode.list(limit=100, active=True, expand=['data.coupon'])
        
        added_count = 0
        for promo in stripe_promos.data:
            code_text = promo.code.upper()
            
            # 👇 THE FIX: Skip user-specific reward vouchers (starting with RW-)
            if code_text.startswith('RW-'):
                continue 
            
            # Check if this general promo code is already in our DB
            exists = PromoCode.query.filter_by(code=code_text).first()
            
            if not exists:
                coupon_data = promo.coupon
                percent = int(coupon_data.percent_off) if coupon_data.percent_off else 0
                
                expiry_dt = None
                if coupon_data.redeem_by:
                    expiry_dt = datetime.fromtimestamp(coupon_data.redeem_by)

                new_promo = PromoCode(
                    code=code_text,
                    stripe_coupon_id=coupon_data.id,
                    discount_percent=percent,
                    is_active=promo.active,
                    created_at=datetime.fromtimestamp(promo.created),
                    expires_at=expiry_dt
                )
                db.session.add(new_promo)
                added_count += 1
                
        db.session.commit()
        
        if added_count > 0:
            flash(f"✅ Synced {added_count} general promo codes!", "success")
        else:
            flash("No new general promo codes to sync.", "info")
            
    except Exception as e:
        db.session.rollback()
        print(f"Sync Error: {e}")
        flash(f"Sync failed: {str(e)}", "danger")
        
    return redirect(url_for('admin.dashboard', tab='promo-code'))

# --- USER & LEADER MANAGEMENT ---
@admin_bp.route('/users/promote/<int:user_id>', methods=['POST'])
def promote_user(user_id):
    user = Customer.query.get_or_404(user_id)
    user.role = 'admin'
    db.session.commit()
    return redirect(url_for('admin.dashboard', tab='manage-users'))

@admin_bp.route('/users/demote/<int:user_id>', methods=['POST'])
def demote_user(user_id):
    user = Customer.query.get_or_404(user_id)
    if user.id != session.get('user_id'):
        leader_profile = GroupLeader.query.filter_by(customer_id=user.id).first()
        if leader_profile: db.session.delete(leader_profile)
        user.role = 'user'
        db.session.commit()
    return redirect(url_for('admin.dashboard', tab='manage-users'))

@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    user = Customer.query.get_or_404(user_id)
    if user.id != session.get('user_id'):
        db.session.delete(user)
        db.session.commit()
    return redirect(url_for('admin.dashboard', tab='manage-users'))

@admin_bp.route("/leaders/add", methods=['POST'])
def add_leader():
    # 1. Get and CLEAN the input (remove accidental spaces)
    target_email = request.form.get('user_email').strip()
    name = request.form.get('name')
    area = request.form.get('area')

    # 2. Find the User
    user = Customer.query.filter_by(email=target_email).first()

    # --- CHECK 1: Does user exist? ---
    if not user:
        flash(f"❌ Error: User with email '{target_email}' not found. Please register them first.", "danger")
        return redirect(url_for('admin.dashboard', tab='manage-leaders'))

    # --- CHECK 2: Do they have a phone number? ---
    # We need the phone for WhatsApp features
    if not user.phone:
        flash(f"⚠️ Error: The user '{user.name}' has no phone number saved. They must update their profile first.", "warning")
        return redirect(url_for('admin.dashboard', tab='manage-leaders'))

    # --- CHECK 3: Are they ALREADY a leader? ---
    # (This prevents the "Internal Server Error" crash)
    existing = GroupLeader.query.filter_by(customer_id=user.id).first()
    if existing:
        flash(f"⚠️ Error: This user is already the leader for '{existing.area}'!", "warning")
        return redirect(url_for('admin.dashboard', tab='manage-leaders'))

    # 3. Create the Leader
    try:
        new_leader = GroupLeader(
            name=name, 
            phone=user.phone,  # <--- Automatically takes phone from Customer profile
            area=area, 
            customer_id=user.id
        )
        
        user.role = 'leader' # Upgrade their role
        db.session.add(new_leader)
        db.session.commit()
        
        flash(f"✅ Success! Added {name} as leader for {area}.", "success")

    except Exception as e:
        db.session.rollback()
        print(f"Add Leader Error: {e}")
        flash("Database error occurred.", "danger")

    return redirect(url_for('admin.dashboard', tab='manage-leaders'))

@admin_bp.route("/leaders/delete/<int:id>", methods=['POST'])
def delete_leader(id):
    leader = GroupLeader.query.get_or_404(id)
    user = Customer.query.get(leader.customer_id)
    if user: user.role = 'user'
    db.session.delete(leader)
    db.session.commit()
    return redirect(url_for('admin.dashboard', tab='manage-leaders'))

@admin_bp.route("/leaders/edit/<int:id>", methods=['POST'])
def edit_leader(id):
    leader = GroupLeader.query.get_or_404(id)
    leader.name = request.form.get('name')
    leader.phone = request.form.get('phone')
    leader.area = request.form.get('area')
    if leader.leader_account:
        leader.leader_account.email = request.form.get('user_email')
    db.session.commit()
    return redirect(url_for('admin.dashboard', tab='manage-leaders'))

# --- SETTINGS ---
@admin_bp.route('/settings/clear-cache')
def clear_cache():
    session.pop('last_seen_id', None)
    flash("Cache cleared.", "success")
    return redirect(url_for('admin.dashboard', tab='settings'))

@admin_bp.route('/settings/update', methods=['POST'])
def update_settings():
    session['settings_alerts'] = bool(request.form.get('critical_alerts'))
    session['settings_broadcast'] = bool(request.form.get('broadcast_mode'))
    flash("Settings updated.", "success")
    return redirect(url_for('admin.dashboard', tab='settings'))

@admin_bp.route('/admin/settings/backup')
def run_backup():
    try:
        if not os.path.exists('backups'): os.makedirs('backups')
        shutil.copy2('instance/site.db', f"backups/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        flash("Backup successful.", "success")
    except:
        flash("Backup failed.", "danger")
    return redirect(url_for('admin.dashboard', tab='settings'))

@admin_bp.route('/update_status/<int:id>', methods=['POST'])
def update_status(id):
    inquiry = ContactInquiry.query.get_or_404(id)
    inquiry.status = request.form.get('status')
    db.session.commit()
    return redirect(url_for('admin.dashboard') + '?refresh=true&tab=customer-service')

@admin_bp.route('/delete/<int:id>', methods=['POST'])
def delete_inquiry(id):
    inquiry = ContactInquiry.query.get_or_404(id)
    db.session.delete(inquiry)
    db.session.commit()
    return redirect(url_for('admin.dashboard') + '?refresh=true&tab=customer-service')

# =========================================================
#  📊 ANALYTICS API (Connects DB to Dashboard Charts)
# =========================================================
@admin_bp.route('/api/analytics')
def analytics_api():
    # 1. GET TIME PERIOD FROM FRONTEND (Default to 'week')
    period = request.args.get('period', 'week')
    
    sgt_zone = pytz.timezone('Asia/Singapore')
    now = datetime.now(sgt_zone)
    today = now.date()
    
    demand_query = db.session.query(
        StockAlert.product_name,
        func.count(StockAlert.id)
    ).filter(StockAlert.is_notified == False)\
     .group_by(StockAlert.product_name)\
     .order_by(func.count(StockAlert.id).desc()).all()
     
    demand_data = [{'name': item[0], 'count': item[1]} for item in demand_query]
    
    # 2. DETERMINE START DATE BASED ON PERIOD
    if period == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'month':
        start_date = now - timedelta(days=30)
    elif period == 'year':
        start_date = now - timedelta(days=365)
    else: # Default 'week'
        start_date = now - timedelta(days=6)

    # 3. TOP SELLING PRODUCTS (Filtered by Time)
    top_products_query = db.session.query(
        WhatsAppOrder.product_name, 
        func.sum(WhatsAppOrder.quantity)
    ).filter(WhatsAppOrder.timestamp >= start_date)\
     .group_by(WhatsAppOrder.product_name)\
     .order_by(func.sum(WhatsAppOrder.quantity).desc()).limit(5).all()

    if top_products_query:
        top_labels = [p[0] for p in top_products_query]
        top_data = [p[1] for p in top_products_query]
    else:
        top_labels = ["No Sales"]
        top_data = [1]

    # 4. TOTAL SALES TREND (Dynamic Axis)
    sales_labels = []
    sales_data = []

    if period == 'today':
        # HOURLY LOOP (00:00 to 23:00)
        for i in range(8, 23): # Show business hours 8am-10pm
            hour_str = f"{i:02}"
            label = f"{i}:00"
            
            # Sum sales for this specific hour today
            hourly_sum = db.session.query(func.sum(WhatsAppOrder.total_price)).filter(
                func.date(WhatsAppOrder.timestamp) == today,
                func.strftime('%H', WhatsAppOrder.timestamp) == hour_str
            ).scalar()
            
            sales_labels.append(label)
            sales_data.append(float(hourly_sum) if hourly_sum else 0)

    elif period == 'year':
        # MONTHLY LOOP (Last 12 Months)
        for i in range(11, -1, -1): # Count backwards 11 months
            month_date = now - timedelta(days=i*30) 
            month_str = month_date.strftime('%m-%Y') # e.g. 02-2025
            label = month_date.strftime('%b') # Jan, Feb
            
            monthly_sum = db.session.query(func.sum(WhatsAppOrder.total_price)).filter(
                func.strftime('%m-%Y', WhatsAppOrder.timestamp) == month_str
            ).scalar()
            
            sales_labels.append(label)
            sales_data.append(float(monthly_sum) if monthly_sum else 0)

    else:
        # DAILY LOOP (For Week & Month)
        days_range = 30 if period == 'month' else 7
        start_loop = today - timedelta(days=days_range - 1)
        
        for i in range(days_range):
            day = start_loop + timedelta(days=i)
            day_str = day.strftime('%d %b') if period == 'month' else day.strftime('%a')
            
            daily_sum = db.session.query(func.sum(WhatsAppOrder.total_price)).filter(
                func.date(WhatsAppOrder.timestamp) == day
            ).scalar()
            
            sales_labels.append(day_str)
            sales_data.append(float(daily_sum) if daily_sum else 0)

    return jsonify({
        "sales": {"labels": sales_labels, "data": sales_data},
        "top_products": {"labels": top_labels, "data": top_data},
        "demand": demand_data
    })