import os
from dotenv import load_dotenv

# 1. Print where Python is currently looking
print(f"Current Working Directory: {os.getcwd()}")

# 2. Try to load the .env file
result = load_dotenv()
print(f"Did load_dotenv find a file? -> {result}")

# 3. Try to read the key
key = os.getenv('GOOGLE_RECAPTCHA_SECRET')
print(f"The Secret Key is: {key}")

if key:
    print("✅ SUCCESS! The file works.")
else:
    print("❌ FAILURE! Python cannot read the key.")
    # Check if the file is accidentally named .env.txt
    if os.path.exists('.env.txt'):
        print("⚠️  FOUND THE ISSUE: Your file is named '.env.txt' (hidden extension). Rename it to just '.env'")