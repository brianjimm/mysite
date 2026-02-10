
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_mail import Mail, Message as EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
from threading import Thread
from dotenv import load_dotenv
import anthropic
import re
from mpesa_service import MpesaService

# Load environment variables
load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-very-secret-key-change-this-in-production'
# Get database URL and fix for SQLAlchemy
database_url = os.environ.get('DATABASE_URL', 'sqlite:///ecommerce.db')
if database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['WTF_CSRF_ENABLED'] = False

# Gmail configuration with Flask-Mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 465
app.config['MAIL_USE_SSL'] = True
app.config['MAIL_USE_TLS'] = False
app.config['MAIL_USERNAME'] = 'ikendi377@gmail.com'
app.config['MAIL_PASSWORD'] = 'jjzj hwzn yqpo umuw'
app.config['MAIL_DEFAULT_SENDER'] = 'ikendi377@gmail.com'
app.config['ADMIN_EMAIL'] = 'ikendi377@gmail.com'

# Configure Claude API
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
if not ANTHROPIC_API_KEY:
    print("⚠️  WARNING: ANTHROPIC_API_KEY not found in environment variables!")
    print("Please add it to your .env file")

db = SQLAlchemy(app)
mail = Mail(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# Initialize Claude client
try:
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print("✅ Claude AI initialized successfully!")
except Exception as e:
    print(f"❌ Error initializing Claude: {str(e)}")
    claude_client = None

# Initialize M-Pesa service
mpesa = MpesaService()


# Asynchronous email sending
def send_async_email(app, msg):
    with app.app_context():
        try:
            print("=" * 60)
            print("📧 ATTEMPTING TO SEND EMAIL...")
            print(f"From: {msg.sender}")
            print(f"To: {msg.recipients}")
            print(f"Subject: {msg.subject}")
            print("=" * 60)
            mail.send(msg)
            print("=" * 60)
            print("✅ EMAIL SENT SUCCESSFULLY!")
            print("=" * 60)
        except Exception as e:
            print("=" * 60)
            print(f"❌ ERROR SENDING EMAIL: {str(e)}")
            print("=" * 60)
            import traceback
            traceback.print_exc()


def send_email(subject, recipient, html_body):
    """Send email using Flask-Mail and Gmail"""
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


# Database Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
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
    image_url = db.Column(db.String(255))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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


# M-Pesa Transaction Model
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    checkout_request_id = db.Column(db.String(100), unique=True)
    mpesa_receipt_number = db.Column(db.String(50))
    phone_number = db.Column(db.String(15), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    result_desc = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    order = db.relationship('Order', backref='transactions')


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Email notification functions
def send_signup_notification(user):
    """Send email notification when a new user signs up"""
    subject = f"🎉 New Customer Signup - {user.username}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4;">
            <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h2 style="color: #667eea; margin-top: 0;">🎉 New Customer Registration</h2>
                <p style="color: #333; font-size: 16px;">A new customer has signed up on tradioAI!</p>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea;">
                    <p style="margin: 8px 0;"><strong style="color: #555;">Username:</strong> <span style="color: #333;">{user.username}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Email:</strong> <span style="color: #333;">{user.email}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Phone:</strong> <span style="color: #333;">{user.phone or 'Not provided'}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Address:</strong> <span style="color: #333;">{user.address or 'Not provided'}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Registration Time:</strong> <span style="color: #333;">{user.created_at.strftime('%Y-%m-%d %H:%M:%S')}</span></p>
                </div>
                <hr style="border: none; border-top: 1px solid #e1e5e9; margin: 25px 0;">
                <p style="color: #999; font-size: 13px; margin: 0;">This is an automated notification from tradioAI e-commerce platform.</p>
            </div>
        </body>
    </html>
    """
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_login_notification(user):
    """Send email notification when a user logs in"""
    subject = f"🔐 Customer Login - {user.username}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4;">
            <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h2 style="color: #667eea; margin-top: 0;">🔐 Customer Login Notification</h2>
                <p style="color: #333; font-size: 16px;">A customer has logged in to tradioAI.</p>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea;">
                    <p style="margin: 8px 0;"><strong style="color: #555;">Username:</strong> <span style="color: #333;">{user.username}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Email:</strong> <span style="color: #333;">{user.email}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Login Time:</strong> <span style="color: #333;">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}</span></p>
                </div>
                <hr style="border: none; border-top: 1px solid #e1e5e9; margin: 25px 0;">
                <p style="color: #999; font-size: 13px; margin: 0;">This is an automated notification from tradioAI e-commerce platform.</p>
            </div>
        </body>
    </html>
    """
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_order_notification(order):
    """Send email notification when a new order is placed"""
    items_html = ""
    for item in order.items:
        items_html += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e1e5e9;">{item.product.name}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e1e5e9; text-align: center;">{item.quantity}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e1e5e9; text-align: right;">KSh {item.price:,.2f}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e1e5e9; text-align: right; font-weight: 600;">KSh {(item.price * item.quantity):,.2f}</td>
        </tr>
        """

    subject = f"🛒 New Order #{order.id} - {order.customer.username}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4;">
            <div style="max-width: 650px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h2 style="color: #667eea; margin-top: 0;">🛒 New Order Received!</h2>
                <p style="color: #333; font-size: 16px;">A new order has been placed on tradioAI.</p>
                <h3 style="color: #333; margin-top: 30px; margin-bottom: 10px; font-size: 18px;">Customer Information</h3>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; border-left: 4px solid #667eea;">
                    <p style="margin: 8px 0;"><strong style="color: #555;">Name:</strong> <span style="color: #333;">{order.customer.username}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Email:</strong> <span style="color: #333;">{order.customer.email}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Phone:</strong> <span style="color: #333;">{order.customer.phone or 'Not provided'}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Address:</strong> <span style="color: #333;">{order.customer.address or 'Not provided'}</span></p>
                </div>
                <h3 style="color: #333; margin-top: 30px; margin-bottom: 10px; font-size: 18px;">Order Details</h3>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; border-left: 4px solid #667eea;">
                    <p style="margin: 8px 0;"><strong style="color: #555;">Order ID:</strong> <span style="color: #333;">#{order.id}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Order Date:</strong> <span style="color: #333;">{order.created_at.strftime('%Y-%m-%d %H:%M:%S')}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Status:</strong> <span style="color: #333; background-color: #ffc107; padding: 3px 10px; border-radius: 3px; font-weight: 600;">{order.status.upper()}</span></p>
                </div>
                <h3 style="color: #333; margin-top: 30px; margin-bottom: 15px; font-size: 18px;">Order Items</h3>
                <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; background-color: white;">
                    <thead>
                        <tr style="background-color: #667eea; color: white;">
                            <th style="padding: 12px; text-align: left; font-weight: 600;">Product</th>
                            <th style="padding: 12px; text-align: center; font-weight: 600;">Qty</th>
                            <th style="padding: 12px; text-align: right; font-weight: 600;">Price</th>
                            <th style="padding: 12px; text-align: right; font-weight: 600;">Subtotal</th>
                        </tr>
                    </thead>
                    <tbody>
                        {items_html}
                    </tbody>
                </table>
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; margin-top: 20px; text-align: right;">
                    <h3 style="margin: 0; font-size: 24px;">Total: KSh {order.total_amount:,.2f}</h3>
                </div>
                <hr style="border: none; border-top: 1px solid #e1e5e9; margin: 25px 0;">
                <p style="color: #999; font-size: 13px; margin: 0;">This is an automated notification from tradioAI e-commerce platform.</p>
            </div>
        </body>
    </html>
    """
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


def send_chat_notification(message):
    """Send email notification when a customer sends a chat message"""
    subject = f"💬 New Chat Message - {message.sender.username}"
    html_body = f"""
    <!DOCTYPE html>
    <html>
        <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f4f4f4;">
            <div style="max-width: 600px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h2 style="color: #667eea; margin-top: 0;">💬 New Customer Message</h2>
                <p style="color: #333; font-size: 16px;">You have received a new message from a customer.</p>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #667eea;">
                    <p style="margin: 8px 0;"><strong style="color: #555;">From:</strong> <span style="color: #333;">{message.sender.username}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Email:</strong> <span style="color: #333;">{message.sender.email}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Phone:</strong> <span style="color: #333;">{message.sender.phone or 'Not provided'}</span></p>
                    <p style="margin: 8px 0;"><strong style="color: #555;">Time:</strong> <span style="color: #333;">{message.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</span></p>
                </div>
                <div style="background-color: #fff; border: 2px solid #667eea; border-radius: 8px; padding: 20px; margin: 20px 0;">
                    <p style="margin: 0 0 10px 0; color: #555; font-weight: 600; font-size: 14px;">MESSAGE:</p>
                    <p style="margin: 0; color: #333; font-size: 16px; line-height: 1.6;">{message.content}</p>
                </div>
                <div style="background-color: #e8f4fd; padding: 15px; border-radius: 5px; margin-top: 20px;">
                    <p style="margin: 0; color: #1976d2; font-size: 14px;">💡 <strong>Tip:</strong> Login to your admin panel to respond to this message.</p>
                </div>
                <hr style="border: none; border-top: 1px solid #e1e5e9; margin: 25px 0;">
                <p style="color: #999; font-size: 13px; margin: 0;">This is an automated notification from tradioAI e-commerce platform.</p>
            </div>
        </body>
    </html>
    """
    send_email(subject, app.config['ADMIN_EMAIL'], html_body)


# ============================================
# RULE-BASED CHATBOT (HANDLES 70% OF QUERIES)
# ============================================

def simple_chatbot_response(message, user):
    """Rule-based chatbot for common queries - 100% FREE"""
    message_lower = message.lower()

    # Greetings
    if re.search(r'\b(hi|hello|hey|good morning|good afternoon|good evening|hola|jambo|habari)\b', message_lower):
        return f"Hello {user.username}! 👋 Welcome to tradioAI. How can I assist you today?\n\n💡 You can ask me about:\n• Products and prices\n• Order status\n• Product recommendations\n• Shopping assistance"

    # Thank you
    if re.search(r'\b(thank|thanks|appreciate|asante)\b', message_lower):
        return "You're very welcome! 😊 Is there anything else I can help you with today?"

    # Help
    if re.search(r'\b(help|what can you|how do you work|commands)\b', message_lower):
        return """🤖 **I'm your AI shopping assistant!** Here's what I can do:

🛍️ **Product Help:**
• Find products by category or name
• Check prices and stock availability
• Get detailed product information

📦 **Order Management:**
• Track your order status
• View order history
• Get delivery updates

💰 **Shopping Assistance:**
• Budget planning and recommendations
• Product comparisons
• Answer any questions about our products

Just chat with me naturally! For example:
• "Show me construction materials"
• "Do you have phones under 10000?"
• "What's the status of my order?"
• "I need cement for a 3-bedroom house" """

    # Construction materials
    if re.search(r'\b(cement|mabati|iron sheet|steel|construction|building material)\b', message_lower):
        products = Product.query.filter(Product.category_id == 1, Product.stock > 0).limit(8).all()
        if products:
            response = "🏗️ **Construction Materials Available:**\n\n"
            for p in products:
                response += f"📦 **{p.name}**\n   💰 KSh {p.price:,.2f}\n   📊 Stock: {p.stock} units\n\n"
            response += "Need help choosing? Just ask me about specific products!"
            return response
        return "🏗️ We're currently restocking construction materials. Please check back soon!"

    # Electronics
    if re.search(r'\b(phone|smartphone|mobile|itel|tecno|redmi|charger|electronics|laptop|speaker|gadget)\b', message_lower):
        products = Product.query.filter(Product.category_id == 4, Product.stock > 0).limit(8).all()
        if products:
            response = "📱 **Electronics Available:**\n\n"
            for p in products:
                response += f"📱 **{p.name}**\n   💰 KSh {p.price:,.2f}\n   📊 Stock: {p.stock} units\n\n"
            response += "💡 Need help deciding? Tell me your budget and requirements!"
            return response
        return "📱 Electronics are being restocked. Check back soon!"

    # Fashion & Cosmetics
    if re.search(r'\b(shoes|dress|fashion|clothes|clothing|lotion|cosmetic|soap|nivea|jeans|sandal|footwear)\b', message_lower):
        products = Product.query.filter(Product.category_id == 3, Product.stock > 0).limit(8).all()
        if products:
            response = "👗 **Fashion & Cosmetics Available:**\n\n"
            for p in products:
                response += f"👔 **{p.name}**\n   💰 KSh {p.price:,.2f}\n   📊 Stock: {p.stock} units\n\n"
            response += "✨ Looking for something specific? Let me know!"
            return response
        return "👗 Fashion items are being restocked. New arrivals coming soon!"

    # Fresh Produce
    if re.search(r'\b(food|produce|rice|beans|flour|vegetable|fruit|maize|potato|sugar|groceries|cooking)\b', message_lower):
        products = Product.query.filter(Product.category_id == 2, Product.stock > 0).limit(8).all()
        if products:
            response = "🌾 **Fresh Produce Available:**\n\n"
            for p in products:
                response += f"🥬 **{p.name}**\n   💰 KSh {p.price:,.2f}\n   📊 Stock: {p.stock} units\n\n"
            response += "🌟 Fresh quality guaranteed! Need recipes or meal planning help?"
            return response
        return "🌾 Fresh produce arriving soon! Check back shortly."

    # Auto parts
    if re.search(r'\b(car|auto|automobile|spare part|engine|oil|brake|tire|tyre|battery|vehicle)\b', message_lower):
        products = Product.query.filter(Product.category_id == 5, Product.stock > 0).limit(8).all()
        if products:
            response = "🚗 **Auto Parts & Accessories Available:**\n\n"
            for p in products:
                response += f"🔧 **{p.name}**\n   💰 KSh {p.price:,.2f}\n   📊 Stock: {p.stock} units\n\n"
            response += "🚘 Need help finding parts? Tell me your car make and model!"
            return response
        return "🚗 Auto parts being restocked. Check back soon!"

    # Order tracking
    if re.search(r'\b(order|orders|track|status|purchase|purchased|bought|delivery)\b', message_lower):
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(5).all()
        if orders:
            response = "📦 **Your Recent Orders:**\n\n"
            for o in orders:
                status_emoji = "⏳" if o.status == "pending" else "✅" if o.status == "completed" else "🚚"
                response += f"{status_emoji} **Order #{o.id}**\n"
                response += f"   📊 Status: {o.status.upper()}\n"
                response += f"   💰 Total: KSh {o.total_amount:,.2f}\n"
                response += f"   📅 Date: {o.created_at.strftime('%B %d, %Y')}\n"
                response += f"   📦 Items: {len(o.items)} product(s)\n\n"
            return response
        return "📦 You haven't placed any orders yet. Start shopping to see your orders here!"

    # Price inquiry
    if re.search(r'\b(price|cost|how much|what.*cost)\b', message_lower):
        products = Product.query.filter(Product.stock > 0).all()
        for p in products:
            if p.name.lower() in message_lower:
                return f"💰 **{p.name}**\n\n💵 Price: KSh {p.price:,.2f}\n📊 Stock: {p.stock} available\n📁 Category: {p.category.name}\n\n📝 {p.description}\n\n✅ Ready to add to cart!"

        return "💰 What product would you like to know the price of?\n\nI can help with:\n• Construction materials\n• Electronics\n• Fashion items\n• Fresh produce\n• Auto parts"

    # Return None if no pattern matched (will use Claude AI)
    return None


# ============================================
# CLAUDE AI-POWERED CHATBOT
# ============================================

def claude_chatbot_response(message, user):
    """AI-powered response using Claude Sonnet 4"""

    if not claude_client:
        return "Sorry, AI service is temporarily unavailable. Please try the help menu or contact support."

    try:
        # Get user context
        orders = Order.query.filter_by(user_id=user.id).all()
        order_context = f"Customer has {len(orders)} total orders. "

        if orders:
            latest = orders[-1]
            order_context += f"Most recent order: #{latest.id}, Status: {latest.status}, Total: KSh {latest.total_amount:,.2f}. "

        # Get available products (limit to save tokens)
        products = Product.query.filter(Product.stock > 0).limit(20).all()
        product_summary = {}

        for category in Category.query.all():
            cat_products = [p for p in products if p.category_id == category.id]
            if cat_products:
                product_summary[category.name] = [
                    f"{p.name} - KSh {p.price:,.2f} ({p.stock} in stock)"
                    for p in cat_products[:4]  # Top 4 per category
                ]

        # Build system prompt
        system_prompt = f"""You are a friendly and professional shopping assistant for tradioAI, a Kenyan e-commerce platform.

**Customer Information:**
- Name: {user.username}
- Email: {user.email}
- {order_context}

**Available Products:**
"""

        for cat, prods in product_summary.items():
            system_prompt += f"\n**{cat}:**\n"
            for prod in prods:
                system_prompt += f"  • {prod}\n"

        system_prompt += """

**Your Responsibilities:**
1. Help customers find the right products for their needs
2. Answer questions about pricing, stock, and product features
3. Provide helpful recommendations based on their requirements
4. Assist with order inquiries using the order context provided
5. Be conversational, warm, and professional
6. Use Kenyan context (KSh currency, local references)
7. Keep responses concise (2-4 paragraphs maximum)
8. Use emojis occasionally to be friendly

**Important Guidelines:**
- Only recommend products from the available list
- If a product isn't available, suggest alternatives or say it's not in stock
- For orders, refer to the order context provided
- Be honest about limitations
- Always aim to help the customer make informed decisions"""
# Call Claude API
        message_response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            temperature=0.7,
            system=system_prompt,
            messages=[
                {"role": "user", "content": message}
            ]
        )

        # Extract response text
        response_text = message_response.content[0].text

        return response_text

    except anthropic.APIError as e:
        print(f"Claude API Error: {str(e)}")
        return "I'm having trouble connecting to my AI brain right now. Could you try rephrasing your question, or use the quick action buttons below?"
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return "Something went wrong. Please try again or contact support if the issue persists."

    # Routes
@app.route('/')
def home():
    categories = Category.query.all()
    featured_products = Product.query.limit(8).all()
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

            print(f"DEBUG: Received signup data - username: {username}, email: {email}")

            if not username or not email or not password:
                flash('Username, email, and password are required', 'error')
                return render_template('signup.html')

            if len(password) < 6:
                flash('Password must be at least 6 characters long', 'error')
                return render_template('signup.html')

            existing_user = User.query.filter_by(username=username).first()
            if existing_user:
                flash('Username already exists. Please choose a different username.', 'error')
                return render_template('signup.html')

            existing_email = User.query.filter_by(email=email).first()
            if existing_email:
                flash('Email already exists. Please use a different email.', 'error')
                return render_template('signup.html')

            user = User(
                username=username,
                email=email,
                password_hash=generate_password_hash(password),
                phone=phone or None,
                address=address or None
            )

            db.session.add(user)
            db.session.commit()

            print(f"DEBUG: User created successfully with ID: {user.id}")
            send_signup_notification(user)
            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))

        except Exception as e:
            db.session.rollback()
            print(f"DEBUG: Registration error: {str(e)}")
            flash(f'Registration failed: {str(e)}', 'error')
            return render_template('signup.html')

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        try:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')

            print(f"DEBUG: Login attempt - username: {username}")

            if not username or not password:
                flash('Username and password are required', 'error')
                return render_template('login.html')

            user = User.query.filter_by(username=username).first()

            if user and check_password_hash(user.password_hash, password):
                login_user(user, remember=True)
                print(f"DEBUG: User logged in successfully: {user.username}")
                send_login_notification(user)
                flash('Login successful!', 'success')
                next_page = request.args.get('next')
                return redirect(next_page) if next_page else redirect(url_for('home'))
            else:
                flash('Invalid username or password', 'error')

        except Exception as e:
            print(f"DEBUG: Login error: {str(e)}")
            flash(f'Login failed: {str(e)}', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out successfully.', 'info')
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
    """Add product to cart with specified quantity"""
    # Get quantity from form (POST) or default to 1 (GET)
    if request.method == 'POST':
        try:
            quantity = int(request.form.get('quantity', 1))
            # Validate quantity is positive
            if quantity < 1:
                flash('Quantity must be at least 1', 'error')
                return redirect(url_for('product_detail', product_id=product_id))
        except (ValueError, TypeError):
            flash('Invalid quantity specified', 'error')
            return redirect(url_for('product_detail', product_id=product_id))
    else:
        quantity = 1  # Default for backward compatibility with GET requests

    # Verify product exists
    product = Product.query.get_or_404(product_id)

    # Initialize cart if it doesn't exist
    if 'cart' not in session:
        session['cart'] = {}

    cart = session['cart']
    product_id_str = str(product_id)

    # Add or update quantity in cart
    if product_id_str in cart:
        cart[product_id_str] += quantity
    else:
        cart[product_id_str] = quantity

    # Save cart to session
    session['cart'] = cart
    session.permanent = True

    # Success message with quantity info
    if quantity == 1:
        flash(f'Added {product.name} to cart', 'success')
    else:
        flash(f'Added {quantity} units of {product.name} to cart', 'success')

    return redirect(url_for('product_detail', product_id=product_id))


@app.route('/update_cart/<int:product_id>', methods=['POST'])
@login_required
def update_cart(product_id):
    """Update cart item quantity via AJAX"""
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 1))

        if quantity < 1:
            return jsonify({'success': False, 'message': 'Quantity must be at least 1'}), 400

        # Verify product exists
        product = Product.query.get_or_404(product_id)

        if 'cart' not in session:
            session['cart'] = {}

        # Update quantity in cart
        session['cart'][str(product_id)] = quantity
        session.modified = True

        # Calculate subtotal for this item
        subtotal = product.price * quantity

        # Calculate cart totals
        total = 0
        total_items = 0
        for prod_id, qty in session['cart'].items():
            prod = Product.query.get(int(prod_id))
            if prod:
                total += prod.price * qty
                total_items += qty

        return jsonify({
            'success': True,
            'subtotal': subtotal,
            'total': total,
            'total_items': total_items
        })

    except Exception as e:
        print(f"Error updating cart: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/remove_from_cart/<int:product_id>', methods=['POST'])
@login_required
def remove_from_cart(product_id):
    """Remove item from cart"""
    try:
        if 'cart' in session and str(product_id) in session['cart']:
            del session['cart'][str(product_id)]
            session.modified = True

            return jsonify({
                'success': True,
                'message': 'Item removed from cart'
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Item not found in cart'
            }), 404

    except Exception as e:
        print(f"Error removing from cart: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/cart')
@login_required
def view_cart():
    if 'cart' not in session:
        session['cart'] = {}

    cart_items = []
    total = 0

    for product_id, quantity in session['cart'].items():
        product = Product.query.get(int(product_id))
        if product:
            subtotal = product.price * quantity
            cart_items.append({
                'product': product,
                'quantity': quantity,
                'subtotal': subtotal
            })
            total += subtotal

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

        for product_id, quantity in session['cart'].items():
            product = Product.query.get(int(product_id))
            if product and product.stock >= quantity:
                order_item = OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    quantity=quantity,
                    price=product.price
                )
                db.session.add(order_item)
                product.stock -= quantity
                total_amount += product.price * quantity
            else:
                flash(f'Insufficient stock for {product.name if product else "unknown product"}', 'error')
                db.session.rollback()
                return redirect(url_for('view_cart'))

        order.total_amount = total_amount
        db.session.commit()

        # Clear cart
        session['cart'] = {}

        # Redirect to M-Pesa payment instead of completing order
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
        message = Message(
            sender_id=current_user.id,
            receiver_id=None,
            content=content
        )
        db.session.add(message)
        db.session.commit()
        send_chat_notification(message)
        flash('Message sent successfully!', 'success')
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
        'id': msg.id,
        'sender_id': msg.sender_id,
        'content': msg.content,
        'timestamp': msg.timestamp.isoformat(),
        'sender_name': msg.sender.username if msg.sender else 'Admin'
    } for msg in messages])


# ============================================
# AI CHATBOT ROUTES
# ============================================

@app.route('/ai-chat', methods=['POST'])
@login_required
def ai_chat():
    """Hybrid AI chatbot with cost optimization"""
    try:
        user_message = request.json.get('message', '').strip()

        if not user_message:
            return jsonify({'error': 'Message is required'}), 400

        print(f"💬 Message from {current_user.username}: {user_message}")

        # Step 1: Try rule-based response first (FREE & INSTANT)
        rule_response = simple_chatbot_response(user_message, current_user)

        if rule_response:
            # Save to database
            chat_message = Message(
                sender_id=current_user.id,
                receiver_id=None,
                content=f"USER: {user_message}\n\nASSISTANT: {rule_response}"
            )
            db.session.add(chat_message)
            db.session.commit()

            print(f"✅ Responded with rule-based answer (FREE)")

            return jsonify({
                'response': rule_response,
                'success': True,
                'type': 'instant',
                'powered_by': 'Rule-based AI'
            })

        # Step 2: Use Claude AI for complex queries
        print(f"🤖 Using Claude AI for complex query...")
        claude_response = claude_chatbot_response(user_message, current_user)

        if claude_response:
            # Save to database
            chat_message = Message(
                sender_id=current_user.id,
                receiver_id=None,
                content=f"USER: {user_message}\n\nASSISTANT (CLAUDE): {claude_response}"
            )
            db.session.add(chat_message)
            db.session.commit()

            print(f"✅ Responded with Claude AI (~$0.002 cost)")

            return jsonify({
                'response': claude_response,
                'success': True,
                'type': 'ai-powered',
                'powered_by': 'Claude AI by Anthropic'
            })

        # Fallback response
        fallback = """I'm here to help! 🤖
You can ask me about:
• **Products** - Browse by category or search specific items
• **Prices** - Check costs and availability
• **Orders** - Track your purchases
• **Recommendations** - Get personalized suggestions

Try the quick action buttons below or ask me anything!"""

        return jsonify({
            'response': fallback,
            'success': True,
            'type': 'fallback',
            'powered_by': 'Fallback system'
        })

    except Exception as e:
        print(f"❌ Chat error: {str(e)}")
        import traceback
        traceback.print_exc()

        return jsonify({
            'error': 'Sorry, I encountered an error. Please try again.',
            'success': False
        }), 500


@app.route('/ai-assistant')
@login_required
def ai_assistant_page():
    """Render the AI chat interface"""
    return render_template('ai_assistant.html')


# ============================================
# M-PESA PAYMENT ROUTES
# ============================================

@app.route('/pay-with-mpesa/<int:order_id>', methods=['GET', 'POST'])
@login_required
def pay_with_mpesa(order_id):
    """Initiate M-Pesa payment for an order"""
    order = Order.query.get_or_404(order_id)

    # Verify order belongs to current user
    if order.user_id != current_user.id:
        flash('Unauthorized access', 'error')
        return redirect(url_for('order_history'))

    # Check if already paid
    if order.status == 'paid':
        flash('This order has already been paid', 'info')
        return redirect(url_for('order_history'))

    if request.method == 'POST':
        phone = request.form.get('phone_number')

        if not phone:
            flash('Phone number is required', 'error')
            return render_template('mpesa_payment.html', order=order)

        # Initiate STK Push
        result = mpesa.stk_push(
            phone_number=phone,
            amount=order.total_amount,
            account_reference=f"Order-{order.id}",
            transaction_desc=f'Payment for Order #{order.id}'
        )

        if result['success']:
            # Save transaction
            transaction = Transaction(
                order_id=order.id,
                checkout_request_id=result['checkout_request_id'],
                phone_number=phone,
                amount=order.total_amount,
                status='pending'
            )
            db.session.add(transaction)
            db.session.commit()

            flash('Payment request sent! Please check your phone and enter your M-Pesa PIN', 'success')
            return redirect(url_for('payment_status', order_id=order.id))
        else:
            flash(f'Payment initiation failed: {result["message"]}', 'error')

    return render_template('mpesa_payment.html', order=order)


@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    """Handle M-Pesa payment callback"""
    try:
        data = request.get_json()
        print(f"M-Pesa Callback received: {data}")

        stk_callback = data.get('Body', {}).get('stkCallback', {})
        result_code = stk_callback.get('ResultCode')
        checkout_request_id = stk_callback.get('CheckoutRequestID')
        result_desc = stk_callback.get('ResultDesc', '')

        # Find transaction
        transaction = Transaction.query.filter_by(
            checkout_request_id=checkout_request_id
        ).first()

        if not transaction:
            print(f"Transaction not found: {checkout_request_id}")
            return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})

        if result_code == 0:
            # Payment successful
            callback_metadata = stk_callback.get('CallbackMetadata', {}).get('Item', [])

            mpesa_receipt = next(
                (item['Value'] for item in callback_metadata if item['Name'] == 'MpesaReceiptNumber'),
                None
            )

            # Update transaction
            transaction.status = 'completed'
            transaction.mpesa_receipt_number = mpesa_receipt
            transaction.result_desc = result_desc

            # Update order
            order = transaction.order
            order.status = 'paid'

            db.session.commit()

            print(f"Payment successful: Order #{order.id}, Receipt: {mpesa_receipt}")
        else:
            # Payment failed
            transaction.status = 'failed'
            transaction.result_desc = result_desc

            db.session.commit()

            print(f"Payment failed: {result_desc}")

        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})

    except Exception as e:
        print(f"Callback error: {e}")
        return jsonify({'ResultCode': 1, 'ResultDesc': str(e)})

@app.route('/payment-status/<int:order_id>')
@login_required
def payment_status(order_id):
    """Check payment status for an order"""
    order = Order.query.get_or_404(order_id)

    if order.user_id != current_user.id:
        flash('Unauthorized access', 'error')
        return redirect(url_for('order_history'))

    transaction = Transaction.query.filter_by(order_id=order.id).first()

    return render_template('payment_status.html', order=order, transaction=transaction)


@app.route('/api/check-payment/<int:order_id>')
@login_required
def check_payment_status(order_id):
    """API endpoint to check payment status (for AJAX polling)"""
    order = Order.query.get_or_404(order_id)

    if order.user_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    transaction = Transaction.query.filter_by(order_id=order.id).first()

    return jsonify({
        'order_status': order.status,
        'transaction_status': transaction.status if transaction else None,
        'mpesa_receipt': transaction.mpesa_receipt_number if transaction else None
    })

# ============================================
# TESTING ROUTES
# ============================================

@app.route('/test-email')
def test_email_route():
    """Test route to check if email sending works"""
    try:
        print("\n🧪 TESTING EMAIL CONFIGURATION...")
        msg = EmailMessage(
            subject="🧪 Test Email from tradioAI",
            recipients=['ikendi377@gmail.com'],
            html="<h1>Test Successful!</h1><p>Your email configuration is working!</p>",
            sender=app.config['MAIL_DEFAULT_SENDER']
        )
        mail.send(msg)
        print("✅ Test email sent synchronously!")
        return "✅ Test email sent! Check ikendi377@gmail.com (including spam folder)"
    except Exception as e:
        print(f"❌ Test email failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return f"❌ Error: {str(e)}"


@app.route('/test-claude')
def test_claude_connection():
    """Test if Claude API is working"""
    if not claude_client:
        return "❌ Claude client not initialized. Check your API key in .env file"

    try:
        # Simple test message
        response = claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=100,
            messages=[
                {"role": "user", "content": "Say 'Hello from tradioAI!' if you're working"}
            ]
        )

        return f"✅ Claude AI is working!<br><br>Response: {response.content[0].text}"

    except Exception as e:
        return f"❌ Error testing Claude: {str(e)}"


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


def init_db():
    """Initialize the database with sample data"""
    try:
        db.create_all()
        print("DEBUG: Database tables created successfully")

        if Category.query.first():
            print("DEBUG: Sample data already exists, skipping initialization")
            return

        print("DEBUG: Adding sample data...")

        categories_data = [
            {'name': 'Construction Materials', 'description': 'Cement, iron sheets, and building supplies'},
            {'name': 'Fresh Produce', 'description': 'Fruits, grains, and fresh food items'},
            {'name': 'Fashion & Cosmetics', 'description': 'Clothing, footwear, and beauty products'},
            {'name': 'Electronics', 'description': 'Smartphones, laptops, chargers, and accessories'},
            {'name': 'Cars & Spare Parts', 'description': 'Vehicles and automotive parts'}
        ]

        for cat_data in categories_data:
            category = Category(**cat_data)
            db.session.add(category)

        db.session.commit()

        products_data = [
            # Fresh Produce (Category 2)
            {'name': 'Ndengu (Green Grams)', 'description': 'Fresh green grams from local farms', 'price': 180.00, 'stock': 300, 'category_id': 2, 'image_url': 'ndengu.jpeg'},
            {'name': 'Pika Cooking Oil', 'description': 'Premium cooking oil for healthy meals', 'price': 450.00, 'stock': 200, 'category_id': 2, 'image_url': 'pikaoil.jpeg'},
            {'name': 'Beans 1kg', 'description': 'High quality beans', 'price': 150.00, 'stock': 350, 'category_id': 2, 'image_url': 'beans.jpeg'},
            {'name': 'Sweet Potatoes 1kg', 'description': 'Fresh sweet potatoes', 'price': 100.00, 'stock': 400, 'category_id': 2, 'image_url': 'sweetpotatoes.jpeg'},
            {'name': 'Potatoes 1kg', 'description': 'Farm fresh potatoes', 'price': 120.00, 'stock': 450, 'category_id': 2, 'image_url': 'potatoes.jpeg'},
            {'name': 'Arrowroots 1kg', 'description': 'Fresh arrowroots', 'price': 130.00, 'stock': 250, 'category_id': 2, 'image_url': 'arrowroots.jpeg'},
            {'name': 'Onions 1kg', 'description': 'Fresh onions', 'price': 140.00, 'stock': 380, 'category_id': 2, 'image_url': 'onions.jpeg'},
            {'name': 'Maize 2kg', 'description': 'Fresh maize grains', 'price': 200.00, 'stock': 320, 'category_id': 2, 'image_url': 'maize.jpeg'},
            {'name': 'Wheat Flour 2kg', 'description': 'Premium wheat flour', 'price': 280.00, 'stock': 500, 'category_id': 2, 'image_url': 'wheatflour.jpeg'},
            {'name': 'Maize Flour 2kg', 'description': 'Quality maize flour', 'price': 180.00, 'stock': 550, 'category_id': 2, 'image_url': 'maizeflour.jpeg'},
            {'name': 'Fresh Avocados 1kg', 'description': 'Ripe avocados', 'price': 250.00, 'stock': 200, 'category_id': 2, 'image_url': 'avocados.jpeg'},
            {'name': 'Rice 5kg Bag', 'description': 'Premium white rice', 'price': 650.00, 'stock': 300, 'category_id': 2, 'image_url': 'rice.jpg'},
            {'name': 'Sugar 2kg', 'description': 'Pure white sugar', 'price': 320.00, 'stock': 400, 'category_id': 2, 'image_url': 'sugar.jpeg'},

            # Fashion & Cosmetics (Category 3)
            {'name': 'Nivea Body Lotion', 'description': 'Moisturizing body lotion', 'price': 850.00, 'stock': 120, 'category_id': 3, 'image_url': 'nivealotion.jpeg'},
            {'name': 'Ladies Closed Shoes', 'description': 'Elegant ladies closed shoes', 'price': 2800.00, 'stock': 60, 'category_id': 3, 'image_url': 'ladiesclosedshoes.jpeg'},
            {'name': 'Loafers', 'description': 'Comfortable loafers', 'price': 2200.00, 'stock': 75, 'category_id': 3, 'image_url': 'loafers.jpeg'},
            {'name': 'Rubber Shoes Type 3', 'description': 'Durable rubber shoes', 'price': 1500.00, 'stock': 100, 'category_id': 3, 'image_url': 'rubber3.jpeg'},
            {'name': 'Nivea Deodorant', 'description': 'Fresh scent deodorant', 'price': 450.00, 'stock': 150, 'category_id': 3, 'image_url': 'niveadeodrant.jpeg'},
            {'name': 'Msafi Bar Soap', 'description': 'Quality bar soap', 'price': 120.00, 'stock': 300, 'category_id': 3, 'image_url': 'msafibar.jpeg'},
            {'name': 'Rubber Shoes', 'description': 'Comfortable rubber shoes', 'price': 1400.00, 'stock': 95, 'category_id': 3, 'image_url': 'rubbershoes.jpeg'},
            {'name': 'Ladies Sandals', 'description': 'Stylish ladies sandals', 'price': 1800.00, 'stock': 80, 'category_id': 3, 'image_url': 'ladiessandles.jpeg'},
            {'name': 'Geisha Black Soap', 'description': 'Premium black soap', 'price': 200.00, 'stock': 250, 'category_id': 3, 'image_url': 'geishablack.jpeg'},
            {'name': 'Msafi Liquid Soap', 'description': 'Liquid hand soap', 'price': 180.00, 'stock': 200, 'category_id': 3, 'image_url': 'msafisoap.jpeg'},
            {'name': 'Geisha Soap', 'description': 'Quality beauty soap', 'price': 150.00, 'stock': 280, 'category_id': 3, 'image_url': 'geisha.jpeg'},
            {'name': 'Rubber Shoes Type 2', 'description': 'Sporty rubber shoes', 'price': 1600.00, 'stock': 90, 'category_id': 3, 'image_url': 'rubber2.jpeg'},
            {'name': 'Leather Brown Shoes', 'description': 'Premium leather shoes', 'price': 3500.00, 'stock': 45, 'category_id': 3, 'image_url': 'leatherbrown.jpeg'},
            {'name': 'Rubber Shoes Type 1', 'description': 'Classic rubber shoes', 'price': 1350.00, 'stock': 110, 'category_id': 3, 'image_url': 'rubber1.jpeg'},
            {'name': 'Darling Zuri Hair', 'description': 'Premium hair extensions', 'price': 950.00, 'stock': 85, 'category_id': 3, 'image_url': 'darlingzuri.jpg'},
            {'name': 'Darling Pamoja Hair', 'description': 'Quality braiding hair', 'price': 850.00, 'stock': 100, 'category_id': 3, 'image_url': 'darlingpamoja.jpg'},
            {'name': 'Ladies Dress Style 1', 'description': 'Elegant ladies dress', 'price': 2200.00, 'stock': 55, 'category_id': 3, 'image_url': 'ladiesdress1.jpeg'},
            {'name': 'Ladies 600 Dress', 'description': 'Affordable ladies dress', 'price': 600.00, 'stock': 120, 'category_id': 3, 'image_url': 'ladies600dress.jpeg'},
            {'name': 'Nivea Shower Gel', 'description': 'Refreshing shower gel', 'price': 650.00, 'stock': 90, 'category_id': 3, 'image_url': 'niveashowergel.jpeg'},
            {'name': 'Nivea Men Products', 'description': 'Grooming products for men', 'price': 750.00, 'stock': 100, 'category_id': 3, 'image_url': 'niveamen.jpeg'},
            {'name': 'Blue Jeans', 'description': 'Classic blue denim jeans', 'price': 1800.00, 'stock': 70, 'category_id': 3, 'image_url': 'jeansblue.jpeg'},
            {'name': 'Nivea Lip Balm', 'description': 'Moisturizing lip balm', 'price': 280.00, 'stock': 150, 'category_id': 3, 'image_url': 'nivealipbum.jpeg'},
            {'name': 'Toughees Shoes', 'description': 'Durable school shoes', 'price': 1950.00, 'stock': 65, 'category_id': 3, 'image_url': 'toughees.jpeg'},

            # Electronics (Category 4)
            {'name': 'Itel 1550 Phone', 'description': 'Basic Itel phone', 'price': 2200.00, 'stock': 90, 'category_id': 4, 'image_url': 'itel1550.jpeg'},
            {'name': 'Itel 2200 Phone', 'description': 'Affordable Itel smartphone', 'price': 5500.00, 'stock': 65, 'category_id': 4, 'image_url': 'itel2200.jpeg'},
            {'name': 'Itel 2700 Phone', 'description': 'Feature-rich Itel phone', 'price': 7800.00, 'stock': 50, 'category_id': 4, 'image_url': 'itel2700.jpeg'},
            {'name': 'Redmi A5 Smartphone', 'description': 'Xiaomi Redmi A5 smartphone', 'price': 12500.00, 'stock': 40, 'category_id': 4, 'image_url': 'redmia5.jpeg'},
            {'name': 'Oraimo Fast Charger', 'description': 'Oraimo fast charging adapter with cable', 'price': 1200.00, 'stock': 150, 'category_id': 4, 'image_url': 'oraimo_charger.jpeg'},
            {'name': 'Oraimo USB Cable', 'description': 'Durable Oraimo USB charging cable', 'price': 450.00, 'stock': 200, 'category_id': 4, 'image_url': 'oraimo_cable.jpeg'},
            {'name': 'Bulb Neelux LED', 'description': 'Energy efficient Neelux LED bulb', 'price': 350.00, 'stock': 250, 'category_id': 4, 'image_url': 'bulb_neelux.jpeg'},

            # Cars & Spare Parts (Category 5)
            {'name': 'Quartz Engine Oil', 'description': 'Premium quartz engine oil', 'price': 3800.00, 'stock': 80, 'category_id': 5, 'image_url': 'quartzengineoil.jpeg'},
            {'name': 'Motorbike Indicators', 'description': 'Universal motorbike indicators', 'price': 650.00, 'stock': 120, 'category_id': 5, 'image_url': 'motorbikeindicators.jpeg'},
            {'name': 'Shell Engine Oil', 'description': 'Trusted Shell engine oil', 'price': 4200.00, 'stock': 70, 'category_id': 5, 'image_url': 'shellengineoil.jpeg'},
            {'name': 'Dayun Helmet', 'description': 'Safety motorbike helmet', 'price': 2800.00, 'stock': 60, 'category_id': 5, 'image_url': 'dayunhelmet.jpeg'},
            {'name': 'Motorbike Headlights', 'description': 'Bright LED headlights', 'price': 1500.00, 'stock': 90, 'category_id': 5, 'image_url': 'motorbikeheadlights.jpg'},
            {'name': 'Bike Tire', 'description': 'Durable motorbike tire', 'price': 3500.00, 'stock': 55, 'category_id': 5, 'image_url': 'biketire.jpeg'},
            {'name': 'Toyota Spark Plugs', 'description': 'Genuine Toyota spark plugs', 'price': 1800.00, 'stock': 100, 'category_id': 5, 'image_url': 'toyotaplugs.jpeg'},
            {'name': 'Air Filter', 'description': 'Quality car air filter', 'price': 1200.00, 'stock': 110, 'category_id': 5, 'image_url': 'airfilter.jpeg'},
            {'name': 'General Lubricant', 'description': 'Multi-purpose lubricant', 'price': 450.00, 'stock': 200, 'category_id': 5, 'image_url': 'generallubricant.jpeg'},
            {'name': 'Engine Rings', 'description': 'Replacement engine rings', 'price': 2500.00, 'stock': 75, 'category_id': 5, 'image_url': 'rings.jpeg'},
            {'name': 'Engine Valve', 'description': 'Quality engine valve', 'price': 3200.00, 'stock': 65, 'category_id': 5, 'image_url': 'enginevalve.jpeg'},

            # Construction Materials (Category 1)
            {'name': 'Versatile Iron Sheets', 'description': 'Versatile corrugated iron sheets', 'price': 1350.00, 'stock': 300, 'category_id': 1, 'image_url': 'versatileironsheets.jpeg'},
            {'name': 'Corrugated Mabati', 'description': 'Standard corrugated iron sheets', 'price': 1250.00, 'stock': 350, 'category_id': 1, 'image_url': 'currogatedmabati.jpeg'},
            {'name': 'Nguvu Cement 50kg', 'description': 'High quality Bamburi Nguvu cement for construction', 'price': 850.00, 'stock': 1000, 'category_id': 1, 'image_url': 'cement_nguvu.jpeg'},
            {'name': 'Blue Triangle Cement 50kg', 'description': 'Premium Blue Triangle Portland cement', 'price': 810.00, 'stock': 800, 'category_id': 1, 'image_url': 'cement_blue.jpeg'},
            {'name': 'Savannah Cement 50kg', 'description': 'Reliable Savannah cement for all construction needs', 'price': 820.00, 'stock': 900, 'category_id': 1, 'image_url': 'cement_savannah.jpeg'},
            {'name': 'Simba Cement 50kg', 'description': 'Strong and durable Simba cement', 'price': 830.00, 'stock': 750, 'category_id': 1, 'image_url': 'cement_simba.jpeg'},
            {'name': 'Mombasa Cement 50kg', 'description': 'Quality Mombasa cement for building', 'price': 800.00, 'stock': 850, 'category_id': 1, 'image_url': 'cement_mombasa.jpeg'},
            {'name': 'Steel Rods 12mm', 'description': 'Construction grade steel rods 12mm diameter', 'price': 450.00, 'stock': 600, 'category_id': 1, 'image_url': 'steel_rods.jpeg'},
        ]

        for prod_data in products_data:
            product = Product(**prod_data)
            db.session.add(product)

        db.session.commit()
        print("DEBUG: Sample data added successfully")
        print(f"DEBUG: Added {len(categories_data)} categories and {len(products_data)} products")

    except Exception as e:
        print(f"DEBUG: Database initialization error: {str(e)}")
        db.session.rollback()


with app.app_context():
    init_db()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

