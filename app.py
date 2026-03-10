from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message as EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import os
from threading import Thread
from dotenv import load_dotenv
import anthropic
import re
from mpesa_service import MpesaService
from functools import wraps
import cloudinary
import cloudinary.uploader

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-key-change-this-in-production'

database_url = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

# ── Cloudinary config ────────────────────────────────────────────────────────
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

# ── File upload config (kept for allowed_file check) ────────────────────────
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload

# ── Mail config ─────────────────────────────────────────────────────────────
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USERNAME'] = 'ikendi377@gmail.com'
app.config['MAIL_PASSWORD'] = 'jjzj hwzn yqpo umuw'
app.config['MAIL_DEFAULT_SENDER'] = 'ikendi377@gmail.com'
app.config['ADMIN_EMAIL'] = 'ikendi377@gmail.com'

# ── Admin credentials ────────────────────────────────────────────────────────
ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'adminstrator')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'Administratorx')

ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
if not ANTHROPIC_API_KEY:
    print("⚠️  WARNING: ANTHROPIC_API_KEY not found in environment variables!")

db = SQLAlchemy(app)
with app.app_context():
    db.create_all()
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

try:
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("✅ Claude AI initialized successfully!")
except Exception as e:
    print(f"❌ Error initializing Claude: {str(e)}")
    claude_client = None

mpesa = MpesaService()


# ── Helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def upload_image_to_cloudinary(file):
    """Upload a file to Cloudinary and return the secure URL, or None on failure."""
    try:
        result = cloudinary.uploader.upload(
            file,
            folder="tradioai_products",
            transformation=[{"width": 800, "height": 800, "crop": "limit"}]
        )
        return result['secure_url']
    except Exception as e:
        print(f"❌ Cloudinary upload error: {str(e)}")
        return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            flash('Admin access required.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ── Async email ──────────────────────────────────────────────────────────────

def send_async_email(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
            print("✅ EMAIL SENT SUCCESSFULLY!")
        except Exception as e:
            print(f"❌ ERROR SENDING EMAIL: {str(e)}")
            import traceback
            traceback.print_exc()


def send_email(subject, recipient, html_body):
    try:
        msg = EmailMessage(
            subject=subject,
            recipients=[recipient] if isinstance(recipient, str) else recipient,
            html=html_body,
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        Thread(target=send_async_email, args=(app, msg)).start()
    except Exception as e:
        print(f"❌ Error creating email: {str(e)}")


# ── Database Models ──────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    orders = db.relationship('Order', backref='customer', lazy=True)
    messages_sent = db.relationship('Message', foreign_keys='Message.sender_id', backref='sender', lazy=True)
    messages_received = db.relationship('Message', foreign_keys='Message.receiver_id', backref='receiver', lazy=True)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    products = db.relationship('Product', backref='category', lazy=True)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)
    image_url = db.Column(db.String(500))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def image_path(self):
        """Return the correct image URL for templates."""
        PLACEHOLDER = 'https://placehold.co/400x300?text=No+Image'
        if not self.image_url:
            return PLACEHOLDER
        # Cloudinary or any external URL — return as-is
        if self.image_url.startswith('http://') or self.image_url.startswith('https://'):
            return self.image_url
        # Legacy uploads/ path — return placeholder since files don't persist on Render
        return PLACEHOLDER


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(50), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    items = db.relationship('OrderItem', backref='order', lazy=True)


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    product = db.relationship('Product', backref='order_items')


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False)


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    checkout_request_id = db.Column(db.String(100), unique=True)
    mpesa_receipt_number = db.Column(db.String(50))
    phone_number = db.Column(db.String(15), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')
    result_desc = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    order = db.relationship('Order', backref='transactions')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ── Email notifications ──────────────────────────────────────────────────────

def send_signup_notification(user):
    subject = f"🎉 New Customer Signup - {user.username}"
    html_body = f"""
    <body style="font-family:Arial,sans-serif;padding:20px;background:#f4f4f4">
    <div style="max-width:600px;margin:0 auto;background:#fff;padding:30px;border-radius:10px">
        <h2 style="color:#667eea">🎉 New Customer Registration</h2>
        <p>A new customer has signed up on tradioAI!</p>
        <div style="background:#f8f9fa;padding:20px;border-left:4px solid #667eea;border-radius:5px">
            <p><strong>Username:</strong> {user.username}</p>
            <p><strong>Email:</strong> {user.email}</p>
            <p><strong>Phone:</strong> {user.phone or 'Not provided'}</p>
            <p><strong>Registered:</strong> {user.created_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div></body>"""
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_login_notification(user):
    subject = f"🔐 Customer Login - {user.username}"
    html_body = f"""
    <body style="font-family:Arial,sans-serif;padding:20px;background:#f4f4f4">
    <div style="max-width:600px;margin:0 auto;background:#fff;padding:30px;border-radius:10px">
        <h2 style="color:#667eea">🔐 Customer Login</h2>
        <div style="background:#f8f9fa;padding:20px;border-left:4px solid #667eea;border-radius:5px">
            <p><strong>Username:</strong> {user.username}</p>
            <p><strong>Email:</strong> {user.email}</p>
            <p><strong>Login Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </div></body>"""
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_order_notification(order):
    items_html = "".join(f"""
        <tr>
            <td style="padding:10px;border-bottom:1px solid #e1e5e9">{i.product.name}</td>
            <td style="padding:10px;border-bottom:1px solid #e1e5e9;text-align:center">{i.quantity}</td>
            <td style="padding:10px;border-bottom:1px solid #e1e5e9;text-align:right">KSh {i.price:,.2f}</td>
            <td style="padding:10px;border-bottom:1px solid #e1e5e9;text-align:right;font-weight:600">KSh {i.price*i.quantity:,.2f}</td>
        </tr>""" for i in order.items)

    subject = f"🛒 New Order #{order.id} - {order.customer.username}"
    html_body = f"""
    <body style="font-family:Arial,sans-serif;padding:20px;background:#f4f4f4">
    <div style="max-width:650px;margin:0 auto;background:#fff;padding:30px;border-radius:10px">
        <h2 style="color:#667eea">🛒 New Order Received!</h2>
        <p><strong>Customer:</strong> {order.customer.username} ({order.customer.email})</p>
        <p><strong>Order ID:</strong> #{order.id} | <strong>Date:</strong> {order.created_at.strftime('%Y-%m-%d %H:%M')}</p>
        <table style="width:100%;border-collapse:collapse;margin:20px 0">
            <thead><tr style="background:#667eea;color:#fff">
                <th style="padding:10px;text-align:left">Product</th>
                <th style="padding:10px">Qty</th>
                <th style="padding:10px;text-align:right">Price</th>
                <th style="padding:10px;text-align:right">Subtotal</th>
            </tr></thead>
            <tbody>{items_html}</tbody>
        </table>
        <div style="background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px;border-radius:8px;text-align:right">
            <h3 style="margin:0;font-size:24px">Total: KSh {order.total_amount:,.2f}</h3>
        </div>
    </div></body>"""
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_chat_notification(message):
    subject = f"💬 New Chat Message - {message.sender.username}"
    html_body = f"""
    <body style="font-family:Arial,sans-serif;padding:20px;background:#f4f4f4">
    <div style="max-width:600px;margin:0 auto;background:#fff;padding:30px;border-radius:10px">
        <h2 style="color:#667eea">💬 New Customer Message</h2>
        <div style="background:#f8f9fa;padding:20px;border-left:4px solid #667eea;border-radius:5px">
            <p><strong>From:</strong> {message.sender.username} ({message.sender.email})</p>
            <p><strong>Time:</strong> {message.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
        <div style="border:2px solid #667eea;border-radius:8px;padding:20px;margin-top:20px">
            <p style="margin:0">{message.content}</p>
        </div>
    </div></body>"""
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


# ── Rule-based chatbot ───────────────────────────────────────────────────────

def simple_chatbot_response(message, user):
    msg = message.lower()

    if re.search(r'\b(hi|hello|hey|good morning|good afternoon|good evening|jambo|habari)\b', msg):
        return f"Hello {user.username}! 👋 Welcome to tradioAI. How can I assist you today?"

    if re.search(r'\b(thank|thanks|asante)\b', msg):
        return "You're very welcome! 😊 Anything else I can help with?"

    if re.search(r'\b(help|what can you|how do you work)\b', msg):
        return "🤖 I can help you find products, check prices, track orders, and more. Just ask!"

    # Resolve Duka category ID dynamically (safe for any DB state)
    duka_cat = Category.query.filter_by(name='Duka').first()
    duka_id = duka_cat.id if duka_cat else 6

    category_map = {
        r'\b(cement|mabati|iron sheet|steel|construction|building)\b': (1, "🏗️ Construction Materials"),
        r'\b(food|beans|flour|vegetable|fruit|maize|potato|produce)\b': (2, "🌾 Fresh Produce"),
        r'\b(shoes|dress|fashion|clothes|lotion|nivea|jeans|sandal)\b': (3, "👗 Fashion & Cosmetics"),
        r'\b(phone|smartphone|itel|tecno|redmi|charger|electronics|bulb)\b': (4, "📱 Electronics"),
        r'\b(car|auto|spare part|engine|oil|brake|tire|motorbike|vehicle)\b': (5, "🚗 Auto Parts"),
        r'\b(duka|bar soap|omo|ariel|blue band|cooking oil|unga|sugar|rice|royco|cigarette|sigara|matchbox|kiberiti|kerosene|paraffin|candle|mshumaa|detergent|washing powder|kimbo|salit|menengai|lifebuoy|sunlight)\b': (duka_id, "🛒 Duka"),
    }

    for pattern, (cat_id, label) in category_map.items():
        if re.search(pattern, msg):
            products = Product.query.filter_by(category_id=cat_id).filter(Product.stock > 0).limit(8).all()
            if products:
                lines = "\n".join(f"• **{p.name}** — KSh {p.price:,.2f} ({p.stock} in stock)" for p in products)
                return f"{label} Available:\n\n{lines}"
            return f"{label}: Currently restocking. Check back soon!"

    if re.search(r'\b(order|track|status|delivery)\b', msg):
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(5).all()
        if orders:
            lines = "\n".join(
                f"• Order #{o.id} — {o.status.upper()} — KSh {o.total_amount:,.2f} — {o.created_at.strftime('%b %d, %Y')}"
                for o in orders)
            return f"📦 Your Recent Orders:\n\n{lines}"
        return "📦 You haven't placed any orders yet."

    return None


# ── Claude AI chatbot ────────────────────────────────────────────────────────

def claude_chatbot_response(message, user):
    if not claude_client:
        return "Sorry, AI service is temporarily unavailable."
    try:
        orders = Order.query.filter_by(user_id=user.id).all()
        order_ctx = f"Customer has {len(orders)} orders."
        if orders:
            latest = orders[-1]
            order_ctx += f" Latest: #{latest.id}, {latest.status}, KSh {latest.total_amount:,.2f}."

        products = Product.query.filter(Product.stock > 0).limit(20).all()
        prod_summary = {}
        for cat in Category.query.all():
            cp = [p for p in products if p.category_id == cat.id]
            if cp:
                prod_summary[cat.name] = [f"{p.name} - KSh {p.price:,.2f}" for p in cp[:4]]

        system = f"""You are a friendly shopping assistant for tradioAI (Kenya).
Customer: {user.username} | {order_ctx}
Available products:\n"""
        for cat, prods in prod_summary.items():
            system += f"\n{cat}:\n" + "\n".join(f"  • {p}" for p in prods)
        system += "\n\nBe concise, warm, use KSh currency. Only recommend available products."

        resp = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            temperature=0.7,
            system=system,
            messages=[{"role": "user", "content": message}]
        )
        return resp.content[0].text
    except Exception as e:
        print(f"Claude error: {e}")
        return "I'm having trouble right now. Please try again shortly."


# ════════════════════════════════════════════════════════════════════════════
# ADMIN ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))
    if request.method == 'POST':
        uname = request.form.get('username', '').strip()
        pwd = request.form.get('password', '')
        if uname == ADMIN_USERNAME and pwd == ADMIN_PASSWORD:
            session['is_admin'] = True
            session.permanent = True
            flash('Welcome, Admin!', 'success')
            return redirect(url_for('admin_dashboard'))
        flash('Invalid admin credentials.', 'error')
    return render_template('admin/login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    flash('Admin logged out.', 'info')
    return redirect(url_for('admin_login'))


@app.route('/admin')
@admin_required
def admin_dashboard():
    stats = {
        'products': Product.query.count(),
        'categories': Category.query.count(),
        'orders': Order.query.count(),
        'users': User.query.count(),
        'revenue': db.session.query(db.func.sum(Order.total_amount)).filter_by(status='paid').scalar() or 0,
        'pending_orders': Order.query.filter_by(status='pending').count(),
    }
    recent_orders = Order.query.order_by(Order.created_at.desc()).limit(5).all()
    return render_template('admin/dashboard.html', stats=stats, recent_orders=recent_orders)


# ── Product Management ───────────────────────────────────────────────────────

@app.route('/admin/products')
@admin_required
def admin_products():
    category_id = request.args.get('category_id', type=int)
    search = request.args.get('search', '').strip()
    query = Product.query
    if category_id:
        query = query.filter_by(category_id=category_id)
    if search:
        query = query.filter(Product.name.ilike(f'%{search}%'))
    products = query.order_by(Product.name).all()
    categories = Category.query.all()
    return render_template('admin/products.html', products=products,
                           categories=categories, selected_category=category_id, search=search)


@app.route('/admin/products/new', methods=['GET', 'POST'])
@admin_required
def admin_add_product():
    categories = Category.query.all()
    if request.method == 'POST':
        try:
            name = request.form['name'].strip()
            description = request.form.get('description', '').strip()
            price = float(request.form['price'])
            stock = int(request.form.get('stock', 0))
            category_id = int(request.form['category_id'])

            image_url = None
            file = request.files.get('image')
            if file and file.filename and allowed_file(file.filename):
                image_url = upload_image_to_cloudinary(file)
                if not image_url:
                    flash('Image upload failed. Product saved without image.', 'warning')
            elif request.form.get('image_url'):
                image_url = request.form['image_url'].strip()

            product = Product(name=name, description=description, price=price,
                              stock=stock, category_id=category_id, image_url=image_url)
            db.session.add(product)
            db.session.commit()
            flash(f'Product "{name}" added successfully!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding product: {str(e)}', 'error')

    return render_template('admin/product_form.html', product=None, categories=categories, action='Add')


@app.route('/admin/products/<int:product_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.all()

    if request.method == 'POST':
        try:
            product.name = request.form['name'].strip()
            product.description = request.form.get('description', '').strip()
            product.price = float(request.form['price'])
            product.stock = int(request.form.get('stock', 0))
            product.category_id = int(request.form['category_id'])

            file = request.files.get('image')
            if file and file.filename and allowed_file(file.filename):
                new_url = upload_image_to_cloudinary(file)
                if new_url:
                    product.image_url = new_url
                else:
                    flash('Image upload failed. Keeping existing image.', 'warning')
            elif request.form.get('image_url'):
                product.image_url = request.form['image_url'].strip()

            db.session.commit()
            flash(f'Product "{product.name}" updated successfully!', 'success')
            return redirect(url_for('admin_products'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating product: {str(e)}', 'error')

    return render_template('admin/product_form.html', product=product, categories=categories, action='Edit')


@app.route('/admin/products/<int:product_id>/delete', methods=['POST'])
@admin_required
def admin_delete_product(product_id):
    product = Product.query.get_or_404(product_id)
    try:
        db.session.delete(product)
        db.session.commit()
        flash(f'Product "{product.name}" deleted.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting product: {str(e)}', 'error')
    return redirect(url_for('admin_products'))


# ── Quick price update (AJAX) ────────────────────────────────────────────────

@app.route('/admin/products/<int:product_id>/update-price', methods=['POST'])
@admin_required
def admin_update_price(product_id):
    product = Product.query.get_or_404(product_id)
    try:
        data = request.get_json()
        product.price = float(data['price'])
        db.session.commit()
        return jsonify({'success': True, 'price': product.price})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


# ── Quick stock update (AJAX) ────────────────────────────────────────────────

@app.route('/admin/products/<int:product_id>/update-stock', methods=['POST'])
@admin_required
def admin_update_stock(product_id):
    product = Product.query.get_or_404(product_id)
    try:
        data = request.get_json()
        product.stock = int(data['stock'])
        db.session.commit()
        return jsonify({'success': True, 'stock': product.stock})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 400


# ── Category Management ──────────────────────────────────────────────────────

@app.route('/admin/categories')
@admin_required
def admin_categories():
    categories = Category.query.all()
    return render_template('admin/categories.html', categories=categories)


@app.route('/admin/categories/new', methods=['GET', 'POST'])
@admin_required
def admin_add_category():
    if request.method == 'POST':
        name = request.form['name'].strip()
        description = request.form.get('description', '').strip()
        if name:
            cat = Category(name=name, description=description)
            db.session.add(cat)
            db.session.commit()
            flash(f'Category "{name}" added!', 'success')
            return redirect(url_for('admin_categories'))
        flash('Category name is required.', 'error')
    return render_template('admin/category_form.html', category=None, action='Add')


@app.route('/admin/categories/<int:cat_id>/edit', methods=['GET', 'POST'])
@admin_required
def admin_edit_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    if request.method == 'POST':
        cat.name = request.form['name'].strip()
        cat.description = request.form.get('description', '').strip()
        db.session.commit()
        flash('Category updated!', 'success')
        return redirect(url_for('admin_categories'))
    return render_template('admin/category_form.html', category=cat, action='Edit')


@app.route('/admin/categories/<int:cat_id>/delete', methods=['POST'])
@admin_required
def admin_delete_category(cat_id):
    cat = Category.query.get_or_404(cat_id)
    if cat.products:
        flash('Cannot delete category with existing products. Move or delete products first.', 'error')
        return redirect(url_for('admin_categories'))
    db.session.delete(cat)
    db.session.commit()
    flash('Category deleted.', 'success')
    return redirect(url_for('admin_categories'))


# ── Order Management ─────────────────────────────────────────────────────────

@app.route('/admin/orders')
@admin_required
def admin_orders():
    status_filter = request.args.get('status', '')
    query = Order.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    orders = query.order_by(Order.created_at.desc()).all()
    return render_template('admin/orders.html', orders=orders, status_filter=status_filter)


@app.route('/admin/orders/<int:order_id>/status', methods=['POST'])
@admin_required
def admin_update_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    new_status = request.form.get('status')
    allowed = ['pending', 'paid', 'processing', 'shipped', 'delivered', 'cancelled']
    if new_status in allowed:
        order.status = new_status
        db.session.commit()
        flash(f'Order #{order_id} status updated to {new_status}.', 'success')
    else:
        flash('Invalid status.', 'error')
    return redirect(url_for('admin_orders'))


# ── User Management ──────────────────────────────────────────────────────────

@app.route('/admin/users')
@admin_required
def admin_users():
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=users)


# ════════════════════════════════════════════════════════════════════════════
# CUSTOMER ROUTES
# ════════════════════════════════════════════════════════════════════════════

@app.route('/')
def home():
    categories = Category.query.all()
    featured_products = Product.query.filter(
        Product.image_url.isnot(None),
        Product.image_url != '',
        Product.image_url.like('http%')  # only Cloudinary/external URLs, no legacy paths
    ).limit(8).all()
    return render_template('home.html', categories=categories, products=featured_products)


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            email = request.form.get('email', '').strip()
            password = request.form.get('password', '')
            phone = request.form.get('phone', '').strip()
            address = request.form.get('address', '').strip()

            if not username or not email or not password:
                flash('Username, email, and password are required', 'error')
                return render_template('signup.html')
            if len(password) < 6:
                flash('Password must be at least 6 characters long', 'error')
                return render_template('signup.html')
            if User.query.filter_by(username=username).first():
                flash('Username already exists.', 'error')
                return render_template('signup.html')
            if User.query.filter_by(email=email).first():
                flash('Email already exists.', 'error')
                return render_template('signup.html')

            user = User(username=username, email=email,
                        password_hash=generate_password_hash(password),
                        phone=phone or None, address=address or None)
            db.session.add(user)
            db.session.commit()
            send_signup_notification(user)
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Registration failed: {str(e)}', 'error')
    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if not username or not password:
                flash('Username and password are required', 'error')
                return render_template('login.html')
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                login_user(user, remember=True)
                send_login_notification(user)
                flash('Login successful!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page or url_for('home'))
            flash('Invalid username or password', 'error')
        except Exception as e:
            flash(f'Login failed: {str(e)}', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('home'))


@app.route('/profile')
@login_required
def profile():
    return render_template('profile.html', user=current_user)


@app.route('/category/<int:category_id>')
def category_products(category_id):
    category = Category.query.get_or_404(category_id)
    products = Product.query.filter_by(category_id=category_id).all()
    return render_template('category.html', category=category, products=products)


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template('product.html', product=product)


@app.route('/add_to_cart/<int:product_id>', methods=['GET', 'POST'])
@login_required
def add_to_cart(product_id):
    if request.method == 'POST':
        try:
            quantity = int(request.form.get('quantity', 1))
            if quantity < 1:
                flash('Quantity must be at least 1', 'error')
                return redirect(url_for('product_detail', product_id=product_id))
        except (ValueError, TypeError):
            flash('Invalid quantity', 'error')
            return redirect(url_for('product_detail', product_id=product_id))
    else:
        quantity = 1

    product = Product.query.get_or_404(product_id)
    if 'cart' not in session:
        session['cart'] = {}
    cart = session['cart']
    pid = str(product_id)
    cart[pid] = cart.get(pid, 0) + quantity
    session['cart'] = cart
    session.permanent = True
    flash(f'Added {quantity}x {product.name} to cart', 'success')
    return redirect(url_for('product_detail', product_id=product_id))


@app.route('/update_cart/<int:product_id>', methods=['POST'])
@login_required
def update_cart(product_id):
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 1))
        if quantity < 1:
            return jsonify({'success': False, 'message': 'Quantity must be at least 1'}), 400
        product = Product.query.get_or_404(product_id)
        if 'cart' not in session:
            session['cart'] = {}
        session['cart'][str(product_id)] = quantity
        session.modified = True
        subtotal = product.price * quantity
        total = sum(
            (Product.query.get(int(pid)).price * qty)
            for pid, qty in session['cart'].items()
            if Product.query.get(int(pid))
        )
        total_items = sum(session['cart'].values())
        return jsonify({'success': True, 'subtotal': subtotal, 'total': total, 'total_items': total_items})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/remove_from_cart/<int:product_id>', methods=['POST'])
@login_required
def remove_from_cart(product_id):
    try:
        if 'cart' in session and str(product_id) in session['cart']:
            del session['cart'][str(product_id)]
            session.modified = True
            return jsonify({'success': True, 'message': 'Item removed'})
        return jsonify({'success': False, 'message': 'Item not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/cart')
@login_required
def view_cart():
    if 'cart' not in session:
        session['cart'] = {}
    cart_items, total = [], 0
    for pid, qty in session['cart'].items():
        p = Product.query.get(int(pid))
        if p:
            sub = p.price * qty
            cart_items.append({'product': p, 'quantity': qty, 'subtotal': sub})
            total += sub
    return render_template('cart.html', cart_items=cart_items, total=total)


@app.route('/checkout', methods=['POST'])
@login_required
def checkout():
    if 'cart' not in session or not session['cart']:
        flash('Your cart is empty', 'error')
        return redirect(url_for('view_cart'))
    try:
        total_amount = 0
        order = Order(user_id=current_user.id, total_amount=0, status='pending')
        db.session.add(order)
        db.session.flush()
        for pid, qty in session['cart'].items():
            p = Product.query.get(int(pid))
            if p and p.stock >= qty:
                db.session.add(OrderItem(order_id=order.id, product_id=p.id, quantity=qty, price=p.price))
                p.stock -= qty
                total_amount += p.price * qty
            else:
                flash(f'Insufficient stock for {p.name if p else "product"}', 'error')
                db.session.rollback()
                return redirect(url_for('view_cart'))
        order.total_amount = total_amount
        db.session.commit()
        session['cart'] = {}
        send_order_notification(order)
        flash('Order created! Please complete payment with M-Pesa', 'info')
        return redirect(url_for('pay_with_mpesa', order_id=order.id))
    except Exception as e:
        db.session.rollback()
        flash(f'Checkout failed: {str(e)}', 'error')
        return redirect(url_for('view_cart'))


@app.route('/orders')
@login_required
def order_history():
    orders = Order.query.filter_by(user_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template('orders.html', orders=orders)


@app.route('/chat')
@login_required
def chat():
    messages = Message.query.filter(
        (Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)
    ).order_by(Message.timestamp.asc()).all()
    return render_template('chat.html', messages=messages)


@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    content = request.form.get('message', '').strip()
    if content:
        msg = Message(sender_id=current_user.id, receiver_id=None, content=content)
        db.session.add(msg)
        db.session.commit()
        send_chat_notification(msg)
        flash('Message sent!', 'success')
    else:
        flash('Message cannot be empty', 'error')
    return redirect(url_for('chat'))


@app.route('/api/messages')
@login_required
def get_messages():
    messages = Message.query.filter(
        (Message.sender_id == current_user.id) | (Message.receiver_id == current_user.id)
    ).order_by(Message.timestamp.asc()).all()
    return jsonify([{
        'id': m.id, 'sender_id': m.sender_id, 'content': m.content,
        'timestamp': m.timestamp.isoformat(),
        'sender_name': m.sender.username if m.sender else 'Admin'
    } for m in messages])


@app.route('/ai-chat', methods=['POST'])
@login_required
def ai_chat():
    try:
        user_message = request.json.get('message', '').strip()
        if not user_message:
            return jsonify({'error': 'Message is required'}), 400

        rule_resp = simple_chatbot_response(user_message, current_user)
        if rule_resp:
            db.session.add(Message(sender_id=current_user.id, receiver_id=None,
                                   content=f"USER: {user_message}\n\nASSISTANT: {rule_resp}"))
            db.session.commit()
            return jsonify({'response': rule_resp, 'success': True, 'powered_by': 'Rule-based AI'})

        claude_resp = claude_chatbot_response(user_message, current_user)
        if claude_resp:
            db.session.add(Message(sender_id=current_user.id, receiver_id=None,
                                   content=f"USER: {user_message}\n\nASSISTANT: {claude_resp}"))
            db.session.commit()
            return jsonify({'response': claude_resp, 'success': True, 'powered_by': 'Claude AI'})

        return jsonify({'response': "Try asking about products, prices, or orders!", 'success': True})
    except Exception as e:
        print(f"Chat error: {e}")
        return jsonify({'error': 'Error processing message.', 'success': False}), 500


@app.route('/ai-assistant')
@login_required
def ai_assistant_page():
    return render_template('ai_assistant.html')


@app.route('/pay-with-mpesa/<int:order_id>', methods=['GET', 'POST'])
@login_required
def pay_with_mpesa(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        flash('Unauthorized', 'error')
        return redirect(url_for('order_history'))
    if order.status == 'paid':
        flash('Order already paid', 'info')
        return redirect(url_for('order_history'))
    if request.method == 'POST':
        phone = request.form.get('phone_number')
        if not phone:
            flash('Phone number is required', 'error')
            return render_template('mpesa_payment.html', order=order)
        result = mpesa.stk_push(phone_number=phone, amount=order.total_amount,
                                account_reference=f"Order-{order.id}",
                                transaction_desc=f'Payment for Order #{order.id}')
        if result['success']:
            db.session.add(Transaction(order_id=order.id,
                                       checkout_request_id=result['checkout_request_id'],
                                       phone_number=phone, amount=order.total_amount, status='pending'))
            db.session.commit()
            flash('Payment request sent! Enter your M-Pesa PIN on your phone.', 'success')
            return redirect(url_for('payment_status', order_id=order.id))
        flash(f'Payment failed: {result["message"]}', 'error')
    return render_template('mpesa_payment.html', order=order)


@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    try:
        data = request.get_json()
        cb = data.get('Body', {}).get('stkCallback', {})
        result_code = cb.get('ResultCode')
        crid = cb.get('CheckoutRequestID')
        txn = Transaction.query.filter_by(checkout_request_id=crid).first()
        if not txn:
            return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
        if result_code == 0:
            items = cb.get('CallbackMetadata', {}).get('Item', [])
            receipt = next((i['Value'] for i in items if i['Name'] == 'MpesaReceiptNumber'), None)
            txn.status = 'completed'
            txn.mpesa_receipt_number = receipt
            txn.order.status = 'paid'
        else:
            txn.status = 'failed'
            txn.result_desc = cb.get('ResultDesc', '')
        db.session.commit()
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
    except Exception as e:
        return jsonify({'ResultCode': 1, 'ResultDesc': str(e)})


@app.route('/payment-status/<int:order_id>')
@login_required
def payment_status(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        flash('Unauthorized', 'error')
        return redirect(url_for('order_history'))
    txn = Transaction.query.filter_by(order_id=order.id).first()
    return render_template('payment_status.html', order=order, transaction=txn)


@app.route('/api/check-payment/<int:order_id>')
@login_required
def check_payment_status(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    txn = Transaction.query.filter_by(order_id=order.id).first()
    return jsonify({'order_status': order.status,
                    'transaction_status': txn.status if txn else None,
                    'mpesa_receipt': txn.mpesa_receipt_number if txn else None})


@app.route('/test-email')
def test_email_route():
    try:
        msg = EmailMessage(subject="🧪 Test Email", recipients=['ikendi377@gmail.com'],
                           html="<h1>Test OK!</h1>", sender=app.config['MAIL_DEFAULT_SENDER'])
        mail.send(msg)
        return "✅ Test email sent!"
    except Exception as e:
        return f"❌ {str(e)}"


@app.route('/test-claude')
def test_claude_connection():
    if not claude_client:
        return "❌ Claude not initialized."
    try:
        r = claude_client.messages.create(model="claude-sonnet-4-20250514", max_tokens=100,
                                          messages=[{"role": "user", "content": "Say hello!"}])
        return f"✅ Claude works: {r.content[0].text}"
    except Exception as e:
        return f"❌ {str(e)}"


@app.route('/favicon.ico')
def favicon():
    return '', 204


@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500


# ── Database initialisation & seeding ───────────────────────────────────────

def init_db():
    try:
        db.create_all()

        if not Category.query.first():
            # Fresh database — seed all categories including Duka
            categories_data = [
                {'name': 'Construction Materials', 'description': 'Cement, iron sheets, and building supplies'},
                {'name': 'Fresh Produce',           'description': 'Fruits, grains, and fresh food items'},
                {'name': 'Fashion & Cosmetics',     'description': 'Clothing, footwear, and beauty products'},
                {'name': 'Electronics',             'description': 'Smartphones, chargers, and accessories'},
                {'name': 'Cars & Spare Parts',      'description': 'Vehicles and automotive parts'},
                {'name': 'Duka',                    'description': 'Fast-moving everyday shop items — soaps, sugar, rice, cigarettes & more'},
            ]
            for c in categories_data:
                db.session.add(Category(**c))
            db.session.commit()
            print(f"✅ DB seeded: {len(categories_data)} categories")

        else:
            # Existing database — add Duka only if it is missing
            if not Category.query.filter_by(name='Duka').first():
                db.session.add(Category(
                    name='Duka',
                    description='Fast-moving everyday shop items — soaps, sugar, rice, cigarettes & more'
                ))
                db.session.commit()
                print("✅ Duka category added to existing database.")
            else:
                print("ℹ️  Duka category already exists — no action needed.")

    except Exception as e:
        print(f"❌ DB init error: {e}")
        db.session.rollback()


with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
