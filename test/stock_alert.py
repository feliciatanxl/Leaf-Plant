import os
import csv
import requests
from datetime import datetime
import pytz
from whatsapp.app import app
from models import db, WhatsAppOrder, GroupLeader, StockAlert, Product

def run_management_tasks():
    with app.app_context():
        # 1. SETUP: Timezone & Auth
        sgt = pytz.timezone('Asia/Singapore')
        today_date = datetime.now(sgt).strftime('%Y-%m-%d')
        access_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        phone_id = os.getenv("PHONE_NUMBER_ID")

        # ==============================================================================
        # TASK 2: AUTOMATED RESTOCK NOTIFICATIONS (Proactive Sales)
        # ==============================================================================
        print("\n--- 🔔 CHECKING FOR RESTOCK ALERTS ---")
        pending_alerts = StockAlert.query.filter_by(is_notified=False).all()
        
        for alert in pending_alerts:
            product = Product.query.filter_by(name=alert.product_name).first()
            
            # If the product now has stock, notify the customer
            if product and product.available_qty > 0:
                message = f"Hi! Good news from the farm: {product.name} is back in stock ({product.available_qty} available). Would you like to place an order?"
                
                url = f"https://graph.facebook.com/v24.0/{phone_id}/messages"
                headers = {"Authorization": f"Bearer {access_token}"}
                json_data = {
                    "messaging_product": "whatsapp",
                    "to": alert.customer_phone,
                    "type": "text",
                    "text": {"body": message}
                }
                
                try:
                    response = requests.post(url, json=json_data, headers=headers)
                    if response.status_code == 200:
                        alert.is_notified = True
                        db.session.commit()
                        print(f"📧 Notification sent to {alert.customer_phone} for {product.name}")
                except Exception as e:
                    print(f"❌ Failed to notify {alert.customer_phone}: {e}")

        print("\n--- 🏁 ALL TASKS COMPLETE ---")

if __name__ == "__main__":
    run_management_tasks()