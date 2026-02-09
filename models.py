from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from datetime import datetime
import pytz
from flask_mail import Mail

db = SQLAlchemy()
mail = Mail()

# Helper function for Singapore Time
def get_sg_time():
    return datetime.now(pytz.timezone('Asia/Singapore'))

# ==============================================================================
# SQLITE CONFIGURATION (Single File Mode)
# ==============================================================================
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=DELETE")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

# ==============================================================================
# 1. WEBSITE DATA: Customer Service (Web Form)
# ==============================================================================
class ContactInquiry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20))
    message = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), default='Pending') 
    created_at = db.Column(db.DateTime, default=get_sg_time)

# ==============================================================================
# 2. WHATSAPP DATA: Sales & Lead Intake
# ==============================================================================
class WhatsAppOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'))
    leader_id = db.Column(db.Integer, db.ForeignKey('group_leader.id'))
    customer_phone = db.Column(db.String(20), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    commission_earned = db.Column(db.Float, default=0.0)
    order_status = db.Column(db.String(50), default='New Order')
    timestamp = db.Column(db.DateTime, default=get_sg_time)
    invoice_url = db.Column(db.String(500), nullable=True)

class WhatsAppLead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    extracted_name = db.Column(db.String(100)) 
    neighborhood = db.Column(db.String(100))     
    status = db.Column(db.String(50), default='Awaiting Assignment')
    created_at = db.Column(db.DateTime, default=get_sg_time)

# ==============================================================================
# 3. CORE BUSINESS DATA: Inventory & Logistics
# ==============================================================================
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    available_qty = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default='In Stock')
    description = db.Column(db.Text, nullable=False)
    image_file = db.Column(db.String(500), nullable=False, default='default_product.jpg')
    category = db.Column(db.String(50), nullable=False, default='leafy')
    reviews = db.relationship('Review', backref='product', lazy=True)
    @property
    def average_rating(self):
        if not self.reviews:
            return 0
        total = sum(r.rating for r in self.reviews)
        return round(total / len(self.reviews), 1)
    @property
    def review_count(self):
        return len(self.reviews)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(100), nullable=False, unique=True)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120))
    leader_id = db.Column(db.Integer, db.ForeignKey('group_leader.id'))
    orders = db.relationship('WhatsAppOrder', backref='buyer', lazy=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='user')
    postal_code = db.Column(db.String(6), nullable=True)  
    street_address = db.Column(db.String(255), nullable=True) 
    unit_number = db.Column(db.String(10), nullable=True) 
    loyalty = db.relationship('LoyaltyPoints', backref='customer', uselist=False, lazy=True)

    @property
    def points(self):
        """Helper to get points safely (returns 0 if no loyalty account exists)"""
        return self.loyalty.current_points if self.loyalty else 0

class GroupLeader(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=True) 
    area = db.Column(db.String(100)) 
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    members = db.relationship('Customer', backref='leader', lazy=True, foreign_keys='Customer.leader_id')
    leader_orders = db.relationship('WhatsAppOrder', backref='handling_leader', lazy=True)
    leader_account = db.relationship('Customer', backref='leader_profile', foreign_keys=[customer_id])
    

class StockAlert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_phone = db.Column(db.String(20), nullable=False)
    product_name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=get_sg_time) 
    is_notified = db.Column(db.Boolean, default=False, nullable=False)
    notified_at = db.Column(db.DateTime, nullable=True)

class LoyaltyPoints(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    current_points = db.Column(db.Integer, default=0)
    lifetime_points = db.Column(db.Integer, default=0)
    tier = db.Column(db.String(20), default='Seedling') # Seedling, Sprout, Harvest
    transactions = db.relationship('LoyaltyTransaction', backref='account', lazy=True)

    def add_points(self, amount, description, ref_id=None):
        if self.current_points is None: self.current_points = 0
        if self.lifetime_points is None: self.lifetime_points = 0

        self.current_points += amount
        self.lifetime_points += amount
        self.update_tier()
        
        txn = LoyaltyTransaction(
            loyalty_id=self.id, 
            amount=amount, 
            description=description, 
            type='earn', 
            reference_id=str(ref_id) if ref_id else None
        )
        db.session.add(txn)

    def redeem_points(self, amount, description):
        """Deducts points if sufficient balance"""
        if self.current_points >= amount:
            self.current_points -= amount
            txn = LoyaltyTransaction(
                loyalty_id=self.id, 
                amount=-amount, 
                description=description, 
                type='redeem'
            )
            db.session.add(txn)
            return True
        return False
    
    def update_tier(self):
        """Auto-calculates tier based on lifetime growth milestones"""
        if self.lifetime_points >= 300: # 🌳 Harvest
            self.tier = 'Harvest'
        elif self.lifetime_points >= 100: # 🌿 Sprout
            self.tier = 'Sprout'
        else: # 🌱 Seedling
            self.tier = 'Seedling'
            
class LoyaltyTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    loyalty_id = db.Column(db.Integer, db.ForeignKey('loyalty_points.id'), nullable=False)
    amount = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    type = db.Column(db.String(20))
    reference_id = db.Column(db.String(50), nullable=True) 
    timestamp = db.Column(db.DateTime, default=get_sg_time)
    
class Voucher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    discount_amount = db.Column(db.Float, nullable=False)
    stripe_coupon_id = db.Column(db.String(50), nullable=False)
    is_used = db.Column(db.Boolean, default=False)
    expiry_date = db.Column(db.DateTime, nullable=True) 
    timestamp = db.Column(db.DateTime, default=get_sg_time)
    
class PromoCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    discount_percent = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=get_sg_time)
    stripe_coupon_id = db.Column(db.String(50), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    
class Review(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # Relationships
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('whats_app_order.id'), nullable=False)
    
    # Content
    rating = db.Column(db.Integer, nullable=False)
    comment = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=get_sg_time)
    customer = db.relationship('Customer', backref='reviews')
    
    @property
    def stars(self):
        """Returns visual stars for the UI (e.g. '★★★★☆')"""
        return '★' * self.rating + '☆' * (5 - self.rating)
    
def find_leader_by_address(postal_code, street_address):
    """
    Auto-assigns a leader ID by checking if the Leader's 'Area' 
    matches the User's Postal Code or Street Name.
    """
    leaders = GroupLeader.query.filter(GroupLeader.area != None).all()
    
    user_sector = str(postal_code).strip()[:2] if postal_code and len(str(postal_code)) >= 2 else ""
    user_street = street_address.lower().strip() if street_address else ""

    for leader in leaders:
        admin_area = leader.area.lower().strip()
        
        if admin_area.isdigit() and len(admin_area) == 2:
            if user_sector == admin_area:
                return leader.id
        
        elif admin_area in user_street:
            return leader.id
            
    return None