from __future__ import annotations

import logging
import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional, Union

logger = logging.getLogger("asok.mail")


class Mail:
    """Simple email sender using smtplib.

    Emails are sent in a background thread by default once the message is built.
    """

    @staticmethod
    def _cfg(key: str, default: Optional[str] = None) -> Optional[str]:
        return os.environ.get(key, default)

    @staticmethod
    def _do_send(
        sender: str,
        all_recipients: list[str],
        msg_string: str,
        host: str,
        port: int,
        username: Optional[str],
        password: Optional[str],
        use_tls: bool,
        raise_on_error: bool = False,
    ) -> None:
        try:
            with smtplib.SMTP(host, port) as server:
                if use_tls:
                    server.starttls()
                if username and password:
                    server.login(username, password)
                server.sendmail(sender, all_recipients, msg_string)
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            if raise_on_error:
                from .exceptions import MailError

                raise MailError(f"Failed to send email: {e}") from e

    @staticmethod
    def _validate_email(email: str) -> str:
        """Validate and sanitize email address to prevent header injection.

        SECURITY: Strict email validation prevents SMTP header injection attacks
        via malformed email addresses containing CRLF, spaces, or control chars.
        """
        import re

        # Remove any whitespace
        email = email.strip()

        # SECURITY: Strict email format validation
        # Must be: alphanumeric + allowed chars @ domain.tld
        email_pattern = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
        if not email_pattern.match(email):
            raise ValueError(f"Invalid email address: {email}")

        # Additional check for control characters
        if any(ord(c) < 32 or ord(c) == 127 for c in email):
            raise ValueError(f"Email contains control characters: {email}")

        return email

    @staticmethod
    def _sanitize(val: Union[str, list[str]]) -> Any:
        """Strip control characters (CR/LF) from header values to prevent injection."""
        if isinstance(val, list):
            return [v.replace("\n", "").replace("\r", "").strip() for v in val]
        if isinstance(val, str):
            return val.replace("\n", "").replace("\r", "").strip()
        return val

    @staticmethod
    def send(
        to: Union[str, list[str]],
        subject: str,
        body: str,
        html: Optional[str] = None,
        from_addr: Optional[str] = None,
        cc: Optional[Union[str, list[str]]] = None,
        bcc: Optional[Union[str, list[str]]] = None,
        sync: bool = False,
    ) -> threading.Thread | None:
        """Send an email (in a background thread by default).

        Returns the thread instance if async, or None if sync.

        SECURITY: Limits on recipients and content size prevent abuse.
        """
        host = Mail._cfg("MAIL_HOST", "localhost")
        port = int(Mail._cfg("MAIL_PORT", "587"))
        username = Mail._cfg("MAIL_USERNAME")
        password = Mail._cfg("MAIL_PASSWORD")
        sender = from_addr or Mail._cfg("MAIL_FROM", username or "noreply@localhost")
        use_tls = Mail._cfg("MAIL_TLS", "true").lower() != "false"

        # SECURITY: Validate and sanitize all email addresses
        if isinstance(to, str):
            to = [to]

        # SECURITY: Limit number of recipients to prevent abuse (max 100 total)
        if len(to) > 100:
            to = to[:100]

        # Validate all recipient emails
        to = [Mail._validate_email(e) for e in to]
        cc_list = [Mail._validate_email(e) for e in ([cc] if isinstance(cc, str) else (cc or []))]
        bcc_list = [Mail._validate_email(e) for e in ([bcc] if isinstance(bcc, str) else (bcc or []))]

        # SECURITY: Limit total recipients across to/cc/bcc (max 100)
        all_recipients = (to + cc_list + bcc_list)[:100]

        # Validate sender email
        sender = Mail._validate_email(sender)

        # SECURITY: Limit body and HTML content size (max 1MB each)
        if len(body) > 1_000_000:
            logger.warning("Email body too large (%d bytes), truncating", len(body))
            body = body[:1_000_000] + "\n\n[Content truncated]"

        if html and len(html) > 1_000_000:
            logger.warning("Email HTML too large (%d bytes), truncating", len(html))
            html = html[:1_000_000] + "\n\n<!-- Content truncated -->"

        # Sanitize subject and other headers
        subject = Mail._sanitize(subject)

        msg = MIMEMultipart("alternative") if html else MIMEMultipart()
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        msg_string = msg.as_string()

        if sync:
            Mail._do_send(
                sender,
                all_recipients,
                msg_string,
                host,
                port,
                username,
                password,
                use_tls,
                raise_on_error=True,
            )
        else:
            backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
            if backend == "redis":
                from .background import background

                background(
                    _send_mail_task,
                    sender,
                    all_recipients,
                    msg_string,
                    host,
                    port,
                    username,
                    password,
                    use_tls,
                )
                return None
            else:
                t = threading.Thread(
                    target=Mail._do_send,
                    args=(
                        sender,
                        all_recipients,
                        msg_string,
                        host,
                        port,
                        username,
                        password,
                        use_tls,
                    ),
                    daemon=True,
                )
                t.start()
                return t


def _send_mail_task(
    sender: str,
    all_recipients: list[str],
    msg_string: str,
    host: str,
    port: int,
    username: Optional[str],
    password: Optional[str],
    use_tls: bool,
) -> None:
    Mail._do_send(
        sender=sender,
        all_recipients=all_recipients,
        msg_string=msg_string,
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
        raise_on_error=False,
    )
