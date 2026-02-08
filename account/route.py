import re  # <--- For phone validation
from datetime import timedelta # <--- For Remember Me duration
from itsdangerous import URLSafeTimedSerializer, SignatureExpired # <--- For Tokens
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Message 
from models import db, Customer, mail, find_leader_by_address

# 1. Define the Blueprint
auth = Blueprint('auth', __name__)

# 2. LOGIN ROUTE (Updated with Remember Me)
@auth.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')
    remember = request.form.get('remember')

    user = Customer.query.filter_by(email=email).first()

    if user and check_password_hash(user.password_hash, password):
        session['user_id'] = user.id
        session['user_name'] = user.name
        session['user_role'] = user.role 
        
        # --- REMEMBER ME LOGIC ---
        if remember:
            session.permanent = True
            current_app.permanent_session_lifetime = timedelta(days=7) # Stays logged in for 7 days
        else:
            session.permanent = False
        # ------------------------------

        flash(f"Welcome back, {user.name}!", "login_success")

        if user.role == 'admin':
            return redirect(url_for('admin.dashboard'))
        elif user.role == 'leader':
            return redirect(url_for('leader')) 
        else:
            return redirect(url_for('myaccount.myaccount')) 
    
    flash("Invalid email or password.", "danger")
    return redirect(url_for('account', tab='login'))

@auth.route('/signup', methods=['POST'])
def signup():
    # 1. Capture basic data
    name = request.form.get('name')
    email = request.form.get('email')
    phone = request.form.get('phone') 
    password = request.form.get('password')
    confirm_password = request.form.get('confirm_password')

    # 2. Capture Address data
    postal_code = request.form.get('postal_code')
    unit_number = request.form.get('unit_number')
    street_address = request.form.get('street_address')

    # --- 🛡️ VALIDATION CHECKS START ---
    # (Keep your existing validation checks here - Name, Phone, Password, Postal Code)
    
    # Check 1: Name Length
    if not name or not (10 <= len(name) <= 50):
        flash("Name must be between 10 and 50 characters long.", "danger")
        return redirect(url_for('account', tab='signup'))

    # Check 2: Phone Format & Logic
    if not phone or not re.match(r"^\+?[0-9\s]+$", phone):
        flash("Phone number can only contain digits and an optional '+' prefix.", "danger")
        return redirect(url_for('account', tab='signup'))
    
    digits_only = re.sub(r'\D', '', phone) 
    if phone.strip().startswith('+'):
        if len(digits_only) < 10:
            flash("Phone number with country code is too short (min 10 digits).", "danger")
            return redirect(url_for('account', tab='signup'))
    else:
        if len(digits_only) < 8:
            flash("Phone number must be at least 8 digits.", "danger")
            return redirect(url_for('account', tab='signup'))

    # Check 3: Password Match
    if password != confirm_password:
        flash("Passwords do not match!", "danger")
        return redirect(url_for('account', tab='signup'))

    # Check 4: Postal Code
    if not postal_code or not postal_code.isdigit() or len(postal_code) != 6:
        flash("Postal code must be exactly 6 digits.", "danger")
        return redirect(url_for('account', tab='signup'))

    # 3. Check if user already exists
    user_exists = Customer.query.filter((Customer.email == email) | (Customer.phone == phone)).first()
    if user_exists:
        flash('Email or Phone already exists!', 'danger')
        return redirect(url_for('account', tab='signup')) 
    # --- VALIDATION CHECKS END ---


    # 4. Create New User (THIS MUST COME FIRST)
    new_user = Customer(
        name=name,
        email=email,
        phone=phone,
        password_hash=generate_password_hash(password, method='scrypt'),
        role='user',
        postal_code=postal_code,
        unit_number=unit_number,
        street_address=street_address
    )

    # 5. 🔥 NOW Run Auto-Assign Logic (After creating new_user) 🔥
    assigned_leader_id = find_leader_by_address(postal_code, street_address)
    
    if assigned_leader_id:
        new_user.leader_id = assigned_leader_id
        print(f"✅ Auto-assigned to Leader ID: {assigned_leader_id}")
    else:
        print("⚠️ No leader found for this area")

    # 6. Save to DB
    try:
        db.session.add(new_user)
        db.session.commit()
        flash('Account created successfully! Please log in.', 'login_success')
        return redirect(url_for('account', tab='login'))
    except Exception as e:
        db.session.rollback()
        print(f"❌ SIGNUP ERROR: {e}")
        flash('An internal error occurred. Please try again.', 'danger')
        return redirect(url_for('account', tab='signup'))

# 4. LOGOUT
@auth.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for('account'))

# 5. REAL EMAIL FORGOT PASSWORD ROUTES
@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = Customer.query.filter_by(email=email).first()
        
        if user:
            # A. Generate Secure Token
            s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
            token = s.dumps(user.email, salt='password-reset-salt')
            
            # B. Generate Link
            link = url_for('auth.reset_password', token=token, _external=True)
            
            # C. Send Real Email 📧
            try:
                msg = Message('Password Reset Request', 
                              sender=current_app.config['MAIL_USERNAME'], 
                              recipients=[email])
                
                # Email Body
                msg.body = f"""Hello {user.name},

You requested a password reset for your Leaf Plant account.
Please click the link below to reset your password:

{link}

This link will expire in 1 hour.
If you did not make this request, please ignore this email.

Regards,
Team Leaf Plant
"""
                mail.send(msg)
                flash('Reset link sent to your email.', 'success')
            
            except Exception as e:
                print(f"❌ MAIL ERROR: {e}")
                flash(f"Error sending email: {e}", "danger")
        else:
            # Security: Don't reveal if user exists or not
            flash('If an account exists, a reset link has been sent.', 'info')
            
        return redirect(url_for('account', tab='login'))
        
    return render_template('forgot_password.html')

@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=3600) 
    except SignatureExpired:
        flash('The reset link has expired.', 'danger')
        return redirect(url_for('auth.forgot_password'))
    except Exception:
        flash('The reset link is invalid.', 'danger')
        return redirect(url_for('account'))
    
    if request.method == 'POST':
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        if password != confirm_password:
            flash("Passwords do not match!", "danger")
            return redirect(request.url)

        user = Customer.query.filter_by(email=email).first()
        
        if user:
            user.password_hash = generate_password_hash(password, method='scrypt')
            db.session.commit()
            flash('Your password has been updated! Please log in.', 'success')
            return redirect(url_for('account', tab='login'))
            
    return render_template('reset_password.html', token=token)

# 6. ADMIN SEEDING (Unchanged)
def create_admin():
    admin = Customer.query.filter_by(email='admin@leafplant.com').first()
    if admin:
        if admin.role != 'admin':
            admin.role = 'admin'
            db.session.commit()
            print("🔄 Existing admin user role updated to 'admin'")
        else:
            print("❌ Admin already exists with correct role!")
        return
    
    admin_user = Customer(
        name='Admin User',
        email='admin@leafplant.com',
        phone='ADMIN_SYSTEM_01', 
        password_hash=generate_password_hash('adminpass', method='scrypt'),
        role='admin' 
    )
    try:
        db.session.add(admin_user)
        db.session.commit()
        print("✅ Admin created successfully with 'admin' role!")
    except Exception as e:
        db.session.rollback()
        print(f"❌ Admin Seed Error: {e}")

# This part is only for running auth.py directly (rarely used in blueprints)
if __name__ == "__main__":
    from main import create_app
    app = create_app()
    with app.app_context():
        create_admin()