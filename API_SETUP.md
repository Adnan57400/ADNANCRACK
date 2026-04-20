# CRACK SMS - API Setup Guide

## 🚀 Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Start the Bot
```bash
python bot.py
```

### 3. Start the API Server (in another terminal)
```bash
python api_server.py
```
The API server will be available at: `http://localhost:8000`

---

## 📱 Creating API Tokens

### Via Admin Panel

1. Open the bot and go to **Admin Panel** → **Advanced Tools** → **API Tokens**
2. Click **Create Token**
3. Follow 3 steps:
   - **Step 1:** Name your API token (e.g., "Website API", "Mobile App")
   - **Step 2:** Enter developer/company name (optional)
   - **Step 3:** Select which SMS panels this token can access
4. The token will be generated and displayed once - **save it securely!**

---

## 🔑 API Token Management

### View Your Tokens
- Go to **Admin Panel** → **Advanced Tools** → **API Tokens**
- See all tokens you've created with their status and last usage

### Manage Tokens
- **Active:** Token is working and can be used
- **Blocked:** Token is disabled (no API access)
- **Deleted:** Token is removed

---

## 📡 API Endpoints

### Base URL
```
https://mywebsite.com  (or http://localhost:8000 for local testing)
```

### Fetch OTPs
```http
GET /api/sms?token=YOUR_TOKEN&date=2024-01-15&limit=50
```

**Query Parameters:**
- `token` (required): Your API token
- `date` (optional): Filter by date (format: YYYY-MM-DD)
- `limit` (optional): Max records (1-500, default: 100)

**Example Response:**
```json
{
  "status": "success",
  "token_name": "Website API",
  "api_dev": "MyCompany",
  "total_records": 2,
  "data": [
    {
      "number": "+1234567890",
      "service": "WhatsApp",
      "country": "United States",
      "otp": "123456",
      "message": "Your WhatsApp code is: 123456",
      "received_at": "2024-01-15 10:30:45"
    },
    {
      "number": "+44 20 7946 0958",
      "service": "Telegram",
      "country": "United Kingdom",
      "otp": "654321",
      "message": "Your Telegram code is: 654321",
      "received_at": "2024-01-15 10:29:15"
    }
  ]
}
```

**Error Response:**
```json
{
  "detail": "Not authorized"
}
```

---

## 🌐 Web Dashboard

### Access the Dashboard
Open your browser to:
- Local: `http://localhost:8000`
- Production: `https://mywebsite.com`

### Features
- ✅ Real-time OTP display with beautiful cards
- ✅ Shows phone number, service, country, and OTP code
- ✅ Auto-refresh every 5 seconds
- ✅ Responsive design for mobile and desktop
- ✅ Smooth animations and loading indicators

---

## 🔐 Security Best Practices

1. **Keep tokens secret** - Never share your API token publicly
2. **Use HTTPS only** - Always use HTTPS in production
3. **Rotate tokens regularly** - Create new tokens and revoke old ones
4. **Limit panel access** - Only grant token access to necessary panels
5. **Monitor usage** - Check token usage in the admin panel

---

## 🛠️ cURL Examples

### Fetch Latest OTPs
```bash
curl -X GET "http://localhost:8000/api/sms?token=YOUR_TOKEN&limit=100"
```

### Fetch OTPs by Date
```bash
curl -X GET "http://localhost:8000/api/sms?token=YOUR_TOKEN&date=2024-01-15&limit=50"
```

### Check API Health
```bash
curl -X GET "http://localhost:8000/health"
```

---

## 📚 Integration Examples

### JavaScript/Node.js
```javascript
async function getOTPs(token) {
  const response = await fetch(`/api/sms?token=${token}&limit=50`);
  const data = await response.json();
  console.log(data);
}

getOTPs('your_token_here');
```

### Python
```python
import requests

token = 'your_token_here'
response = requests.get(f'http://localhost:8000/api/sms?token={token}&limit=50')
data = response.json()
print(data)
```

### PHP
```php
$token = 'your_token_here';
$url = "http://localhost:8000/api/sms?token={$token}&limit=50";
$response = file_get_contents($url);
$data = json_decode($response, true);
print_r($data);
```

---

## 📊 Response Format

All API responses follow this structure:

```json
{
  "status": "success|error",
  "token_name": "Display name of the token",
  "api_dev": "Developer name",
  "total_records": 42,
  "data": [
    {
      "number": "+1234567890",
      "service": "WhatsApp",
      "country": "United States",
      "otp": "123456",
      "message": "Full message text",
      "received_at": "2024-01-15 10:30:45"
    }
  ]
}
```

---

## 🐛 Troubleshooting

### "Not authorized" Error
- Check your token is correct
- Verify token status is "ACTIVE"
- Ensure the token hasn't expired or been blocked

### No OTPs in Response
- Check if OTPs are actually being received
- Verify your panels are connected and receiving messages
- Check the date filter if using one

### Connection Refused
- Make sure API server is running (`python api_server.py`)
- Check port 8000 is not blocked
- For production, ensure HTTPS is configured

---

## 📞 Support

For issues or questions:
1. Check the [API Documentation](http://localhost:8000/api/docs)
2. Review the dashboard logs
3. Check panel status in Admin Panel
4. Contact: support@cracksms.com

---

## 📄 License

CRACK SMS API © 2024. All rights reserved.
