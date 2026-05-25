# LinkedIn Post Workflow

Runs daily at 7am UTC. Generates a LinkedIn post using OpenAI, sends it to you for approval via email, then posts it to LinkedIn.

---

## Files

| File | What it does |
|------|-------------|
| `config.json` | **The only file you edit** — change your post description & instructions here |
| `workflow.py` | The engine — don't touch this |
| `.github/workflows/run.yml` | The scheduler — don't touch this |

---

## One-Time Setup

### 1. Fork or create this repo on GitHub

### 2. Add these secrets in GitHub
Go to: **Repo → Settings → Secrets and variables → Actions → New repository secret**

| Secret | How to get it |
|--------|--------------|
| `OPENAI_API_KEY` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) |
| `GOOGLE_SHEETS_ID` | The long ID in your Google Sheet URL: `docs.google.com/spreadsheets/d/THIS_PART/edit` |
| `GOOGLE_CREDENTIALS` | See below ↓ |
| `GMAIL_ADDRESS` | Your Gmail address e.g. `you@gmail.com` |
| `GMAIL_APP_PASSWORD` | See below ↓ |
| `LINKEDIN_TOKEN` | See below ↓ |
| `LINKEDIN_PERSON_URN` | See below ↓ |

---

### Getting GOOGLE_CREDENTIALS (Service Account)
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → Enable **Google Sheets API**
3. Go to **IAM & Admin → Service Accounts → Create Service Account**
4. Give it any name → click **Create**
5. Click the service account → **Keys → Add Key → JSON**
6. Download the JSON file
7. Copy the **entire contents** of that JSON file and paste it as the `GOOGLE_CREDENTIALS` secret
8. Also **share your Google Sheet** with the service account email (it looks like `name@project.iam.gserviceaccount.com`) with **Editor** access

---

### Getting GMAIL_APP_PASSWORD
Gmail won't let scripts use your real password. Use an App Password instead:
1. Go to your Google Account → **Security**
2. Enable **2-Step Verification** (required)
3. Go to **App Passwords** (search for it)
4. Select **Mail** + **Other (Custom name)** → name it "LinkedIn Workflow"
5. Copy the 16-character password → paste as `GMAIL_APP_PASSWORD`

---

### Getting LINKEDIN_TOKEN and LINKEDIN_PERSON_URN
1. Go to [linkedin.com/developers](https://www.linkedin.com/developers/) → Create an app
2. Add the `w_member_social` permission
3. Use the OAuth 2.0 flow to get an access token → paste as `LINKEDIN_TOKEN`
4. Your Person URN: call `https://api.linkedin.com/v2/me` with your token → copy the `id` field → paste as `LINKEDIN_PERSON_URN`

---

### Set up your Google Sheet
Your sheet should have these columns in order:

| A | B | C | D | E |
|---|---|---|---|---|
| Post Description | Instructions | Image URL | Status | Row Number |

- Set **Status** to `Pending` for rows you want posted
- Set **Row Number** to the actual row number (2, 3, 4...) so the script can update it

---

## Daily Usage

1. Open `config.json`
2. Edit `post_description` and `instructions`
3. Commit and push
4. At 7am UTC the workflow runs automatically
5. You'll get an email — reply with:
   - `APPROVE` → posts it
   - `REJECT: make it shorter` → regenerates with your feedback
   - `CANCEL` → skips it
