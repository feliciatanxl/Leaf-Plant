import requests

# Use your actual ngrok URL here
url = "https://chokeable-undefaced-tess.ngrok-free.dev/webhook"

data = {
    "entry": [{
        "changes": [{
            "value": {
                "messages": [{
                    "from": "6591540822",
                    "id": "test_123",
                    "type": "text",
                    "text": {"body": "Do you have tomatoes?"}
                }]
            }
        }]
    }]
}

try:
    response = requests.post(url, json=data)
    print(f"Status Code: {response.status_code}")
    print(f"Response Body: {response.text}")
except Exception as e:
    print(f"Error: {e}")