# Newtonite Task Tracker: Known Limitations & Production Roadmap

This document transparently records architectural trade-offs, environmental constraints, and future expansion paths for the Newtonite Task Tracker.

---

## 1. Current Environmental & Architectural Limitations

### 1.1 SQLite Write Concurrency Ceiling
- **Observation**: Newtonite currently uses SQLite 3 with Write-Ahead Logging (WAL) and optimistic concurrency control.
- **Limitation**: While SQLite supports unlimited concurrent readers, only one database connection can write at any given millisecond. For thousands of concurrent writes per minute, write lock contention (`database is locked`) may occur.
- **Production Recommendation**: Transition to **PostgreSQL** in production by updating the `DATABASES` dictionary in `config/settings.py` to use `django.db.backends.postgresql`. The schema, indexes, constraints, and atomic transactions in this codebase are 100% compatible with PostgreSQL.

### 1.2 Development Email Delivery Backend
- **Observation**: Password reset emails are dispatched via Django's console email backend (`django.core.mail.backends.console.EmailBackend`).
- **Limitation**: Reset links are printed to the terminal rather than delivered to real email inboxes.
- **Production Recommendation**: Configure an SMTP service or transactional API (e.g., SendGrid, Amazon SES, Postmark) with `EMAIL_HOST`, `EMAIL_HOST_USER`, and `EMAIL_HOST_PASSWORD`.

### 1.3 Polling & Server-Side Rendering vs Live WebSockets
- **Observation**: State transitions and concurrency conflicts are evaluated when actions are submitted.
- **Limitation**: If User A changes a task status, User B's open browser screen will not automatically reflect the change until User B interacts with the page or refreshes.
- **Production Recommendation**: Integrate **Django Channels** and **Redis** for real-time WebSocket push notifications to update task cards dynamically.

### 1.4 Single-Level Work Item Hierarchy
- **Observation**: Tasks are direct children of Teams with priority and status attributes.
- **Limitation**: Epic-to-task or sub-task tree dependencies are not currently modeled in this release.
- **Production Recommendation**: Introduce a self-referencing foreign key `parent_task = ForeignKey('self', null=True)` on the `Task` model.

### 1.5 File & Screenshot Attachments
- **Observation**: Tasks store detailed descriptions and acceptance criteria as text.
- **Limitation**: Direct binary file uploads (e.g. PDFs, crash logs, screenshots) are not stored in database blobs.
- **Production Recommendation**: Add a `TaskAttachment` model linked to AWS S3 or Google Cloud Storage via `django-storages`.

---

## 2. Security Considerations

- **Secret Key**: `SECRET_KEY` in `config/settings.py` is configured for local evaluation. In production, load from environment variables (`os.environ.get('DJANGO_SECRET_KEY')`).
- **Debug Mode**: `DEBUG = True` is enabled for local challenge evaluation. Set `DEBUG = False` and specify strict domain hostnames in `ALLOWED_HOSTS` before public hosting.
- **HTTPS & Secure Cookies**: In production, enable `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`, and `SECURE_SSL_REDIRECT = True`.
