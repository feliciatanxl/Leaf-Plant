import os
import io
import csv
import pytz
import requests
from datetime import datetime
from flask import Flask, send_from_directory, render_template, Response, request, redirect, url_for, jsonify, session, flash, abort
from dotenv import load_dotenv
from sqlalchemy import event
from firebase_admin import credentials, storage, initialize_app

basedir = os.path.abspath(os.path.dirname(__file__))
cert_path = os.path.join(basedir, 'serviceAccountKey.json')

cred = credentials.Certificate(cert_path)
initialize_app(cred, {
    'storageBucket': 'wdp-ws-ai-2025.firebasestorage.app'
})

# Database and Model Imports
from models import db, WhatsAppOrder, GroupLeader, Product, StockAlert, Customer, WhatsAppLead, set_sqlite_pragma, Category,mail
from sqlalchemy.orm.attributes import flag_modified

# Blueprint Imports
from contact.route import contact_bp 
from admin.routes import admin_bp 
from leader.route import leader_bp
from account.route import auth, create_admin
from myaccount.route import myaccount_bp
from products.product_route import product_bp
from orders.routes import orders_bp

# Load environment variables
load_dotenv()

def create_app():
    app = Flask(__name__)

    # --- Database Configuration ---
    basedir = os.path.abspath(os.path.dirname(__file__))
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'leafplant.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-123')
    
    # --- 📧 EMAIL CONFIGURATION (Gmail) ---
    app.config['MAIL_SERVER'] = 'smtp.gmail.com'
    app.config['MAIL_PORT'] = 587
    app.config['MAIL_USE_TLS'] = True
    app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME') 
    app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

    # Initialize Database
    db.init_app(app)
    mail.init_app(app)

    # Attach SQLite fixes
    with app.app_context():
        @event.listens_for(db.engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=DELETE")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    # Register Blueprints
    app.register_blueprint(contact_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    app.register_blueprint(leader_bp)
    app.register_blueprint(auth)
    app.register_blueprint(myaccount_bp)
    app.register_blueprint(product_bp)
    app.register_blueprint(orders_bp, url_prefix='/orders')

    # ==============================================================================
    # GLOBAL PUBLIC ROUTES
    # ==============================================================================
    @app.route('/')
    def index(): 
        return render_template('index.html')
    
    # @app.route('/orders')
    # def orders():
    #     # 1. Check if logged in
    #     if 'user_id' not in session:
    #         flash("Please log in to view your bag.", "warning")
    #         return redirect(url_for('account'))
        
    #     # 2. Check if the role is 'user'
    #     if session.get('user_role') != 'user':
    #         # Admins and Leaders shouldn't use the customer cart
    #         abort(403) 
            
    #     return render_template('orders.html')

    @app.route('/about')
    def about(): return render_template('about.html')

    @app.route('/product')
    def product():
        all_products = Product.query.all() 
        categories = Category.query.all()
        return render_template('product.html', products=all_products,categories=categories)

    @app.route('/article')
    def article(): return render_template('article.html')

    @app.route('/account')
    def account(): 
        if 'user_id' in session:
            role = session.get('user_role')
            if role == 'admin':
                return redirect(url_for('admin.dashboard'))
            elif role == 'leader':
                return redirect(url_for('leader'))
            return redirect(url_for('myaccount'))
        return render_template('account.html')

    @app.route('/favicon.ico')
    def favicon_root():
        return send_from_directory(os.path.join(app.root_path, 'static', 'image'), 'favicon.png')
    
    @app.route('/myaccount')
    def myaccount():
        if 'user_id' not in session:
            return redirect(url_for('account')) 
        current_user = Customer.query.get(session['user_id'])
        return render_template('myaccount.html', user=current_user) 

    # ==============================================================================
    # LEADER DASHBOARD ROUTE (Private & Filtered)
    # ==============================================================================
    @app.route('/leader')
    def leader():
        if 'user_id' not in session:
            abort(401)

        if session.get('user_role') != 'leader':
            abort(403)

        leader_data = GroupLeader.query.filter_by(customer_id=session['user_id']).first()
        
        if not leader_data:
            abort(500)

        orders = WhatsAppOrder.query.filter_by(leader_id=leader_data.id).all()
        neighbors = Customer.query.filter_by(leader_id=leader_data.id).all()
        
        pending_leads = WhatsAppLead.query.filter(WhatsAppLead.neighborhood.ilike(f"%{leader_data.area}%")).all()

        total_sales = sum(o.total_price for o in orders if o.order_status == 'Confirmed')
        pending_commission = total_sales * 0.111
        
        sgt = pytz.timezone('Asia/Singapore')
        today = datetime.now(sgt).date()
        today_orders_count = sum(1 for o in orders if o.timestamp.date() == today)

        return render_template('leader.html', 
                                leader=leader_data,
                                orders=orders,
                                neighbors=neighbors,
                                pending_leads=pending_leads,
                                total_sales=total_sales,
                                pending_commission=pending_commission,
                                today_orders_count=today_orders_count)
            
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template('500.html'), 500

    @app.errorhandler(405)
    def method_not_allowed(e):
        return render_template('405.html'), 405
    
    @app.errorhandler(403)
    def forbidden(e):
        return render_template('403.html'), 403
    
    @app.errorhandler(401)
    def unauthorized(e):
        return render_template('401.html'), 401

    
    # ==============================================================================
    # FARM REPORT ROUTE 
    # ==============================================================================
    @app.route('/admin/generate-farm-report')
    def generate_farm_report():
        sgt = pytz.timezone('Asia/Singapore')
        today_str = datetime.now(sgt).strftime('%Y-%m-%d')
        
        report_data = db.session.query(
            GroupLeader.name,
            GroupLeader.phone,
            GroupLeader.area,
            WhatsAppOrder.product_name,
            db.func.sum(WhatsAppOrder.quantity)
        ).join(GroupLeader, WhatsAppOrder.leader_id == GroupLeader.id)\
        .filter(db.func.strftime('%Y-%m-%d', WhatsAppOrder.timestamp) == today_str)\
        .group_by(GroupLeader.name, GroupLeader.phone, GroupLeader.area, WhatsAppOrder.product_name).all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Leader Name', 'Leader Phone', 'Area', 'Product', 'Total Quantity'])
        
        for row in report_data:
            writer.writerow(row)
        
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-disposition": f"attachment; filename=farm_report_{today_str}.csv"}
        )

    # --- Database Initialization ---
    with app.app_context():
        db.create_all()
        create_admin() 

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True, port=5001)