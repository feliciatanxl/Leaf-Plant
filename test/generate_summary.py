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

        print(f"--- 🚀 STARTING MANAGEMENT TASKS FOR {today_date} ---")

        # ==============================================================================
        # TASK 1: GENERATE FARM PACKING LIST & COMMISSION (Censored for Privacy)
        # ==============================================================================
        farm_data = db.session.query(
            GroupLeader.name.label('leader_name'),
            WhatsAppOrder.product_name,
            db.func.sum(WhatsAppOrder.quantity).label('total_qty')
        ).join(GroupLeader, WhatsAppOrder.leader_id == GroupLeader.id)\
         .filter(db.func.strftime('%Y-%m-%d', WhatsAppOrder.timestamp) == today_date)\
         .group_by(GroupLeader.name, WhatsAppOrder.product_name).all()

        if farm_data:
            filename = f"farm_packing_list_{today_date}.csv"
            with open(filename, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(['Leader Name', 'Product', 'Total Quantity'])
                for row in farm_data:
                    writer.writerow([row.leader_name, row.product_name, row.total_qty])
            print(f"✅ CSV Report Generated: {filename}")
        else:
            print("ℹ️ No orders found to summarize for today.")


if __name__ == "__main__":
    run_management_tasks()