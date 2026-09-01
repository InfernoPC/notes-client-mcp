"""Write tools: create/update documents, send mail.

These do their entire COM interaction inside one NotesBackend.run() closure,
same as the read-only tools (see notes_backend.open_database). Unlike the
read tools, every one of these is exposed to the MCP client behind an
elicitation confirmation (see server.py) - this module itself does not
confirm anything; it assumes the caller already got the user's go-ahead.
"""

from __future__ import annotations

from ..notes_backend import NotesBackend, open_database


def create_document(
    backend: NotesBackend,
    server: str,
    file_path: str,
    form: str,
    fields: dict[str, str],
) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.CreateDocument()
        doc.ReplaceItemValue("Form", form)
        for name, value in fields.items():
            doc.ReplaceItemValue(name, value)
        doc.Save(True, False)
        return {"unid": doc.UniversalID, "note_id": doc.NoteID}

    return backend.run(_op)


def update_document(
    backend: NotesBackend,
    server: str,
    file_path: str,
    unid: str,
    fields: dict[str, str],
) -> dict:
    def _op(session):
        db = open_database(session, server, file_path)
        doc = db.GetDocumentByUNID(unid)
        if doc is None:
            raise ValueError(f"No document with UNID {unid!r}")
        for name, value in fields.items():
            doc.ReplaceItemValue(name, value)
        doc.Save(True, False)
        return {"unid": doc.UniversalID, "last_modified": str(doc.LastModified)}

    return backend.run(_op)


def send_mail(backend: NotesBackend, sendto: str, subject: str, body: str) -> dict:
    def _op(session):
        mail_server = session.GetEnvironmentString("MailServer", True)
        mail_file = session.GetEnvironmentString("MailFile", True)
        db = open_database(session, mail_server, mail_file)
        doc = db.CreateDocument()
        doc.ReplaceItemValue("Form", "Memo")
        doc.ReplaceItemValue("SendTo", sendto)
        doc.ReplaceItemValue("Subject", subject)
        doc.ReplaceItemValue("Body", body)
        doc.Send(False)
        return {"unid": doc.UniversalID, "sendto": sendto, "subject": subject}

    return backend.run(_op)
