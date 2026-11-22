"""Sending email through the classic Outlook desktop app, signed in with your UConn school account.

No password or email account is stored in this project: Outlook sends as whoever is signed in to it.
This only works when the app runs on a Windows PC with classic Outlook set up (not on the VM).
The chat only calls send() after the user approved the drafts (see Judge in judge.py).
"""


class MailError(RuntimeError):
    pass


def send(to, subject, body):
    """Send one plain-text email with classic Outlook. Returns the address it was sent from."""
    if not to or "@" not in to:
        raise MailError("no email address to send to")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise MailError("sending needs Windows with classic Outlook (the pywin32 package is not installed here)")
    pythoncom.CoInitialize()          # the web server answers on worker threads
    try:
        try:
            outlook = win32com.client.Dispatch("Outlook.Application")
            session = outlook.GetNamespace("MAPI")
            accounts = [session.Accounts.Item(i + 1).SmtpAddress for i in range(session.Accounts.Count)]
        except Exception as e:
            raise MailError(f"classic Outlook isn't ready ({e}). Open classic Outlook once and sign in with "
                            f"your UConn account.")
        if not accounts:
            raise MailError("classic Outlook has no email account. Open classic Outlook and sign in with your "
                            "UConn account first.")
        mail = outlook.CreateItem(0)   # 0 = an email
        mail.To = to
        mail.Subject = subject
        mail.Body = body
        mail.Send()
        return accounts[0]
    finally:
        pythoncom.CoUninitialize()
