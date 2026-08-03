"""Email notifications service for Talent Sphere Elevate."""

from __future__ import annotations

import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SENDER = os.getenv("SMTP_SENDER", "Talent Sphere Elevate <noreply@company.com>")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "True").lower() in ("true", "1", "yes")


def _send_email_async(subject: str, html_body: str, text_body: str, recipients: list[str]) -> None:
    """Internal function to send emails. Run in a background thread."""
    if not recipients:
        return

    def safe_print(text: str) -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            try:
                # Replace unsupported characters with standard equivalents or "?"
                print(text.encode(sys.stdout.encoding or "ascii", errors="replace").decode(sys.stdout.encoding or "ascii"))
            except Exception:
                # Fallback to ascii replacement
                print(text.encode("ascii", errors="replace").decode("ascii"))

    import sys
    from src.exams import add_email_log, update_email_log

    # Check if SMTP is configured
    if not SMTP_SERVER or not SMTP_USERNAME or not SMTP_PASSWORD:
        safe_print("\n" + "="*80)
        safe_print(" [EMAIL SERVICE DRAFT / FALLBACK MODE]")
        safe_print(f" Subject: {subject}")
        safe_print(f" Recipients: {', '.join(recipients)}")
        safe_print("-"*80)
        safe_print(" Content:")
        safe_print(text_body)
        safe_print("="*80 + "\n")
        
        # Log in database as fallback mode
        for recipient in recipients:
            add_email_log(recipient, subject, "fallback", "SMTP not configured - output printed to server logs")
        return

    try:
        # Connect to server
        if SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=15.0)
        else:
            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15.0)

        if SMTP_USE_TLS and SMTP_PORT != 465:
            server.starttls()

        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        
        success_count = 0
        for recipient in recipients:
            log_id = add_email_log(recipient, subject, "pending")
            try:
                # Create message container for this specific recipient (for privacy and reliability)
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = SMTP_SENDER
                msg["To"] = recipient

                msg.attach(MIMEText(text_body, "plain", "utf-8"))
                msg.attach(MIMEText(html_body, "html", "utf-8"))
                
                server.sendmail(SMTP_SENDER, [recipient], msg.as_string())
                success_count += 1
                if log_id:
                    update_email_log(log_id, "sent")
            except Exception as item_err:
                err_msg = str(item_err)
                safe_print(f"[MAIL ERROR] Failed to deliver email to {recipient}: {err_msg}")
                if log_id:
                    update_email_log(log_id, "failed", err_msg)
                
        server.quit()
        safe_print(f"[MAIL] Email notifications successfully sent to {success_count}/{len(recipients)} user(s).")
    except Exception as e:
        err_msg = f"SMTP connection/login failed: {e}"
        safe_print(f"[MAIL ERROR] {err_msg}")
        for recipient in recipients:
            add_email_log(recipient, subject, "failed", err_msg)


def broadcast_announcement(title: str, content: str) -> None:
    """Send an announcement notification email to all registered trainees in a background thread."""
    # Import locally to avoid circular dependency issues
    from src.users import get_all_users
    from flask import has_request_context, request
    from src.exams import get_system_setting

    # Check if email broadcasts are enabled
    if get_system_setting("email_notifications_enabled", "true").lower() != "true":
        return

    users = get_all_users()
    emails = [u["email"] for u in users if u.get("email") and u.get("role") != "admin"]

    if not emails:
        return

    # Resolve application base URL
    app_url = os.getenv("APP_URL", "")
    if not app_url:
        if has_request_context():
            # Check for proxy headers (Cloudflare Tunnels, ngrok, etc.)
            forwarded_host = request.headers.get("X-Forwarded-Host")
            forwarded_proto = request.headers.get("X-Forwarded-Proto", "http")
            if forwarded_host:
                app_url = f"{forwarded_proto}://{forwarded_host.strip()}"
            else:
                app_url = request.url_root.rstrip('/')
        else:
            app_url = "http://localhost:5000"
    else:
        app_url = app_url.rstrip('/')

    subject = f"📢 Talent Sphere: {title}"
    announcements_url = f"{app_url}/announcements"

    # Generate styled HTML email body
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                background-color: #f4f6f9;
                margin: 0;
                padding: 0;
                -webkit-font-smoothing: antialiased;
            }}
            .email-container {{
                max-width: 600px;
                margin: 40px auto;
                background-color: #ffffff;
                border-radius: 16px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                border: 1px solid #e1e8ed;
                overflow: hidden;
            }}
            .email-header {{
                background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                padding: 30px;
                text-align: center;
                color: #ffffff;
            }}
            .email-header h1 {{
                margin: 0;
                font-size: 24px;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}
            .email-body {{
                padding: 40px 30px;
                color: #334155;
                line-height: 1.6;
            }}
            .announcement-card {{
                background-color: #f8fafc;
                border-left: 4px solid #3b82f6;
                padding: 20px;
                border-radius: 0 12px 12px 0;
                margin: 24px 0;
            }}
            .announcement-title {{
                font-size: 18px;
                font-weight: 700;
                color: #1e293b;
                margin-top: 0;
                margin-bottom: 8px;
            }}
            .announcement-text {{
                font-size: 15px;
                color: #475569;
                margin: 0;
                white-space: pre-wrap;
            }}
            .btn-action {{
                display: inline-block;
                background-color: #2563eb;
                color: #ffffff !important;
                text-decoration: none;
                padding: 12px 28px;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
                margin: 20px 0 10px 0;
                text-align: center;
            }}
            .email-footer {{
                background-color: #f1f5f9;
                padding: 20px 30px;
                text-align: center;
                font-size: 12px;
                color: #64748b;
                border-top: 1px solid #e2e8f0;
            }}
        </style>
    </head>
    <body>
        <div class="email-container">
            <div class="email-header">
                <h1>📢 Talent Sphere Elevate</h1>
            </div>
            <div class="email-body">
                <p>Hello,</p>
                <p>A new system update has been posted by the Administrator:</p>
                
                <div class="announcement-card">
                    <div class="announcement-title">{title}</div>
                    <p class="announcement-text">{content}</p>
                </div>
                
                <p style="text-align: center;">
                    <a href="{announcements_url}" class="btn-action">Open Dashboard</a>
                </p>
                
                <p style="margin-top: 30px; font-size: 14px;">Best regards,<br><b>Talent Sphere Support Team</b></p>
            </div>
            <div class="email-footer">
                This is an automated notification. Please do not reply directly to this email.<br>
                &copy; 2026 Talent Sphere Elevate. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

    # Generate plain text email body for compatibility
    text_body = (
        f"Talent Sphere Elevate — New Announcement\n\n"
        f"Title: {title}\n\n"
        f"Content:\n{content}\n\n"
        f"Open Dashboard to view: {announcements_url}"
    )

    # Spawn thread to send email asynchronously
    thread = threading.Thread(
        target=_send_email_async,
        args=(subject, html_body, text_body, emails),
        daemon=True
    )
    thread.start()


def send_study_plan_assignment_email(emails: list[str], plan_title: str, domain: str, week_number: int) -> None:
    """Send an email notification to assigned trainees when a new study plan is assigned."""
    if not emails:
        return
        
    from flask import has_request_context, request
    from src.exams import get_system_setting

    if get_system_setting("email_notifications_enabled", "true").lower() != "true":
        return

    app_url = os.getenv("APP_URL", "")
    if not app_url:
        if has_request_context():
            forwarded_host = request.headers.get("X-Forwarded-Host")
            scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
            if forwarded_host:
                app_url = f"{scheme}://{forwarded_host}"
            else:
                app_url = request.url_root.rstrip('/')
        else:
            app_url = "http://127.0.0.1:5000"

    sprint_url = f"{app_url.rstrip('/')}/sprint"
    subject = f"📚 New Study Plan Assigned: {plan_title} (Week {week_number})"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
            .card {{ background-color: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; max-width: 600px; margin: auto; }}
            .header {{ font-size: 20px; font-weight: bold; color: #6366f1; margin-bottom: 12px; }}
            .badge {{ background: rgba(99,102,241,0.15); color: #6366f1; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; display: inline-block; margin-bottom: 16px; }}
            .btn {{ display: inline-block; background-color: #6366f1; color: #ffffff !important; padding: 12px 24px; border-radius: 8px; font-weight: bold; text-decoration: none; margin-top: 20px; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div class="header">📚 New Study Plan Assigned</div>
            <div class="badge">{domain.capitalize()} &middot; Week {week_number}</div>
            <p>You have been assigned a new AI-orchestrated Agile Sprint Study Plan:</p>
            <h3 style="color: #f8fafc; margin-top: 8px;">{plan_title}</h3>
            <p style="color: #94a3b8; font-size: 14px;">Log into your dashboard to access your day-by-day learning modules, source documents, Day 5 Gateway Exam, and Day 6 Voice Interview.</p>
            <a href="{sprint_url}" class="btn">🚀 Start Study Plan Now</a>
        </div>
    </body>
    </html>
    """

    text_body = f"""New Study Plan Assigned: {plan_title} (Week {week_number})

Domain: {domain.capitalize()}
Week Number: {week_number}

Log in to start your learning sprint: {sprint_url}
"""

    thread = threading.Thread(
        target=_send_email_async,
        args=(subject, html_body, text_body, emails),
        daemon=True
    )
    thread.start()


def send_user_credentials(email: str, name: str, employee_id: str, password_plain: str) -> None:
    """Send an onboarding email containing the user's new login credentials in a background thread."""
    from flask import has_request_context, request
    from src.exams import get_system_setting

    # Check if email notifications are enabled
    if get_system_setting("email_notifications_enabled", "true").lower() != "true":
        return

    # Resolve application base URL
    app_url = os.getenv("APP_URL", "")
    if not app_url:
        if has_request_context():
            # Check for proxy headers
            forwarded_host = request.headers.get("X-Forwarded-Host")
            forwarded_proto = request.headers.get("X-Forwarded-Proto", "http")
            if forwarded_host:
                app_url = f"{forwarded_proto}://{forwarded_host.strip()}"
            else:
                app_url = request.url_root.rstrip('/')
        else:
            app_url = "http://localhost:5000"
    else:
        app_url = app_url.rstrip('/')

    subject = "🔑 Welcome to Talent Sphere Elevate: Your Login Credentials"
    login_url = f"{app_url}/login"

    # Generate styled HTML email body
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
                background-color: #f4f6f9;
                margin: 0;
                padding: 0;
                -webkit-font-smoothing: antialiased;
            }}
            .email-container {{
                max-width: 600px;
                margin: 40px auto;
                background-color: #ffffff;
                border-radius: 16px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                border: 1px solid #e1e8ed;
                overflow: hidden;
            }}
            .email-header {{
                background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
                padding: 30px;
                text-align: center;
                color: #ffffff;
            }}
            .email-header h1 {{
                margin: 0;
                font-size: 24px;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}
            .email-body {{
                padding: 40px 30px;
                color: #334155;
                line-height: 1.6;
            }}
            .credentials-card {{
                background-color: #f8fafc;
                border: 1px dashed #3b82f6;
                padding: 20px;
                border-radius: 12px;
                margin: 24px 0;
            }}
            .credentials-item {{
                font-size: 15px;
                color: #475569;
                margin: 8px 0;
            }}
            .credentials-item b {{
                color: #1e293b;
            }}
            .btn-action {{
                display: inline-block;
                background-color: #2563eb;
                color: #ffffff !important;
                text-decoration: none;
                padding: 12px 28px;
                border-radius: 8px;
                font-weight: 600;
                font-size: 15px;
                margin: 20px 0 10px 0;
                text-align: center;
            }}
            .email-footer {{
                background-color: #f1f5f9;
                padding: 20px 30px;
                text-align: center;
                font-size: 12px;
                color: #64748b;
                border-top: 1px solid #e2e8f0;
            }}
        </style>
    </head>
    <body>
        <div class="email-container">
            <div class="email-header">
                <h1>🔑 Account Activated</h1>
            </div>
            <div class="email-body">
                <p>Hello {name},</p>
                <p>An account has been created for you on the Talent Sphere Elevate training portal. You can now log in using the credentials below:</p>
                
                <div class="credentials-card">
                    <div class="credentials-item"><b>Login URL:</b> <a href="{login_url}">{login_url}</a></div>
                    <div class="credentials-item"><b>Employee ID (Username):</b> <code>{employee_id}</code></div>
                    <div class="credentials-item"><b>Temporary Password:</b> <code>{password_plain}</code></div>
                </div>
                
                <p style="text-align: center;">
                    <a href="{login_url}" class="btn-action">Log In Now</a>
                </p>
                
                <p style="margin-top: 30px; font-size: 14px;">Best regards,<br><b>Talent Sphere Support Team</b></p>
            </div>
            <div class="email-footer">
                This is an automated notification. Please do not reply directly to this email.<br>
                &copy; 2026 Talent Sphere Elevate. All rights reserved.
            </div>
        </div>
    </body>
    </html>
    """

    # Generate plain text email body for compatibility
    text_body = (
        f"Welcome to Talent Sphere Elevate!\n\n"
        f"An account has been created for you. Here are your credentials:\n"
        f"- Login URL: {login_url}\n"
        f"- Employee ID: {employee_id}\n"
        f"- Temporary Password: {password_plain}\n\n"
        f"Log in here: {login_url}"
    )

    # Spawn thread to send email asynchronously
    thread = threading.Thread(
        target=_send_email_async,
        args=(subject, html_body, text_body, [email]),
        daemon=True
    )
    thread.start()
