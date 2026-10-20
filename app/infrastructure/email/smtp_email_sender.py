"""Minimal SMTP adapter for transactional application emails."""

from email.message import EmailMessage
import smtplib
import ssl


class SmtpEmailSender:
    """Send plain-text email using an explicitly configured SMTP server."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        use_starttls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        if not host.strip() or not sender.strip():
            raise ValueError("SMTP host and sender must not be blank")
        if not 1 <= port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        if timeout <= 0:
            raise ValueError("SMTP timeout must be positive")
        if username and not use_starttls:
            raise ValueError("SMTP authentication requires STARTTLS")
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_starttls = use_starttls
        self._timeout = timeout

    def send_text(self, *, recipient: str, subject: str, body: str) -> None:
        """Send a text message; credentials and message contents are never logged."""
        if not recipient.strip():
            raise ValueError("Recipient must not be blank")
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            client.ehlo()
            if self._use_starttls:
                client.starttls(context=context)
                client.ehlo()
            if self._username:
                client.login(self._username, self._password)
            client.send_message(message)
