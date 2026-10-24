"""
b2b_chatbot.py
────────────────────────────────────────────────────────────────────────────
A self-contained Flask Blueprint that adds a B2B order-taking chatbot to the
kairuAI platform.

Conversation flow
──────────────────
  Bot:  "What do you need today so that we can supply to you?"
  User: names a product (free text, e.g. "20 bags of 50kg cement")
        │
        ├─ Product found on the platform
        │     → Bot quotes the exact price stored in the Product table
        │     → asks for quantity (if not already given)
        │     → asks for business name + phone to confirm the order
        │     → logs the lead + emails the admin
        │
        └─ Product NOT found on the platform
              → Bot tells the customer it is checking with the team
              → asks for business name + phone (if not already known)
              → creates a ProductInquiry row + emails the admin so a human
                can send a manual reply

Nothing here ever invents a price — every quoted figure comes straight out
of the `Product` table, which mirrors https://www.kairu.co.ke/.

Integration
───────────
This module does NOT import your Flask `app` module directly (that would
create a circular import). Instead call `init_b2b_chatbot(...)` once from
app.py, *after* `db`, `Product`, `Category` and `send_email` are defined,
and before `app.run()`:

    from b2b_chatbot import init_b2b_chatbot
    init_b2b_chatbot(
        app=app,
        db=db,
        Product=Product,
        Category=Category,
        send_email=send_email,
        claude_client=claude_client,          # optional, improves free-text parsing
        admin_email=app.config['ADMIN_EMAIL'],
        admin_required=admin_required,        # optional, protects the admin inbox view
    )

That's it — `db.create_all()` (already called by your existing `init_db()`)
will pick up the three new tables automatically since they share the same
`db` object.
"""

import re
import json
import uuid
import difflib
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify
from sqlalchemy import or_


# ── Regexes used by the conversation state machine ──────────────────────────
GREETING_RE = re.compile(r'\b(hi|hello|hey|jambo|habari|good morning|good afternoon|good evening)\b', re.I)
THANKS_RE = re.compile(r'\b(thank|thanks|asante)\b', re.I)
RESTART_RE = re.compile(r'\b(start over|restart|new order|another product|something else)\b', re.I)
NUMBER_RE = re.compile(r'\b(\d{1,6})\b')
# Kenyan-style or generic phone numbers: 07xx xxx xxx, +2547xx..., 01xx..., or any 9-13 digit run
PHONE_RE = re.compile(r'(\+?254\d{9}|0[71]\d{8}|\+?\d{9,13})')
NOISE_WORDS_RE = re.compile(
    r'\b(i need|i want|do you have|looking for|please|can i get|need|want|kindly|'
    r'we need|we want|is there|got any|any|for my shop|for my business)\b', re.I
)


def _clean_query(text):
    q = NOISE_WORDS_RE.sub(' ', text.lower())
    q = re.sub(r'\s+', ' ', q).strip()
    return q or text.strip()


def extract_query_and_quantity(text):
    """Regex fallback: pulls a quantity out of the message and cleans the rest
    into a product search phrase."""
    qty = None
    m = NUMBER_RE.search(text)
    if m:
        qty = int(m.group(1))
    cleaned = _clean_query(NUMBER_RE.sub(' ', text))
    return cleaned, qty


def claude_extract(claude_client, text):
    """Ask Claude to pull the product phrase + quantity out of free text.
    Never used to invent a price — only to understand what the customer typed.
    Returns None on any failure so callers fall back to the regex extractor."""
    if not claude_client:
        return None
    try:
        resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=150,
            temperature=0,
            system=(
                "You extract shopping intent from a short B2B customer message. "
                "Respond with ONLY compact JSON and nothing else — no markdown fences, no prose: "
                '{"product": "<short product name/phrase, or empty string if none>", '
                '"quantity": <integer if a quantity was mentioned, otherwise null>}'
            ),
            messages=[{"role": "user", "content": text}],
        )
        raw = resp.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        if isinstance(data, dict) and "product" in data:
            return data
        return None
    except Exception as e:
        print(f"⚠️ B2B chatbot: Claude extraction failed, using fallback: {e}")
        return None


def find_matching_products(query, Product, limit=5):
    """Search the live Product table for the customer's request.
    Order of attempts: exact-ish substring → per-word substring → fuzzy match.
    Only ever returns products that are actually in stock on the platform."""
    q = _clean_query(query)
    if not q:
        return []

    base = Product.query.filter(Product.stock > 0)

    direct = base.filter(Product.name.ilike(f"%{q}%")).limit(limit).all()
    if direct:
        return direct

    words = [w for w in re.findall(r"[a-zA-Z]+", q) if len(w) > 2]
    if words:
        conds = [Product.name.ilike(f"%{w}%") for w in words]
        token_matches = base.filter(or_(*conds)).limit(limit).all()
        if token_matches:
            return token_matches

    all_products = base.all()
    names = {p.name: p for p in all_products}
    close = difflib.get_close_matches(q, names.keys(), n=limit, cutoff=0.5)
    return [names[c] for c in close]


def init_b2b_chatbot(app, db, Product, Category, send_email,
                      claude_client=None, admin_email=None, admin_required=None):
    """Wires up the models, matching helpers and routes, and registers the
    blueprint on the given Flask app. Returns the blueprint."""

    admin_email = admin_email or app.config.get("ADMIN_EMAIL")

    # ── Models ───────────────────────────────────────────────────────────
    class B2BChatSession(db.Model):
        __tablename__ = "b2b_chat_session"
        id = db.Column(db.Integer, primary_key=True)
        session_token = db.Column(db.String(64), unique=True, nullable=False)
        business_name = db.Column(db.String(150))
        phone_number = db.Column(db.String(20))
        state = db.Column(db.String(30), default="AWAIT_PRODUCT")
        pending_product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=True)
        pending_quantity = db.Column(db.Integer, nullable=True)
        created_at = db.Column(db.DateTime, default=datetime.utcnow)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
        chat_messages = db.relationship(
            "B2BChatMessage", backref="session", lazy=True, cascade="all, delete-orphan"
        )

    class B2BChatMessage(db.Model):
        __tablename__ = "b2b_chat_message"
        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(db.Integer, db.ForeignKey("b2b_chat_session.id"), nullable=False)
        role = db.Column(db.String(10))  # 'user' or 'bot'
        content = db.Column(db.Text)
        timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    class ProductInquiry(db.Model):
        """Products a customer asked for that are NOT on the platform —
        these need a manual admin reply."""
        __tablename__ = "product_inquiry"
        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(db.Integer, db.ForeignKey("b2b_chat_session.id"), nullable=True)
        query_text = db.Column(db.String(300))
        business_name = db.Column(db.String(150))
        phone_number = db.Column(db.String(20))
        status = db.Column(db.String(20), default="pending")  # pending / resolved
        created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Admin email helpers ─────────────────────────────────────────────
    def notify_admin_unmatched(convo, query_text):
        if not admin_email:
            return
        subject = f'🔔 B2B Chat — manual reply needed: "{query_text[:60]}"'
        html = f"""
        <div style="font-family:Arial,sans-serif;padding:20px;max-width:600px;margin:0 auto;
                    background:#fff;border-radius:10px;border-left:4px solid #e74c3c">
            <h2 style="color:#e74c3c">🔔 Product not found on platform</h2>
            <p><strong>Customer asked for:</strong> {query_text}</p>
            <p><strong>Business:</strong> {convo.business_name or 'Not provided yet'}</p>
            <p><strong>Phone:</strong> {convo.phone_number or 'Not provided yet'}</p>
            <p><strong>Chat session:</strong> {convo.session_token}</p>
            <p style="color:#888;font-size:13px">Please follow up with this customer manually.</p>
        </div>"""
        send_email(subject, admin_email, html)

    def notify_admin_order(convo, product, qty):
        if not admin_email:
            return
        line = (f"{qty} x {product.name} @ KSh {product.price:,.2f} = "
                f"KSh {product.price * qty:,.2f}") if product and qty else "Unspecified item"
        subject = f"🛒 B2B Chat order lead — {convo.business_name or 'Unknown business'}"
        html = f"""
        <div style="font-family:Arial,sans-serif;padding:20px;max-width:600px;margin:0 auto;
                    background:#fff;border-radius:10px;border-left:4px solid #667eea">
            <h2 style="color:#667eea">🛒 New B2B order lead</h2>
            <p><strong>Business:</strong> {convo.business_name}</p>
            <p><strong>Phone:</strong> {convo.phone_number}</p>
            <p><strong>Item:</strong> {line}</p>
            <p><strong>Chat session:</strong> {convo.session_token}</p>
        </div>"""
        send_email(subject, admin_email, html)

    # ── Conversation state machine ──────────────────────────────────────
    def log_message(convo, role, content):
        db.session.add(B2BChatMessage(session_id=convo.id, role=role, content=content))

    def handle_product_query(convo, text):
        if RESTART_RE.search(text):
            convo.pending_product_id = None
            convo.pending_quantity = None
            return "Sure! What do you need today so that we can supply to you?"

        if GREETING_RE.search(text) and len(text.split()) <= 4:
            return "Hello! 👋 What do you need today so that we can supply to you?"

        if THANKS_RE.search(text) and len(text.split()) <= 4:
            return "You're very welcome! Let us know if you need anything else. 🙌"

        extracted = claude_extract(claude_client, text)
        product_query, quantity = (None, None)
        if extracted and extracted.get("product"):
            product_query = extracted["product"]
            quantity = extracted.get("quantity")
        if not product_query:
            product_query, quantity = extract_query_and_quantity(text)

        matches = find_matching_products(product_query, Product)

        # ── Not on the platform → escalate to admin ─────────────────────
        if not matches:
            inquiry = ProductInquiry(
                session_id=convo.id, query_text=text.strip(),
                business_name=convo.business_name, phone_number=convo.phone_number,
            )
            db.session.add(inquiry)
            notify_admin_unmatched(convo, text.strip())

            if convo.phone_number:
                return (f"Thanks! We don't currently have \"{text.strip()}\" listed on our platform, "
                        "but I've forwarded this to our team and someone will reach out shortly with a quote. "
                        "Anything else you need today?")

            convo.state = "AWAIT_CONTACT_ESCALATION"
            return (f"We don't currently have \"{text.strip()}\" listed on our platform, but I've flagged it "
                    "for our team. Could you share your business name and phone number so they can get back "
                    "to you with a quote?")

        # ── Single confident match → quote the real platform price ─────
        if len(matches) == 1:
            p = matches[0]
            convo.pending_product_id = p.id
            convo.pending_quantity = quantity

            if quantity:
                convo.state = "AWAIT_CONTACT_ORDER"
                subtotal = p.price * quantity
                return (f"We have **{p.name}** at **KSh {p.price:,.2f}** ({p.stock} in stock). "
                        f"{quantity} units = **KSh {subtotal:,.2f}**. "
                        "Could you share your business name and phone number to confirm this order?")

            convo.state = "AWAIT_QUANTITY"
            return f"We have **{p.name}** at **KSh {p.price:,.2f}** ({p.stock} in stock). How many would you like?"

        # ── Several possible matches → let the customer pick ────────────
        convo.pending_product_id = None
        convo.pending_quantity = None
        lines = "\n".join(f"• {p.name} — KSh {p.price:,.2f}" for p in matches)
        return f"We have a few options matching that:\n\n{lines}\n\nWhich one would you like, and how many?"

    def handle_quantity(convo, text):
        if not convo.pending_product_id:
            convo.state = "AWAIT_PRODUCT"
            return handle_product_query(convo, text)

        m = NUMBER_RE.search(text)
        if not m:
            return "Sorry, could you tell me the quantity as a number — e.g. '20'?"

        qty = int(m.group(1))
        p = Product.query.get(convo.pending_product_id)
        if not p:
            convo.state = "AWAIT_PRODUCT"
            return "Sorry, that item is no longer available — what else do you need?"

        convo.pending_quantity = qty
        subtotal = p.price * qty
        convo.state = "AWAIT_CONTACT_ORDER"
        return (f"Got it — {qty} x {p.name} = **KSh {subtotal:,.2f}**. "
                "Could you share your business name and phone number so we can confirm this order?")

    def handle_contact(convo, text, purpose):
        compact = re.sub(r"[\s-]", "", text)
        phone_match = PHONE_RE.search(compact)
        phone = phone_match.group() if phone_match else None
        # Strip any run of digits/spaces/dashes/plus (6+ chars) — that's the phone number,
        # however it was spaced in the original message — leaving the business name intact.
        name_part = re.sub(r"[\d\s\-\+]{6,}", " ", text).strip(" ,.-")

        if name_part:
            convo.business_name = name_part
        if phone:
            convo.phone_number = phone

        if not convo.phone_number:
            return "Thanks! Could you also share a phone number so our team can reach you?"

        business_name = convo.business_name or "there"

        if purpose == "order":
            p = Product.query.get(convo.pending_product_id) if convo.pending_product_id else None
            qty = convo.pending_quantity
            order_summary = (f"{qty} x {p.name} (KSh {p.price * qty:,.2f})"
                              if p and qty else "your requested item(s)")
            notify_admin_order(convo, p, qty)
            convo.state = "AWAIT_PRODUCT"
            convo.pending_product_id = None
            convo.pending_quantity = None
            return (f"Thank you, {business_name}! ✅ We've received your order for {order_summary}. "
                    "Our team will confirm delivery and payment shortly. Need anything else?")

        # purpose == "escalation"
        inquiry = (ProductInquiry.query.filter_by(session_id=convo.id)
                   .order_by(ProductInquiry.id.desc()).first())
        if inquiry:
            inquiry.business_name = convo.business_name
            inquiry.phone_number = convo.phone_number
        convo.state = "AWAIT_PRODUCT"
        return (f"Thanks, {business_name}! Our team now has your contact details and will follow up with "
                "a quote soon. Anything else you need today?")

    def handle_message(convo, text):
        state = convo.state
        if state == "AWAIT_QUANTITY":
            return handle_quantity(convo, text)
        if state == "AWAIT_CONTACT_ORDER":
            return handle_contact(convo, text, purpose="order")
        if state == "AWAIT_CONTACT_ESCALATION":
            return handle_contact(convo, text, purpose="escalation")
        convo.state = "AWAIT_PRODUCT"
        return handle_product_query(convo, text)

    # ── Blueprint & routes ───────────────────────────────────────────────
    bp = Blueprint("b2b_chatbot", __name__)

    @bp.route("/b2b-chat")
    def b2b_chat_page():
        return render_template("b2b_chat.html")

    @bp.route("/api/b2b-chat", methods=["POST"])
    def b2b_chat_api():
        data = request.get_json(silent=True) or {}
        token = (data.get("session_token") or "").strip()
        user_text = (data.get("message") or "").strip()

        if not token:
            token = str(uuid.uuid4())

        convo = B2BChatSession.query.filter_by(session_token=token).first()
        is_new = False
        if not convo:
            convo = B2BChatSession(session_token=token, state="AWAIT_PRODUCT")
            db.session.add(convo)
            db.session.commit()
            is_new = True

        # First load of a brand new session with no message yet → send the opener
        if is_new and not user_text:
            greeting = "👋 Welcome to KairuAI! What do you need today so that we can supply to you?"
            log_message(convo, "bot", greeting)
            db.session.commit()
            return jsonify({"reply": greeting, "state": convo.state, "session_token": token})

        if not user_text:
            return jsonify({"error": "message is required"}), 400

        try:
            log_message(convo, "user", user_text)
            reply = handle_message(convo, user_text)
            log_message(convo, "bot", reply)
            convo.updated_at = datetime.utcnow()
            db.session.commit()
            return jsonify({"reply": reply, "state": convo.state, "session_token": token})
        except Exception as e:
            db.session.rollback()
            print(f"❌ B2B chatbot error: {e}")
            return jsonify({
                "reply": "Sorry, something went wrong on our end — please try again in a moment.",
                "state": convo.state, "session_token": token,
            }), 200

    # ── Optional admin inbox for escalated / unmatched inquiries ───────
    def _admin_inquiries_view():
        pending = (ProductInquiry.query.filter_by(status="pending")
                   .order_by(ProductInquiry.created_at.desc()).all())
        rows = "".join(f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee">{i.created_at.strftime('%b %d, %H:%M')}</td>
                <td style="padding:10px;border-bottom:1px solid #eee">{i.query_text}</td>
                <td style="padding:10px;border-bottom:1px solid #eee">{i.business_name or '—'}</td>
                <td style="padding:10px;border-bottom:1px solid #eee">{i.phone_number or '—'}</td>
                <td style="padding:10px;border-bottom:1px solid #eee">
                    <a href="/admin/b2b-inquiries/{i.id}/resolve"
                       style="background:#667eea;color:#fff;padding:6px 12px;border-radius:6px;
                              text-decoration:none;font-size:13px">Mark resolved</a>
                </td>
            </tr>""" for i in pending)
        return f"""
        <html><head><title>B2B Inquiries</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>body{{font-family:Arial,sans-serif;padding:16px;background:#f4f4f4}}
        h2{{color:#667eea}} table{{width:100%;border-collapse:collapse;background:#fff;
        border-radius:8px;overflow:hidden;box-shadow:0 2px 6px rgba(0,0,0,0.08)}}
        th{{background:#667eea;color:#fff;padding:10px;text-align:left}}</style></head>
        <body>
        <h2>🔔 Products customers asked for that need a manual reply</h2>
        <p>{len(pending)} pending inquiries</p>
        <table><thead><tr><th>When</th><th>Requested</th><th>Business</th><th>Phone</th><th></th></tr></thead>
        <tbody>{rows or '<tr><td colspan="5" style="padding:20px;text-align:center">Nothing pending 🎉</td></tr>'}</tbody>
        </table></body></html>"""

    def _resolve_inquiry(inquiry_id):
        inquiry = ProductInquiry.query.get_or_404(inquiry_id)
        inquiry.status = "resolved"
        db.session.commit()
        return _admin_inquiries_view()

    if admin_required:
        bp.route("/admin/b2b-inquiries")(admin_required(_admin_inquiries_view))
        bp.route("/admin/b2b-inquiries/<int:inquiry_id>/resolve")(admin_required(_resolve_inquiry))
    else:
        bp.route("/admin/b2b-inquiries")(_admin_inquiries_view)
        bp.route("/admin/b2b-inquiries/<int:inquiry_id>/resolve")(_resolve_inquiry)

    app.register_blueprint(bp)

    # Expose the models on the blueprint in case app.py wants to reference them
    bp.B2BChatSession = B2BChatSession
    bp.B2BChatMessage = B2BChatMessage
    bp.ProductInquiry = ProductInquiry

    return bp
