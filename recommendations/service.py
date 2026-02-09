"""
Recommendation Service Module
Handles Gen AI product recommendations based on customer purchase history
"""
import os
import json
from datetime import datetime
import pytz
from openai import OpenAI
from dotenv import load_dotenv
from models import db, WhatsAppOrder, Product, Customer

load_dotenv()

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def get_customer_purchase_history(customer_id):
    """
    Fetch customer's purchase history from WhatsAppOrder table
    
    Args:
        customer_id: Customer ID
        
    Returns:
        List of WhatsAppOrder objects ordered by most recent first
    """
    orders = WhatsAppOrder.query.filter_by(
        customer_id=customer_id
    ).order_by(
        WhatsAppOrder.timestamp.desc()
    ).all()
    
    return orders


def get_available_products():
    """
    Fetch all available products (in stock with quantity > 0)
    
    Returns:
        List of Product objects that are in stock
    """
    products = Product.query.filter(
        Product.status == 'In Stock',
        Product.available_qty > 0
    ).all()
    
    return products


def format_purchase_history(orders):
    """
    Format purchase history into readable text for OpenAI prompt
    
    Args:
        orders: List of WhatsAppOrder objects
        
    Returns:
        Formatted string describing purchase history
    """
    if not orders:
        return "No previous purchase history."
    
    # Get recent orders (last 10)
    recent_orders = orders[:10]
    
    history_lines = []
    for order in recent_orders:
        date_str = order.timestamp.strftime('%d %b %Y')
        history_lines.append(
            f"- {order.product_name} (Quantity: {order.quantity}, Price: ${order.total_price:.2f}) on {date_str}"
        )
    
    history_text = "\n".join(history_lines)
    
    # Add summary statistics
    total_orders = len(orders)
    unique_products = len(set(order.product_name for order in orders))
    total_spent = sum(order.total_price for order in orders)
    
    summary = f"\n\nSummary: {total_orders} total orders, {unique_products} unique products purchased, ${total_spent:.2f} total spent."
    
    return history_text + summary


def format_product_list(products):
    """
    Format available products list for OpenAI prompt
    
    Args:
        products: List of Product objects
        
    Returns:
        Formatted string listing all available products
    """
    if not products:
        return "No products currently available."
    
    product_lines = []
    for product in products:
        product_lines.append(
            f"ID: {product.id}, Name: {product.name}, Price: ${float(product.price):.2f}, "
            f"Category: {product.category}, Stock: {product.available_qty}, "
            f"Description: {product.description[:100]}"
        )
    
    return "\n".join(product_lines)


def generate_recommendations(customer_id, purchase_history, products):
    """
    Generate personalized product recommendations using OpenAI
    
    Args:
        customer_id: Customer ID
        purchase_history: List of WhatsAppOrder objects
        products: List of Product objects (available products)
        
    Returns:
        Dictionary with recommendations and metadata
    """
    if not client:
        return {
            "error": "OpenAI API not configured",
            "recommendations": []
        }
    
    if not products:
        return {
            "error": "No products available",
            "recommendations": []
        }
    
    # Format data for prompt
    history_text = format_purchase_history(purchase_history)
    products_text = format_product_list(products)
    
    # Get customer info
    customer = Customer.query.get(customer_id)
    customer_name = customer.name if customer else "Customer"
    
    # Construct OpenAI prompt
    system_prompt = """You are a helpful product recommendation assistant for a fresh produce farm (Leaf Plant). 
Your goal is to analyze customer purchase history and recommend relevant products from the available inventory.
Provide personalized recommendations based on:
1. Products similar to what they've purchased before
2. Complementary products that go well with their purchases
3. Popular products if they're a new customer
4. Products in similar categories

Return your response as a valid JSON object with this exact structure:
{
  "recommendations": [
    {
      "product_id": <integer>,
      "product_name": "<string>",
      "reason": "<brief explanation why this product is recommended>",
      "confidence": "<high|medium|low>"
    }
  ],
  "purchase_history_summary": "<brief summary of customer's purchase patterns>"
}

Provide 5-8 recommendations. Focus on products that make sense based on their history."""
    
    user_prompt = f"""Customer: {customer_name} (ID: {customer_id})

PURCHASE HISTORY:
{history_text}

AVAILABLE PRODUCTS:
{products_text}

Please analyze the purchase history and recommend products from the available products list. 
Return ONLY valid JSON, no additional text."""
    
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7,
            response_format={"type": "json_object"}
        )
        
        # Parse JSON response
        ai_response = completion.choices[0].message.content
        result = json.loads(ai_response)
        
        # Validate and enrich recommendations with product details
        validated_recommendations = []
        product_dict = {p.id: p for p in products}
        
        for rec in result.get("recommendations", []):
            product_id = rec.get("product_id")
            if product_id and product_id in product_dict:
                product = product_dict[product_id]
                validated_recommendations.append({
                    "product_id": product.id,
                    "product_name": product.name,
                    "price": float(product.price),
                    "image_file": product.image_file,
                    "category": product.category,
                    "reason": rec.get("reason", "Recommended for you"),
                    "confidence": rec.get("confidence", "medium")
                })
        
        # If no valid recommendations, provide fallback (popular products)
        if not validated_recommendations and products:
            # Get top 5 products by stock (as fallback)
            fallback_products = sorted(products, key=lambda p: p.available_qty, reverse=True)[:5]
            validated_recommendations = [
                {
                    "product_id": p.id,
                    "product_name": p.name,
                    "price": float(p.price),
                    "image_file": p.image_file,
                    "category": p.category,
                    "reason": "Popular choice - well-stocked item",
                    "confidence": "medium"
                }
                for p in fallback_products
            ]
        
        return {
            "recommendations": validated_recommendations,
            "purchase_history_summary": result.get("purchase_history_summary", "New customer"),
            "customer_id": customer_id
        }
        
    except json.JSONDecodeError as e:
        # Fallback to popular products if JSON parsing fails
        fallback_products = sorted(products, key=lambda p: p.available_qty, reverse=True)[:5]
        return {
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
            "purchase_history_summary": "Unable to analyze history",
            "customer_id": customer_id,
            "error": "Failed to parse AI response"
        }
    except Exception as e:
        # Return fallback recommendations on any error
        fallback_products = sorted(products, key=lambda p: p.available_qty, reverse=True)[:5]
        return {
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
            "purchase_history_summary": "Error generating recommendations",
            "customer_id": customer_id,
            "error": str(e)
        }