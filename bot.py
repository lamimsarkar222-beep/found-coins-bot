import os
from flask import Flask, request
import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Found Coins Bot is running!"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            requests.post(
                f"{API}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": "🎁 Welcome to Found Coins!\n\n🪙 Your Coins: 0"
                }
            )

    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
