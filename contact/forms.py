from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp

class ContactForm(FlaskForm):
    # Name: Required, 10-50 characters, Letters and spaces only
    name = StringField('Name', validators=[
        DataRequired(), 
        Length(min=10, max=50),
        Regexp(
            r'^[a-zA-Z\s]*$', 
            message="Name can only contain letters and spaces."
        )
    ])
    
    # Email: Required, Valid Format
    email = StringField('Email', validators=[DataRequired(), Email()])
    
    # Phone: Optional, Max 20, allows one '+' at start, then only digits/spaces/dashes
    phone = StringField('Phone', validators=[
        Optional(), 
        Length(min=8, max=20, message="Phone number must be at least 8 characters."),
        Regexp(
            r'^\+?[0-9\s-]*$', 
            message="Phone can only start with one '+' followed by numbers and spaces."
        )
    ])
    
    # Message: Required, Min 10 characters
    message = TextAreaField('Message', validators=[DataRequired(), Length(min=10)])
    
    submit = SubmitField('Send')