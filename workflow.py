"""
LinkedIn Post Workflow
Reads config.json, generates a post with OpenAI, sends Gmail approval,
waits for reply, then posts to LinkedIn and updates Google Sheets.
"""

import os
import json
import base64
import time
import re
import sys
import smtplib
import imaplib
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import urllib.error

# ── Load config ──────────────────────────────────────────────────────────────

with open("config.json") as f:
    config = json.load(f)

OPENAI_API_KEY        = os.environ["OPENAI_API_KEY"]
GOOGLE_SHEETS_ID      = os.environ["GOOGLE_SHEETS_ID"]
GOOGLE_CREDENTIALS    = os.environ["GOOGLE_CREDENTIALS"]   # service account JSON string
GMAIL_ADDRESS         = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD    = os.environ["GMAIL_APP_PASSWORD"]   # Gmail App Password
LINKEDIN_TOKEN        = os.environ["LINKEDIN_TOKEN"]
LINKEDIN_PERSON_URN   = os.environ["LINKEDIN_PERSON_URN"]  # urn:li:person:XXXX

POST_DESCRIPTION  = config["post_description"]
INSTRUCTIONS      = config["instructions"]
IMAGE_URL         = config.get("image_url", "")
APPROVER_EMAIL    = config["approver_email"]
SHEET_RANGE       = config.get("google_sheet_range", "Sheet1!A2:E")

# ── Helpers ───────────────────────────────────────────────────────────────────

def http_post(url, data, headers, retries=3):
    body = json.dumps(data).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 60 * (attempt + 1)  # wait 60s, then 120s
                print(f"Rate limited. Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                raise
def http_get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

# ── Step 1: Get Google Sheets OAuth token via service account ─────────────────

def get_sheets_token():
    creds = json.loads(GOOGLE_CREDENTIALS)
    now = int(time.time())
    header = base64.urlsafe_b64encode(json.dumps({"alg":"RS256","typ":"JWT"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "iss": creds["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now
    }).encode()).rstrip(b"=")

    import subprocess, tempfile
    signing_input = header + b"." + payload
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as pem:
        pem.write(creds["private_key"].encode())
        pem_path = pem.name
    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", pem_path],
        input=signing_input, capture_output=True
    )
    sig = base64.urlsafe_b64encode(result.stdout).rstrip(b"=")
    jwt = (signing_input + b"." + sig).decode()

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["access_token"]

# ── Step 2: Read pending row from Google Sheets ───────────────────────────────

def get_pending_row(token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_ID}/values/{SHEET_RANGE}"
    headers = {"Authorization": f"Bearer {token}"}
    data = http_get(url, headers)
    rows = data.get("values", [])
    # Expects columns: Post Description, Instructions, Image, Status, row_number
    for i, row in enumerate(rows):
        status = row[3] if len(row) > 3 else ""
        if status.lower() == "pending":
            return {
                "post_description": row[0] if len(row) > 0 else POST_DESCRIPTION,
                "instructions":     row[1] if len(row) > 1 else INSTRUCTIONS,
                "image_url":        row[2] if len(row) > 2 else IMAGE_URL,
                "row_number":       int(row[4]) if len(row) > 4 else i + 2,
            }
    # Fall back to config.json values if no pending row in sheet
    print("No pending row in sheet — using config.json values.")
    return {
        "post_description": POST_DESCRIPTION,
        "instructions": INSTRUCTIONS,
        "image_url": IMAGE_URL,
        "row_number": None,
    }

# ── Step 3: Generate post with OpenAI ────────────────────────────────────────

def generate_post(description, instructions, feedback=""):
    system = (
        "You are an expert LinkedIn content writer. "
        "Output ONLY the final post text, ready to publish. "
        "No explanations, no markdown, no headings. Max 1300 characters. "
        "Add relevant lowercase hashtags. Keep tone positive and professional."
    )
    user_prompt = (
        f"Post Description: {description}\n"
        f"Instructions: {instructions}\n"
    )
    if feedback:
        user_prompt += f"Feedback/Changes requested: {feedback}\n"

    data = {
        "contents": [{"parts": [{"text": system + "\n\n" + user_prompt}]}]
    }
    headers = {"Content-Type": "application/json"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={os.environ['GEMINI_API_KEY']}"
    result = http_post(url, data, headers)
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()

# ── Step 4: Send approval email via Gmail ─────────────────────────────────────

def send_approval_email(post_content, attempt=1):
    subject = f"[ACTION REQUIRED] LinkedIn Post Approval – Attempt {attempt}"
    body = f"""Hi,

Here is the generated LinkedIn post for your approval.

─────────────────────────────
{post_content}
─────────────────────────────

Please reply to this email with one of the following:

  APPROVE        → post it as-is
  REJECT: <reason> → regenerate with your feedback
  CANCEL         → skip this post

This workflow will check for your reply every 5 minutes for up to 2 hours.

Thanks!
"""
    msg = MIMEMultipart()
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = APPROVER_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, APPROVER_EMAIL, msg.as_string())
    print(f"Approval email sent to {APPROVER_EMAIL}")
    return subject

# ── Step 5: Poll Gmail inbox for reply ───────────────────────────────────────

def wait_for_reply(original_subject, timeout_minutes=120, poll_every=300):
    """Poll inbox every 5 min for up to 2 hours. Returns (decision, feedback)."""
    deadline = time.time() + timeout_minutes * 60
    re_subject = "re: " + original_subject.lower()

    print(f"Waiting for reply (timeout: {timeout_minutes} min)...")
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select("inbox")
            _, data = mail.search(None, f'(SUBJECT "{original_subject}" UNSEEN)')
            ids = data[0].split()
            for mid in ids:
                _, msg_data = mail.fetch(mid, "(RFC822)")
                msg = email_lib.message_from_bytes(msg_data[0][1])
                subj = msg.get("Subject", "").lower()
                if re_subject in subj or original_subject.lower() in subj:
                    # Extract body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors="ignore")
                    body = body.strip().split("\n")[0].strip()  # first line only
                    mail.store(mid, "+FLAGS", "\\Seen")
                    mail.logout()
                    return parse_reply(body)
            mail.logout()
        except Exception as e:
            print(f"IMAP error: {e}")
        print(f"No reply yet, checking again in {poll_every//60} min...")
        time.sleep(poll_every)

    return ("timeout", "")

def parse_reply(body):
    b = body.upper()
    if b.startswith("APPROVE"):
        return ("approve", "")
    if b.startswith("CANCEL"):
        return ("cancel", "")
    if b.startswith("REJECT"):
        feedback = body[6:].lstrip(":").strip()
        return ("reject", feedback)
    # Default: treat anything else as approve if it contains positive words
    if any(w in b for w in ["YES", "OK", "GOOD", "LOOKS GOOD"]):
        return ("approve", "")
    return ("reject", body)  # unknown → treat as feedback

# ── Step 6: Post to LinkedIn ──────────────────────────────────────────────────

def post_to_linkedin(content, image_url=""):
    headers = {
        "Authorization": f"Bearer {LINKEDIN_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }
    author = f"urn:li:person:{LINKEDIN_PERSON_URN}"

    # Download and upload image if provided
    media = None
    if image_url:
        try:
            # Register upload
            reg = http_post(
                "https://api.linkedin.com/v2/assets?action=registerUpload",
                {
                    "registerUploadRequest": {
                        "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
                        "owner": author,
                        "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
                    }
                },
                headers
            )
            upload_url = reg["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
            asset = reg["value"]["asset"]
            # Upload image bytes
            img_req = urllib.request.urlopen(image_url)
            img_data = img_req.read()
            up_req = urllib.request.Request(upload_url, data=img_data, method="PUT")
            up_req.add_header("Authorization", f"Bearer {LINKEDIN_TOKEN}")
            urllib.request.urlopen(up_req)
            media = asset
        except Exception as e:
            print(f"Image upload failed ({e}), posting without image.")

    post_body = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": content},
                "shareMediaCategory": "IMAGE" if media else "NONE",
                **({"media": [{"status": "READY", "media": media}]} if media else {})
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}
    }

    result = http_post("https://api.linkedin.com/v2/ugcPosts", post_body, headers)
    post_id = result.get("id", "unknown")
    print(f"Posted to LinkedIn: {post_id}")
    return post_id

# ── Step 7: Update Google Sheet status ───────────────────────────────────────

def update_sheet_status(token, row_number, status, post_id=""):
    if not row_number:
        return
    range_ = f"Sheet1!D{row_number}:F{row_number}"
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{GOOGLE_SHEETS_ID}/values/{range_}?valueInputOption=RAW"
    data = {"values": [[status, post_id, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")]]}
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="PUT",
          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        r.read()
    print(f"Sheet row {row_number} updated → {status}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print(f"LinkedIn Workflow started at {datetime.now(timezone.utc)}")
    print("=" * 50)

    # Get Sheets token & pending row
    token = get_sheets_token()
    row   = get_pending_row(token)

    description = row["post_description"]
    instructions = row["instructions"]
    image_url    = row["image_url"]
    row_number   = row["row_number"]

    print(f"Post description: {description[:80]}...")

    # Generate → approve loop (max 3 attempts)
    for attempt in range(1, 4):
        print(f"\n── Attempt {attempt}: Generating post...")
        post_content = generate_post(description, instructions,
                                      feedback="" if attempt == 1 else instructions)
        print(f"Generated ({len(post_content)} chars):\n{post_content}\n")

        subject = send_approval_email(post_content, attempt)
        decision, feedback = wait_for_reply(subject)
        print(f"Decision: {decision} | Feedback: {feedback or 'none'}")

        if decision == "approve":
            break
        elif decision == "cancel":
            print("Post cancelled by approver.")
            update_sheet_status(token, row_number, "Cancelled")
            sys.exit(0)
        elif decision == "timeout":
            print("No reply received in time. Cancelling.")
            update_sheet_status(token, row_number, "Timed Out")
            sys.exit(1)
        else:  # reject
            instructions = feedback  # use feedback as new instructions
    else:
        print("Max attempts reached without approval.")
        update_sheet_status(token, row_number, "Failed")
        sys.exit(1)

    # Post to LinkedIn
    post_id = post_to_linkedin(post_content, image_url)

    # Update sheet
    update_sheet_status(token, row_number, "Completed", post_id)

    print("\n✅ Done! Post published successfully.")

if __name__ == "__main__":
    main()
