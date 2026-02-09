"""
Recommendations API Routes
Flask blueprint for recommendation endpoints
"""
from flask import Blueprint, jsonify, session, abort
from models import db, Customer
from recommendations.service import (
    get_customer_purchase_history,
    get_available_products,
    generate_recommendations
)

recommendations_bp = Blueprint('recommendations', __name__)


@recommendations_bp.route('/recommendations', methods=['GET'])
def get_recommendations():
    """
    Get personalized product recommendations for logged-in customer
    Requires authentication
    """
    # Check if user is logged in
    if 'user_id' not in session:
        return jsonify({
            "error": "Authentication required",
            "message": "Please log in to view recommendations"
        }), 401
    
    customer_id = session['user_id']
    
    # Verify customer exists
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({
            "error": "Customer not found"
        }), 404
    
    try:
        # Get purchase history
        purchase_history = get_customer_purchase_history(customer_id)
        
        # Get available products
        available_products = get_available_products()
        
        if not available_products:
            return jsonify({
                "recommendations": [],
                "message": "No products currently available",
                "customer_id": customer_id
            }), 200
        
        # Generate recommendations
        result = generate_recommendations(
            customer_id,
            purchase_history,
            available_products
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        # Return fallback recommendations on error
        try:
            available_products = get_available_products()
            fallback_products = sorted(available_products, key=lambda p: p.available_qty, reverse=True)[:5]
            
            return jsonify({
                "recommendations": [
                    {
                        "product_id": p.id,
                        "product_name": p.name,
                        "price": float(p.price),
                        "image_file": p.image_file,
                        "category": p.category,
                        "reason": "Popular choice",
                        "confidence": "medium"
                    }
                    for p in fallback_products
                ],
                "purchase_history_summary": "Error occurred",
                "customer_id": customer_id,
                "error": str(e)
            }), 200
        except:
            return jsonify({
                "error": "Failed to generate recommendations",
                "recommendations": []
            }), 500


@recommendations_bp.route('/recommendations/<int:customer_id>', methods=['GET'])
def get_recommendations_admin(customer_id):
    """
    Admin endpoint to get recommendations for any customer
    Requires admin role
    """
    # Check if user is logged in and is admin
    if 'user_id' not in session:
        abort(401)
    
    if session.get('user_role') != 'admin':
        abort(403)
    
    # Verify customer exists
    customer = Customer.query.get(customer_id)
    if not customer:
        return jsonify({
            "error": "Customer not found"
        }), 404
    
    try:
        # Get purchase history
        purchase_history = get_customer_purchase_history(customer_id)
        
        # Get available products
        available_products = get_available_products()
        
        if not available_products:
            return jsonify({
                "recommendations": [],
                "message": "No products currently available",
                "customer_id": customer_id
            }), 200
        
        # Generate recommendations
        result = generate_recommendations(
            customer_id,
            purchase_history,
            available_products
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        return jsonify({
            "error": "Failed to generate recommendations",
            "recommendations": []
        }), 500