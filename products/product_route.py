from flask import Flask, render_template, Blueprint, redirect, url_for, request, jsonify, flash, session,abort
from models import db, Product, Category, ContactInquiry,Review, WhatsAppOrder
import os
from sqlalchemy.orm.attributes import flag_modified
from decimal import Decimal, ROUND_DOWN
from datetime import datetime
import pytz
import math
product_bp = Blueprint('product', __name__)

app = Flask(__name__)

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
db_path = os.path.join(root_dir, 'leafplant.db')

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# ==============================================================================
# 1. LIVE DATA API
# ==============================================================================
@app.route("/admin/api/products")
def get_products_api():
    products = Product.query.order_by(Product.id.desc()).all()
    # This JSON feed tells the JavaScript on your dashboard when numbers change
    return jsonify([{
        'id': p.id,
        'available_qty': p.available_qty,
        'status': p.status
    } for p in products])

#======================================================================================
#               FILTERING ENGINE
#======================================================================================

@product_bp.route("/product")
def list_products():
    target_slug = request.args.get('category')
    search_query = request.args.get('search', '').strip()

    query = Product.query

    if target_slug and target_slug not in ['all','None','']:
        query = query.filter_by(category=target_slug)

    if search_query:
        query = query.filter(Product.name.ilike(f"%{search_query}%"))

    # Execute the query first
    products = query.all()


    print(f"DEBUG: Category from URL is '{target_slug}'")
    for p in products:
        print(f"DEBUG: Found Product: {p.name} with Category: {p.category}")
   

    categories = Category.query.all()

    return render_template("product.html", 
                           products=products, 
                           categories=categories,
                           active_category=target_slug,
                           search_val=search_query)
    
@product_bp.route('/review/<int:order_id>', methods=['GET', 'POST'])
def leave_review(order_id):
    # 1. Security Check
    if 'user_id' not in session:
        flash("Please log in to leave a review.", "warning")
        return redirect(url_for('auth.login'))
    
    # 2. Get the Order
    order = WhatsAppOrder.query.get_or_404(order_id)
    
    # 3. Security: "Did YOU buy this?"
    if order.customer_id != session['user_id']:
        abort(403) 
    
    # 4. Gatekeeper: "Did you RECEIVE it?"
    if order.order_status != 'Delivered' and order.order_status != 'Received':
        flash("You can only review items once they are delivered!", "warning")
        return redirect(url_for('orders.orders_page'))

    # 5. Handle Form Submission
    if request.method == 'POST':
        try:
            rating = int(request.form.get('rating'))
            comment = request.form.get('comment')
            
            product = Product.query.filter_by(name=order.product_name).first()
            
            if product:
                new_review = Review(
                    customer_id=session['user_id'],
                    product_id=product.id,
                    order_id=order.id,
                    rating=rating,
                    comment=comment,
                    timestamp=datetime.now(pytz.timezone('Asia/Singapore'))
                )
                db.session.add(new_review)
                db.session.commit()
                
                # ✅ CORRECT: Show the Success Screen (success=True)
                return render_template('leave_review.html', order=order, success=True)
            else:
                flash("Error: Product details not found.", "danger")
                
        except Exception as e:
            db.session.rollback()
            print(f"Review Error: {e}")
            flash("Something went wrong saving your review.", "danger")

    # ✅ CORRECT: Default view shows the form (success=False)
    # (Your previous code had success=True here, which hides the form immediately!)
    return render_template('leave_review.html', order=order, success=False)

# --- GLOBAL REVIEWS ROUTE ---
@product_bp.route('/reviews')
def global_reviews():
    # 1. Get Filter Parameters from URL
    product_filter = request.args.get('product_id')
    sort_order = request.args.get('sort', 'newest')

    # 2. Start the Query
    query = Review.query

    # 3. Apply Product Filter (if selected)
    if product_filter and product_filter != 'all':
        query = query.filter_by(product_id=product_filter)

    # 4. Apply Sorting
    if sort_order == 'oldest':
        query = query.order_by(Review.timestamp.asc())
    else:
        query = query.order_by(Review.timestamp.desc())

    # 5. Execute Query
    reviews = query.all()

    # 6. Fetch Product List (For the dropdown menu)
    all_products = Product.query.order_by(Product.name).all()

    # --- CALCULATE STATS (Based on filtered results!) ---
    total_reviews = len(reviews)
    average_rating = 0.0
    star_counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
    recommend_pct = 0
    
    if total_reviews > 0:
        total_score = sum(r.rating for r in reviews)
        for r in reviews:
            if r.rating in star_counts:
                star_counts[r.rating] += 1
        
        average_rating = round(total_score / total_reviews, 1)
        happy_customers = star_counts[5] + star_counts[4]
        recommend_pct = int((happy_customers / total_reviews) * 100)

    return render_template('global_review.html', 
                           reviews=reviews, 
                           all_products=all_products,     # Pass products to HTML
                           current_filter=product_filter, # Pass current selection
                           current_sort=sort_order,       # Pass current sort order
                           total_reviews=total_reviews,
                           average_rating=average_rating,
                           star_counts=star_counts,
                           recommend_pct=recommend_pct)
    
if __name__ == "__main__":
    with app.app_context():
        db.create_all() 
    app.run(debug=True, port=5001)