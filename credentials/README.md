# Credentials folder (NEVER commit real files from here)

## YouTube publishing (optional)
To enable "Publish to YouTube":

1. Go to https://console.cloud.google.com and create a free project.
2. Enable "YouTube Data API v3" (APIs & Services -> Library).
3. OAuth consent screen -> External -> add your own Google account as Test User.
4. Create Credentials -> OAuth Client ID -> Application type: **Desktop app**.
5. Download the JSON and save it in this folder as:
       client_secret.json
   (Any client_secret*.json name in this folder is also detected.)

Tokens are stored in credentials/tokens/ after you authorize - also never
committed. Each upload uses ~1,600 quota units of the default 10,000/day.
