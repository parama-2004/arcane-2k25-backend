import os
import io
import qrcode
import smtplib
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import random
import string
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from datetime import datetime, timedelta
import requests
from io import BytesIO

# Load environment variables (for local dev; Render uses dashboard env vars)
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SUPABASE_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/public"

# Gmail SMTP
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASS = os.getenv("SMTP_PASS")

app = Flask(__name__)
CORS(app)

# In-memory OTP store (for production use Redis/DB)
otp_store = {}

# --- Helper Functions ---
def generate_team_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def send_otp_email(to_email, subject, body):
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = SMTP_EMAIL
        msg['To'] = to_email

        with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
            server.starttls()
            server.login(SMTP_EMAIL, SMTP_PASS)
            server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

# ----- OTP Endpoints -----
@app.route('/api/send-otp', methods=['POST'])
def send_otp():
    data = request.get_json()
    email = data.get('email')

    if not email:
        return jsonify({"success": False, "message": "Email is required."}), 400

    otp = str(random.randint(100000, 999999))
    expiration_time = datetime.now() + timedelta(minutes=5)
    otp_store[email] = {"otp": otp, "expires": expiration_time}

    subject = "Your Arcane 2K25 Registration OTP"
    body = f"Your one-time password (OTP) is: {otp}\n\nValid for 5 minutes."

    if send_otp_email(email, subject, body):
        return jsonify({"success": True, "message": "OTP sent successfully."}), 200
    else:
        return jsonify({"success": False, "message": "Failed to send OTP."}), 500

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.get_json()
    email = data.get('email')
    otp = data.get('otp')

    if not email or not otp:
        return jsonify({"success": False, "message": "Email and OTP required."}), 400

    stored_data = otp_store.get(email)
    if not stored_data:
        return jsonify({"success": False, "message": "OTP not found/expired."}), 404

    if datetime.now() > stored_data["expires"]:
        del otp_store[email]
        return jsonify({"success": False, "message": "OTP expired."}), 410

    if otp == stored_data["otp"]:
        del otp_store[email]
        return jsonify({"success": True, "message": "Email verified!"}), 200
    else:
        return jsonify({"success": False, "message": "Invalid OTP."}), 401

# ----- Registration Endpoint -----
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    name = data.get("name")
    email = data.get("email")
    phone = data.get("phone")
    college = data.get("college")
    selected_events = data.get("selected_events", [])
    team_name = data.get("teamName") or None
    team_code = data.get("teamCode") or None
    food_pref = data.get("foodPreference") or None
    amount = data.get("total")

    if not team_code and team_name:
        team_code = generate_team_code()

    result = supabase.table("participants").insert({
        "name": name,
        "email": email,
        "phone": phone,
        "college": college,
        "selected_events": selected_events,
        "team_name": team_name,
        "team_code": team_code,
        "food": food_pref,
        "amount": amount,
        "payment_status": "pending"
    }).execute()

    if result.data:
        return jsonify({"status": "success", "message": "Registered. Awaiting payment.", "team_code": team_code})
    else:
        print("Supabase insert error:", result.error)
        return jsonify({"status": "error", "message": "Error saving registration."}), 500

# Helper to convert URL image to ImageReader
def url_to_imagereader(url):
    resp = requests.get(url)
    resp.raise_for_status()
    return ImageReader(BytesIO(resp.content))

# ----- Payment Confirmation & Ticket Generation -----
@app.route("/confirm_payment", methods=["POST"])
def confirm_payment():
    data = request.json
    email = data.get("email")

    participant = supabase.table("participants").select("*").eq("email", email).single().execute()
    if not participant.data:
        return jsonify({"status": "error", "message": "Participant not found."}), 404

    uuid = participant.data.get("id")
    qr_img = qrcode.make(str(uuid))
    qr_img_bytes = io.BytesIO()
    qr_img.save(qr_img_bytes, format="PNG")
    qr_img_bytes.seek(0)

    # Create PDF
    pdf_bytes = io.BytesIO()
    p = canvas.Canvas(pdf_bytes, pagesize=letter)
    width, height = letter

    # Set background to white
    p.setFillColor(colors.white)
    p.rect(0, 0, width, height, fill=1, stroke=0)

   # Add Crescent logo
    try:
        crescent_logo = url_to_imagereader("https://iprrqvqzztbftfxwyyxx.supabase.co/storage/v1/object/public/tickets/cres1.png")
        p.drawImage(crescent_logo, 0, height - 80, width=width, height=80, mask='auto')
    except Exception as e:
        print("⚠️ Crescent logo load failed:", e)

    try:
        wave_logo = url_to_imagereader("https://iprrqvqzztbftfxwyyxx.supabase.co/storage/v1/object/public/tickets/wavee.jpg")  # upload your wave design too
        p.drawImage(wave_logo, 0, 0, width=width, height=100, mask='auto')
    except Exception as e:
        print("⚠️ Wave design load failed:", e)


    # Main title and welcome message
    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(width / 2, height - 150, "Thank you for registering for ARCANE 2K25!")
    p.setFont("Helvetica", 14)
    p.drawCentredString(width / 2, height - 170, "We're excited to have you onboard.")

    # 🧑 Participant Details
    y_pos = height - 220
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Participant Details:")
    p.setFont("Helvetica", 12)
    p.drawString(70, y_pos - 20, f"Name: {participant.data.get('name', 'N/A')}")
    p.drawString(70, y_pos - 40, f"Email Id: {participant.data.get('email', 'N/A')}")
    p.drawString(70, y_pos - 60, f"Phone Number: {participant.data.get('phone', 'N/A')}")
    p.drawString(70, y_pos - 80, f"College Name: {participant.data.get('college', 'N/A')}")

    # 🎭 Event Details
    y_pos -= 120
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Event Details:")
    p.setFont("Helvetica", 12)
    events_list = [e['name'] for e in participant.data.get('selected_events', [])]
    p.drawString(70, y_pos - 20, f"Events: {', '.join(events_list) or 'N/A'}")
    p.drawString(70, y_pos - 40, f"Team Name: {participant.data.get('team_name', 'N/A')}")
    p.drawString(70, y_pos - 60, f"Team Code: {participant.data.get('team_code', 'N/A')}")
    p.drawString(70, y_pos - 80, f"Food Preference: {participant.data.get('food', 'N/A')}")

    # 💰 Payment Details
    y_pos -= 120
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Payment:")
    p.setFont("Helvetica", 12)
    p.drawString(70, y_pos - 20, f"Amount Paid: ₹{participant.data.get('amount', 'N/A')}")

    # Date and Venue (static)
    y_pos -= 60
    p.setFont("Times-Roman", 12)
    p.drawString(70, y_pos, "Date: 16th October 2025")
    p.drawString(70, y_pos - 20, "Venue: B. S. Abdur Rahman Crescent Institute Of Science And Technology")
    
    # Contact information
    y_pos -= 60
    p.setFont("Helvetica-Bold", 10)
    p.drawCentredString(width / 2, y_pos, "If you have any queries or need assistance, feel free to")
    p.drawCentredString(width / 2, y_pos - 15, "reach us at: arcane2k25@gmail.com")


    # Place QR Code (bottom-left) and social media link
    p.drawImage(ImageReader(qr_img_bytes), 70, 70, width=120, height=120)
    
    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 12)
    p.drawCentredString(width / 2, 40, "@arcane2k25")
    
    p.save()
    pdf_bytes.seek(0) # Rewind the stream after saving to PDF


    pdf_file_path = f"tickets/{email}_ticket.pdf"
    try:
        supabase.storage.from_("tickets").upload(pdf_file_path, pdf_bytes.getvalue(), {"content-type": "application/pdf"})
        supabase_storage_url = f"{SUPABASE_STORAGE_URL}/tickets/{email}_ticket.pdf"

        supabase.table("participants").update({
            "payment_status": "paid",
            "qr_path": supabase_storage_url
        }).eq("email", email).execute()

        pdf_bytes.seek(0)
        send_ticket_email(
            email,
            "Event Registration Confirmed 🎉",
            f"Hello {participant.data['name']},<br>Your registration is confirmed. Attached is your PDF ticket.",
            pdf_bytes
        )
        return jsonify({"status": "success", "message": "Payment confirmed & ticket sent."})
    except Exception as e:
        print("Supabase upload error:", e)
        return jsonify({"status": "error", "message": "Failed to upload ticket."}), 500

# ----- Helper: Send Email with attachment -----
def send_ticket_email(to_email, subject, body, attachment_bytes):
    msg = MIMEMultipart()
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "html"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(attachment_bytes.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment; filename=ticket.pdf")
    msg.attach(part)

    try:
        server = smtplib.SMTP("smtp-relay.brevo.com", 587)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASS)
        server.sendmail(SMTP_EMAIL, to_email, msg.as_string())
        server.quit()
        print("✅ Mail sent to", to_email)
        return True
    except Exception as e:
        print("❌ Mail error:", e)
        return False

# ----- Fetch Participants -----
@app.route("/participants", methods=["GET"])
def get_participants():
    search = request.args.get("search")
    query = supabase.table("participants").select("*")
    if search:
        query = query.ilike("name", f"%{search}%")
    data = query.execute()
    return jsonify({"data": data.data})

