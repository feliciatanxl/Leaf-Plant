import os
import requests 
from dotenv import load_dotenv 
from flask import Blueprint, render_template, redirect, url_for, flash, request
from models import db, ContactInquiry
from .forms import ContactForm

# 1. Load environment variables from .env file
load_dotenv()

contact_bp = Blueprint('contact', __name__)

@contact_bp.route('/contact', methods=['GET', 'POST'])
def contact():
    # 2. Instantiate the form
    form = ContactForm()

    # 3. Check: Is it a POST request? AND Are all fields valid?
    if form.validate_on_submit():
        
        # --- START RECAPTCHA VERIFICATION ---
        recaptcha_response = request.form.get('g-recaptcha-response')
        google_secret_key = os.getenv('GOOGLE_RECAPTCHA_SECRET')

        # Safety Check: Ensure key exists in .env
        if not google_secret_key:
            print("Error: GOOGLE_RECAPTCHA_SECRET is missing in .env file")
            flash('System configuration error: Captcha key missing.', 'danger')
            return render_template('contact.html', form=form)

        # Verify with Google
        verify_url = "https://www.google.com/recaptcha/api/siteverify"
        payload = {
            'secret': google_secret_key,
            'response': recaptcha_response
        }

        try:
            # Ask Google: "Is this user real?"
            response = requests.post(verify_url, data=payload)
            result = response.json()

            # !!! SECURITY GATE !!!
            # If Google says "False", we STOP here. We do NOT save to DB.
            if not result.get('success'):
                flash('Recaptcha verification failed. Please check the box.', 'danger')
                return render_template('contact.html', form=form)

        except Exception as e:
            print(f"Recaptcha Connection Error: {e}")
            flash('Could not verify captcha. Please try again.', 'danger')
            return render_template('contact.html', form=form)
        # --- END RECAPTCHA VERIFICATION ---


        # 4. Only if Captcha passed, Retrieve clean data
        new_inquiry = ContactInquiry(
            name=form.name.data,
            email=form.email.data,
            phone=form.phone.data,
            message=form.message.data
        )

        try:
            # 5. Save to Database
            db.session.add(new_inquiry)
            db.session.commit()
            
            # 6. Success: Show a message
            flash('Message sent successfully! We will contact you within 24 hours.', 'success')
            
            # Redirect prevents "Resubmission" warnings
            return redirect(url_for('contact.contact'))
            
        except Exception as e:
            db.session.rollback()
            print(f"Database Error: {e}") 
            flash('An error occurred while sending your message. Please try again.', 'danger')

    # 7. Render the page
    return render_template('contact.html', form=form)