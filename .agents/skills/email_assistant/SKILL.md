---
name: email-assistant
description: CRITICAL — when the user asks to check, send, draft, or manage emails, or write SMTP/IMAP scripts: Guidelines for context-aware drafting, thread summarization, and safe email automation.
---

# Email Assistant Skill

Use this skill when the user asks to check their inbox, send messages, draft replies, summarize threads, or automate email interactions.

---

## 1. Inbox Checking & Summarization (IMAP)

When asked to check or summarize emails, write a Python script using the native `imaplib` and `email` packages. Always prioritize finding unread or recent messages, and print a clean summary:

```python
import imaplib
import email
from email.header import decode_header

# Fetch credentials from environment or config
IMAP_SERVER = "imap.gmail.com"
EMAIL_USER = "your-email@gmail.com"
EMAIL_PASS = "your-app-password"

mail = imaplib.IMAP4_SSL(IMAP_SERVER)
mail.login(EMAIL_USER, EMAIL_PASS)
mail.select("inbox")

status, messages = mail.search(None, "UNSEEN")
mail_ids = messages[0].split()

# Process the top 5 most recent unread emails
for mail_id in mail_ids[-5:]:
    _, msg_data = mail.fetch(mail_id, "(RFC822)")
    for response_part in msg_data:
        if isinstance(response_part, tuple):
            msg = email.message_from_bytes(response_part[1])
            subject, encoding = decode_header(msg["Subject"])[0]
            if isinstance(subject, bytes):
                subject = subject.decode(encoding or "utf-8")
            from_sender = msg.get("From")
            print(f"Subject: {subject}\nFrom: {from_sender}\n---")
```

---

## 2. Drafting & Sending Emails (SMTP)

Drafting emails must respect the user's tone and context.
*   **Always show the draft to the user** before calling a script to send it.
*   Use `smtplib` to send the finalized email securely via TLS:

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SMTP_SERVER = "smtp.gmail.com"
PORT = 587
EMAIL_USER = "your-email@gmail.com"
EMAIL_PASS = "your-app-password"

msg = MIMEMultipart()
msg["From"] = EMAIL_USER
msg["To"] = "recipient@example.com"
msg["Subject"] = "Drafted Subject"
msg.attach(MIMEText("Body of the email", "plain"))

server = smtplib.SMTP(SMTP_SERVER, PORT)
server.starttls()
server.login(EMAIL_USER, EMAIL_PASS)
server.sendmail(EMAIL_USER, msg["To"], msg.as_string())
server.quit()
```

---

## 3. Strict Safety & Security Rules

> [!CAUTION]
> Email handles sensitive communication and credentials. Follow these rules strictly:
>
> 1. **Never Hardcode Credentials**: Always read passwords/app-keys from environment variables (`EMAIL_PASS`) or from a secure configuration file like `~/.config/ai/email_config.json`.
> 2. **Verification Loop**: Never send emails silently. Output the draft to the screen and wait for the user to confirm with `Yes` or edit the draft.
> 3. **No Destructive Operations**: Never script bulk deletions (`FLAGS \Deleted`) or archives unless explicitly instructed by the user on a specific email ID.
