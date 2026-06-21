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
    def _connect_and_send(
        server: smtplib.SMTP,
        sender: str,
        recipients: list[str],
        msg: str,
        username: Optional[str],
        password: Optional[str],
        use_tls: bool,
    ) -> None:
        if use_tls:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.sendmail(sender, recipients, msg)

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
                Mail._connect_and_send(
                    server, sender, all_recipients, msg_string, username, password, use_tls
                )
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
    def _to_validated_list(val: Optional[Union[str, list[str]]]) -> list[str]:
        if not val:
            return []
        items = [val] if isinstance(val, str) else val
        return [Mail._validate_email(e) for e in items]

    @staticmethod
    def _prepare_recipients(
        to: Union[str, list[str]],
        cc: Optional[Union[str, list[str]]],
        bcc: Optional[Union[str, list[str]]],
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        to_list = Mail._to_validated_list(to)[:100]
        cc_list = Mail._to_validated_list(cc)
        bcc_list = Mail._to_validated_list(bcc)
        all_recipients = (to_list + cc_list + bcc_list)[:100]
        return to_list, cc_list, bcc_list, all_recipients

    @staticmethod
    def _prepare_content(body: str, html: Optional[str]) -> tuple[str, Optional[str]]:
        if len(body) > 1_000_000:
            logger.warning("Email body too large (%d bytes), truncating", len(body))
            body = body[:1_000_000] + "\n\n[Content truncated]"

        if html and len(html) > 1_000_000:
            logger.warning("Email HTML too large (%d bytes), truncating", len(html))
            html = html[:1_000_000] + "\n\n<!-- Content truncated -->"

        return body, html

    @staticmethod
    def _create_message(
        sender: str,
        to: list[str],
        cc_list: list[str],
        subject: str,
        body: str,
        html: Optional[str],
    ) -> str:
        msg = MIMEMultipart("alternative") if html else MIMEMultipart()
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        msg.attach(MIMEText(body, "plain", "utf-8"))
        if html:
            msg.attach(MIMEText(html, "html", "utf-8"))

        return msg.as_string()

    @staticmethod
    def _dispatch_send(
        sender: str,
        all_recipients: list[str],
        msg_string: str,
        host: str,
        port: int,
        username: Optional[str],
        password: Optional[str],
        use_tls: bool,
        sync: bool,
    ) -> threading.Thread | None:
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
            return None

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

        to_list, cc_list, bcc_list, all_recipients = Mail._prepare_recipients(to, cc, bcc)
        sender = Mail._validate_email(sender)
        body, html = Mail._prepare_content(body, html)
        subject = Mail._sanitize(subject)

        msg_string = Mail._create_message(sender, to_list, cc_list, subject, body, html)

        return Mail._dispatch_send(
            sender,
            all_recipients,
            msg_string,
            host,
            port,
            username,
            password,
            use_tls,
            sync,
        )


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
