import requests
import base64
import json

with open("test_image.jpeg", "rb") as f:
    image_b64 = base64.b64encode(f.read()).decode()

payload = {
    "processParam": {
        "scenario": "FullProcess"
    },
    "List": [
        {
            "ImageData": {
                "image": image_b64
            },
            "light": 6,
            "page_idx": 0
        }
    ]
}

response = requests.post("http://localhost:8080/api/process", json=payload)
print(json.dumps(response.json(), indent=2))