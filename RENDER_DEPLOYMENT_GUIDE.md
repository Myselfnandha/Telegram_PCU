# 🌐 24/7 Free Private Deployment Guide for TG Power Suite (Render.com)

Deploy TG Power Suite 24/7 on **Render.com** without paying a cent or entering any credit card information, fully secured so only your allowed devices can access it.

---

## 📋 Architecture Highlights

- **Hosting**: Render.com Free Web Service (750 free hours/month = 24/7 continuous uptime).
- **Zero OOM / Memory Failures**: Built via GitHub Actions runner (16GB RAM) & published to GitHub Container Registry (GHCR).
- **24/7 Sleep Prevention**: Free keep-alive ping on `/api/system/health` every 10 minutes (via cron-job.org).
- **Private Device-Only Access**: Strict `API_AUTH_TOKEN` protection. Only devices with your secret key can view or stream media.
- **Permanent Session**: Powered by `TG_SESSION_STRING` so Telegram never logs out when containers restart.

---

## 🚀 Step 1: Push Changes to GitHub

From your repository directory:

```bash
cd /home/nandha/Desktop/desktop/TG_TOOL

# Stage the new deployment configuration
git add Dockerfile .github/workflows/deploy.yml render.yaml backend/export_session_string.py

# Commit and push to main
git commit -m "feat: add 24/7 Render deployment pipeline and GHCR workflow"
git push origin main
```

When pushed, GitHub Actions will automatically start building the multi-stage Docker container and publish it to `ghcr.io`.
You can view build progress under your GitHub repository's **Actions** tab.

> **Note on GHCR Package Visibility**:
> By default, GitHub Container Registry packages may be private. In your GitHub Profile > **Packages** > `tg_tool` > **Package settings** > change visibility to **Public** so Render can pull the image without credentials (or supply a GitHub Personal Access Token in Render).

---

## ☁️ Step 2: Create Web Service on Render.com

1. Sign up or log into [Render.com](https://render.com) (no credit card required).
2. Click **New +** > **Web Service**.
3. Choose either option:
   - **Option A (Direct Git Repo)**: Connect your GitHub repo (`TG_TOOL`), select runtime **Docker**, and click Deploy.
   - **Option B (Pre-built Image via GHCR)**: Select **Deploy an existing image** and enter:
     ```text
     ghcr.io/myselfnandha/tg_tool:latest
     ```
4. Choose the **Free** instance type (512MB RAM, 0.1 CPU).
5. Scroll down to **Environment Variables** and add:

| Key | Value | Description |
| :--- | :--- | :--- |
| `PORT` | `8088` | Internal gateway port |
| `HOST` | `0.0.0.0` | Container bind address |
| `TG_API_ID` | `30662321` | Telegram API App ID |
| `TG_API_HASH` | `cf007e0155c41fd1aa9b114b592377e0` | Telegram API Hash |
| `TG_PHONE` | `+919489890511` | Your Telegram Phone |
| `ENABLE_API_AUTH` | `true` | Enforces private access token |
| `API_AUTH_TOKEN` | `<Create-A-Strong-Secret-Password>` | Your private device access password |
| `TG_SESSION_STRING` | *(Paste session string from Step 3)* | Persistent session (avoids re-login) |

6. Click **Create Web Service**. Render will deploy your service and assign a permanent URL:
   `https://tg-power-suite-xxxx.onrender.com`

---

## 🔑 Step 3: Telegram Session Persistence

To ensure Telegram stays permanently logged in even when the cloud container restarts:

### Method A: Use Your Existing Local Session (Instant)
Run the newly created export tool on your local machine:
```bash
./venv/bin/python backend/export_session_string.py
```
Copy the printed string and paste it into Render under the `TG_SESSION_STRING` environment variable.

### Method B: QR Code Login
1. Open your Render URL in your browser: `https://<your-app>.onrender.com`.
2. Enter your `API_AUTH_TOKEN` to unlock the dashboard.
3. Open Telegram on your phone > **Settings** > **Devices** > **Link Desktop Device**.
4. Scan the QR code displayed on the screen.

---

## ⏰ Step 4: Keep It Running 24/7 (Prevent Sleeping)

Render free tier containers spin down after 15 minutes without incoming traffic. Keep it awake 24/7 for free:

1. Create a free account at [cron-job.org](https://cron-job.org) (or [uptimerobot.com](https://uptimerobot.com)).
2. Click **Create Cronjob**:
   - **Title**: `TG Power Suite 24/7 Keep-Alive`
   - **URL**: `https://<your-render-app>.onrender.com/api/system/health`
   - **Execution Schedule**: Every **10 minutes**
   - **HTTP Method**: `GET`
3. Save the job.
4. Render's health endpoint returns `{"status":"ok"}` without exposing any user data, keeping the instance active 24/7/365.

---

## 📱 Step 5: Access from Allowed Devices

### 1. Mobile (Android & iOS)
- Open `https://<your-render-app>.onrender.com` in Chrome or Safari.
- Enter your `API_AUTH_TOKEN`.
- Tap **Add to Home screen** to use it as a standalone progressive web app (PWA).
- Stream videos directly in the browser player or tap the VLC icon to stream through VLC for Android / iOS.

### 2. PC / Laptop (Linux, Windows, Mac)
- Open `https://<your-render-app>.onrender.com`.
- Access media sniffer, video streaming with instant seeking, and download queue controls.
- Any request from an unauthenticated device or bot without your `API_AUTH_TOKEN` is automatically rejected with `401 Unauthorized`.
