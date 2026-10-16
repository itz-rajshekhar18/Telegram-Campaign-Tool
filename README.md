# Telegram Campaign Tool — Ready Backend

Local campaign system for your Asus Vivobook.  
Designed for easy Telegram account connection and same-WiFi Mini App control.

## 1. Download & Install Backend (Windows)

1. Unzip this folder on your PC.
2. Double-click **`install.bat`**  
   (It creates a virtual environment and installs all dependencies.)
3. Open the `.env` file that was created and fill in:
   ```
   API_ID=your_number
   API_HASH=your_hash
   ```
   Get these from https://my.telegram.org (log in with the account you want to use).

## 2. Connect Your Telegram Account (dkk_888)

1. Open Command Prompt in the project folder.
2. Activate the environment:
   ```
   venv\Scripts\activate
   ```
3. Run the login helper:
   ```
   python login.py
   ```
4. Enter the phone number linked to **dkk_888**.
5. Enter the code Telegram sends you.
6. If you have 2FA, enter the password when asked.
7. Session will be saved as `sessions/default.session`.

## 3. Start the Backend

```
python -m app.main
```
It runs on http://0.0.0.0:8000

## 4. Setup the Mini App (Same WiFi)

1. On your laptop, run `ipconfig` and note the IPv4 address (example: `192.168.1.105`).
2. Open `static/miniapp.html` in Notepad.
3. Change this line:
   ```js
   const API_BASE = "http://192.168.1.100:8000";
   ```
   to your real IP:
   ```js
   const API_BASE = "http://192.168.1.105:8000";
   ```
4. Save the file.
5. Make sure your phone and laptop are on the **same WiFi**.
6. Ways to open the Mini App:
   - Easiest for testing: Use a simple local file server or just open the HTML and test the API connection.
   - For real Telegram Mini App: Create a bot with @BotFather → Bot Settings → Menu Button / Web App and point it to a URL that serves `miniapp.html`. For pure local same-WiFi use, many people temporarily use a tunnel or serve it from the FastAPI backend (already available at `/miniapp`).

   You can test the interface by opening this on your phone browser (same WiFi):
   ```
   http://YOUR_LAPTOP_IP:8000/miniapp
   ```

## 5. Start Using

1. Open the Mini App / status page.
2. Fill in a campaign (name, objective, Information Bank, keywords).
3. Choose rate profile (start with **Balanced**).
4. Click **Create Campaign** then **Start**.
5. The worker will search groups, join with delays, and reply using Ollama (if running).
6. View leads and click **Export Leads (CSV)**.

## Optional: Enable AI Replies

Install Ollama from https://ollama.com then:
```
ollama pull llama3.1:8b
ollama serve
```

## Important Notes

- Use a dedicated account if possible.
- Start with Safe or Balanced profile to reduce ban risk.
- The tool only works while the backend is running on your laptop.
- Same WiFi only — no public exposure by default.

## Files

- `install.bat` — one-click installer
- `login.py` — easy Telegram account connection
- `app/main.py` — backend server
- `static/miniapp.html` — Mini App frontend
- `README.md` — this guide
