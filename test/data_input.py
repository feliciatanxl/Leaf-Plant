# from models import db, Product, GroupLeader, Customer
# from whatsapp.app import app 

# def seed_data():
#     with app.app_context():
#         # --- 1. SEED PRODUCTS ---
#         crops = [
#             {"name": "Mao Bai", "price": 3.40, "cat": "Vegetable"},
#             {"name": "Jiu Bai Cai", "price": 3.30, "cat": "Vegetable"},
#             {"name": "Xiao Bai Cai", "price": 2.80, "cat": "Vegetable"},
#             {"name": "Red Pak Choy", "price": 4.20, "cat": "Vegetable"},
#             {"name": "Cos Lettuce", "price": 3.00, "cat": "Lettuce"},
#             {"name": "Lollo Bionda Lettuce", "price": 3.20, "cat": "Lettuce"},
#             {"name": "Chris Green Lettuce", "price": 2.80, "cat": "Lettuce"},
#             {"name": "Mizuna", "price": 2.50, "cat": "Herbs"}
#         ]

#         for crop in crops:
#             existing_p = Product.query.filter_by(name=crop["name"]).first()
#             if not existing_p:
#                 new_item = Product(
#                     name=crop["name"],
#                     price=crop["price"],
#                     category=crop["cat"],  # <-- ADD THIS LINE
#                     available_qty=100,
#                     status='In Stock',
#                     image_file='default_product.jpg'
#                 )
#                 db.session.add(new_item)

#         # --- 2. SEED TEST GROUP LEADER ---
#         # We need a leader first so the customer has someone to belong to
#         test_leader = GroupLeader.query.filter_by(name="Test Leader").first()
#         if not test_leader:
#             test_leader = GroupLeader(
#                 name="Test Leader",
#                 area="Singapore Central"
#             )
#             db.session.add(test_leader)
#             db.session.commit() # Commit here so we get the ID for the leader

#         # --- 3. SEED YOURSELF AS CUSTOMER ---
#         # IMPORTANT: Use the exact phone number you use for WhatsApp (with country code)
#         your_phone = "6591540822" # <-- CHANGE THIS to your WhatsApp number
#         your_name = "Felicia"

#         existing_c = Customer.query.filter_by(phone=your_phone).first()
#         if not existing_c:
#             new_customer = Customer(
#                 name=your_name,
#                 phone=your_phone,
#                 email="test@example.com",
#                 leader_id=test_leader.id  
#             )
#             db.session.add(new_customer)
#             print(f"👤 Added {your_name} as a registered customer.")
#         else:
#             print(f"ℹ️ {your_name} already exists in the database.")

#         db.session.commit()
#         print("✅ Database seeding complete.")

# if __name__ == "__main__":
#     seed_data()

# from models import db, GroupLeader, Customer, WhatsAppOrder
# from whatsapp.app import app 
# from datetime import datetime
# import pytz

# def seed_test_user_and_order():
#     with app.app_context():
#         # --- 1. ENSURE A LEADER EXISTS ---
#         test_leader = GroupLeader.query.filter_by(name="Test Leader").first()
#         if not test_leader:
#             test_leader = GroupLeader(
#                 name="Test Leader",
#                 phone="+6500000000", # Using the + format for consistency
#                 area="Singapore Central"
#             )
#             db.session.add(test_leader)
#             db.session.commit()
#             print("✅ Created Test Leader.")

#         # --- 2. SEED CUSTOMER ---
#         your_phone = "+6580262125" # Example phone
#         your_name = "Jane Doe"

#         customer = Customer.query.filter_by(phone=your_phone).first()
#         if not customer:
#             customer = Customer(
#                 name=your_name,
#                 phone=your_phone,
#                 email="jane.doe@example.com",
#                 address="Blk 123, Jurong East St 12, #04-56", # Added address
#                 leader_id=test_leader.id,
#                 password_hash="hashed_placeholder", # Required by your model
#                 role='user'
#             )
#             db.session.add(customer)
#             print(f"👤 Created Customer: {your_name}")
#         else:
#             customer.leader_id = test_leader.id
#             print(f"ℹ️ Customer {your_name} updated.")

#         db.session.commit() # Commit to get customer.id

#         # --- 3. SEED TEST WHATSAPP ORDER ---
#         # We create a pending order to simulate a fresh WhatsApp input
#         test_order = WhatsAppOrder.query.filter_by(customer_id=customer.id, status="Pending").first()
        
#         if not test_order:
#             new_order = WhatsAppOrder(
#                 customer_id=customer.id,
#                 items="2x Kale, 1x Lettuce", # The text string your bot usually parses
#                 total_price=15.50,
#                 status="Pending",
#                 created_at=datetime.now(pytz.timezone('Asia/Singapore'))
#             )
#             db.session.add(new_order)
#             print(f"📦 SUCCESS: Added test Pending order for {your_name}.")
#         else:
#             print(f"ℹ️ Pending order already exists for {your_name}.")

#         db.session.commit()
#         print("✅ Seeding complete. You can now test the WhatsApp bot flow.")

# if __name__ == "__main__":
#     seed_test_user_and_order()


import sys
import os
from datetime import datetime
import pytz

# Add parent directory to path to find models
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models import db, GroupLeader, Customer, WhatsAppOrder
from whatsapp.app import app 

def get_sg_time():
    return datetime.now(pytz.timezone('Asia/Singapore'))

def seed_everything():
    with app.app_context():
        # --- 1. SEED LEADER ---
        leader = GroupLeader.query.filter_by(name="Test Leader").first()
        if not leader:
            leader = GroupLeader(
                name="Test Leader",
                phone="+6590000000",
                area="Singapore Central"
            )
            db.session.add(leader)
            db.session.commit()
            print("✅ Created Test Leader.")

        # --- 2. SEED CUSTOMER ---
        # Ensure this phone matches exactly what you use in your test
        target_phone = "650000000" 
        customer = Customer.query.filter_by(phone=target_phone).first()
        
        if not customer:
            customer = Customer(
                name="Test User",
                phone=target_phone,
                email="test@example.com",
                leader_id=leader.id,
                password_hash="pbkdf2:sha256:...", # Required placeholder
                role='user'
            )
            db.session.add(customer)
            db.session.commit()
            print(f"👤 Created Customer: {customer.name}")

        # --- 3. SEED WHATSAPP ORDER ---
        # Linking to both the customer and their leader
        new_order = WhatsAppOrder(
            customer_id=customer.id,
            leader_id=leader.id,
            customer_phone=customer.phone,
            product_name="Organic Kale",
            quantity=2,
            total_price=11.00,
            commission_earned=1.10,
            order_status='New Order',
            timestamp=get_sg_time()
        )

        try:
            db.session.add(new_order)
            db.session.commit()
            print(f"📦 SUCCESS: Order created for {customer.name}!")
        except Exception as e:
            db.session.rollback()
            print(f"❌ DB Error: {e}")

if __name__ == "__main__":
    seed_everything()