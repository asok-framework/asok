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

    @staticmethod
    def _sanitize(val: Union[str, list[str]]) -> Any:
        """Strip control characters (CR/LF) from header values to prevent injection."""
        if isinstance(val, list):
            return [v.replace("\n", "").replace("\r", "") for v in val]
        if isinstance(val, str):
            return val.replace("\n", "").replace("\r", "")
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
        """
        host = Mail._cfg("MAIL_HOST", "localhost")
        port = int(Mail._cfg("MAIL_PORT", "587"))
        username = Mail._cfg("MAIL_USERNAME")
        password = Mail._cfg("MAIL_PASSWORD")
        sender = from_addr or Mail._cfg("MAIL_FROM", username or "noreply@localhost")
        use_tls = Mail._cfg("MAIL_TLS", "true").lower() != "false"

        # Normalise and sanitize recipients
        if isinstance(to, str):
            to = [to]
        to = Mail._sanitize(to)
        cc_list = Mail._sanitize([cc] if isinstance(cc, str) else (cc or []))
        bcc_list = Mail._sanitize([bcc] if isinstance(bcc, str) else (bcc or []))
        all_recipients = to + cc_list + bcc_list

        sender = Mail._sanitize(sender)
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
            )
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
