# Web Development Project AI 2025
Flask Integration with WhatsApp Business API

Step 1: python -m venv .venv <br>
Step 2: .venv\Scripts\activate <br>
Step 3: pip install -r requirements.txt <br>
<!-- pip freeze > requirements.txt -->
Step 4: python main.py (running on venv address)

*If facing policy issue: Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process

# Whatsapp Business API
Step 1: Make sure you are in your venv. <br>
Step 2: Open CMD and key in ngrok config add-authtoken $YOUR_AUTHTOKEN (direct to whatsapp folder ngrok.exe)<br>
Step 3: .\ngrok http 5000 (1st Terminal) <br>
Step 4: cd whatsapp > python app.py (2nd Terminal)

# Stripe CLI
To ensure orders are saved to the database after payment, you must run the Stripe CLI listener alongside the Flask app. <br>

Step 1: ./stripe.exe login <br>
Step 2: ./stripe.exe listen --forward-to <localhost>/orders/webhook
<!-- ./stripe.exe listen --forward-to 127.0.0.1:5001/orders/webhook -->

