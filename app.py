import os
import io
import qrcode
import base64  # <-- Import base64 for attachments
import random
import string
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from datetime import datetime, timedelta
import requests
from io import BytesIO
# --- Brevo API Imports ---
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException

# Load environment variables
load_dotenv()

# --- Service Configurations ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SUPABASE_STORAGE_URL = f"{SUPABASE_URL}/storage/v1/object/public"

SENDER_EMAIL = os.getenv("SENDER_EMAIL") # Use a generic name for the from email
SENDER_NAME = "Arcane 2K25"

# --- Brevo (Sendinblue) API Configuration ---
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
if not BREVO_API_KEY:
    raise ValueError("BREVO_API_KEY environment variable not set!")

configuration = sib_api_v3_sdk.Configuration()
configuration.api_key['api-key'] = BREVO_API_KEY
# Create an API client instance
api_client = sib_api_v3_sdk.ApiClient(configuration)
# Create a transactional emails API instance
transactional_api_instance = sib_api_v3_sdk.TransactionalEmailsApi(api_client)

app = Flask(__name__)
CORS(app)

# In-memory OTP store (for production use Redis/DB)
otp_store = {}

# --- Helper Functions ---
def generate_team_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

def send_otp_email(to_email, subject, body):
    """Sends a plain-text email using the Brevo Transactional API."""
    sender = sib_api_v3_sdk.SendSmtpEmailSender(name=SENDER_NAME, email=SENDER_EMAIL)
    to = [sib_api_v3_sdk.SendSmtpEmailTo(email=to_email)]
    
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        sender=sender,
        to=to,
        subject=subject,
        text_content=body
    )
    
    try:
        api_response = transactional_api_instance.send_transac_email(send_smtp_email)
        print(f"✅ OTP Email sent successfully to {to_email}. Response: {api_response}")
        return True
    except ApiException as e:
        print(f"❌ Failed to send OTP email via Brevo API: {e}")
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
    body = f"Your one-time password (OTP) is: {otp}\n\nThis code is valid for 5 minutes."

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
    if not stored_data or datetime.now() > stored_data["expires"]:
        if email in otp_store: del otp_store[email]
        return jsonify({"success": False, "message": "OTP not found or has expired."}), 410
    
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

    try:
        result = supabase.table("participants").insert({
            "name": name, "email": email, "phone": phone, "college": college,
            "selected_events": selected_events, "team_name": team_name,
            "team_code": team_code, "food": food_pref, "amount": amount,
            "payment_status": "pending"
        }).execute()

        if result.data:
            return jsonify({"status": "success", "message": "Registered. Awaiting payment.", "team_code": team_code})
        else:
            print("Supabase insert error:", result.error)
            return jsonify({"status": "error", "message": "Error saving registration."}), 500
    except Exception as e:
        print("Registration error:", e)
        return jsonify({"status": "error", "message": "An unexpected error occurred during registration."}), 500
        
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

    participant_req = supabase.table("participants").select("*").eq("email", email).single().execute()
    if not participant_req.data:
        return jsonify({"status": "error", "message": "Participant not found."}), 404
    
    participant = participant_req.data
    uuid = participant.get("id")
    qr_img = qrcode.make(str(uuid))
    qr_img_bytes = io.BytesIO()
    qr_img.save(qr_img_bytes, format="PNG")
    qr_img_bytes.seek(0)

    # Create PDF
    pdf_bytes = io.BytesIO()
    p = canvas.Canvas(pdf_bytes, pagesize=letter)
    width, height = letter

    # White background
    p.setFillColor(colors.white)
    p.rect(0, 0, width, height, fill=1, stroke=0)

    try:
        crescent_logo = url_to_imagereader("https://iprrqvqzztbftfxwyyxx.supabase.co/storage/v1/object/public/tickets/cres1.png")
        p.drawImage(crescent_logo, 0, height - 80, width=width, height=80, mask='auto')
    except Exception as e:
        print("⚠️ Crescent logo load failed:", e)

    try:
        wave_logo = url_to_imagereader("https://iprrqvqzztbftfxwyyxx.supabase.co/storage/v1/object/public/tickets/wavee.jpg")
        p.drawImage(wave_logo, 0, 0, width=width, height=100, mask='auto')
    except Exception as e:
        print("⚠️ Wave design load failed:", e)

    p.setFillColor(colors.black)
    p.setFont("Helvetica-Bold", 18)
    p.drawCentredString(width / 2, height - 150, "Thank you for registering for ARCANE 2K25!")
    p.setFont("Helvetica", 14)
    p.drawCentredString(width / 2, height - 170, "We're excited to have you onboard.")

    # Participant Details
    y_pos = height - 220
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Participant Details:")
    p.setFont("Helvetica", 12)
    p.drawString(70, y_pos - 20, f"Name: {participant.get('name', 'N/A')}")
    p.drawString(70, y_pos - 40, f"Email Id: {participant.get('email', 'N/A')}")
    p.drawString(70, y_pos - 60, f"Phone Number: {participant.get('phone', 'N/A')}")
    p.drawString(70, y_pos - 80, f"College Name: {participant.get('college', 'N/A')}")

    # Events
    y_pos -= 120
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Event Details:")
    p.setFont("Helvetica", 12)
    events_list = [e['name'] for e in participant.get('selected_events', [])]
    p.drawString(70, y_pos - 20, f"Events: {', '.join(events_list) or 'N/A'}")
    p.drawString(70, y_pos - 40, f"Team Name: {participant.get('team_name', 'N/A')}")
    p.drawString(70, y_pos - 60, f"Team Code: {participant.get('team_code', 'N/A')}")
    p.drawString(70, y_pos - 80, f"Food Preference: {participant.get('food', 'N/A')}")

    # Payment
    y_pos -= 120
    p.setFont("Helvetica-Bold", 12)
    p.drawString(70, y_pos, "Payment:")
    p.setFont("Helvetica", 12)
    p.drawString(70, y_pos - 20, f"Amount Paid: ₹{participant.get('amount', 'N/A')}")

    # Date + Venue
    y_pos -= 60
    p.setFont("Times-Roman", 12)
    p.drawString(70, y_pos, "Date: 16th October 2025")
    p.drawString(70, y_pos - 20, "Venue: B.S. Abdur Rahman Crescent Institute of Science & Technology")

    # Contact Info
    y_pos -= 60
    p.setFont("Helvetica-Bold", 10)
    p.drawCentredString(width / 2, y_pos, "If you have any queries, contact us at:")
    p.drawCentredString(width / 2, y_pos - 15, "arcane2k25@gmail.com")

    # QR Code
    p.drawImage(ImageReader(qr_img_bytes), 70, 70, width=120, height=120)
    p.setFont("Helvetica-Bold", 12)
    p.drawCentredString(width / 2, 40, "@arcane2k25")

    p.save()
    pdf_bytes.seek(0)

    # Upload PDF to Supabase
    pdf_file_path = f"tickets/{email}_ticket.pdf"
    try:
        supabase.storage.from_("tickets").upload(pdf_file_path, pdf_bytes.getvalue(), {"content-type": "application/pdf"})
        supabase_storage_url = f"{SUPABASE_STORAGE_URL}/tickets/{email}_ticket.pdf"

        supabase.table("participants").update({
            "payment_status": "paid",
            "qr_path": supabase_storage_url
        }).eq("email", email).execute()

        # Send the ticket email via Brevo
        pdf_bytes.seek(0) # Rewind again for the email attachment
        send_ticket_email(
            email,
            "Your Arcane 2K25 Ticket is Here! 🎉",
            f"Hello {participant['name']},<br><br>Your registration is confirmed! We are thrilled to have you at Arcane 2K25. Your personalized ticket is attached to this email.<br><br>See you there!",
            pdf_bytes
        )
        return jsonify({"status": "success", "message": "Payment confirmed & ticket sent."})
    except Exception as e:
        print("Ticket generation/upload error:", e)
        return jsonify({"status": "error", "message": "Failed to generate or upload ticket."}), 500

# ----- Helper: Send Email with attachment -----
def send_ticket_email(to_email, subject, body, attachment_bytes):
    """Sends an email with a PDF attachment using the Brevo Transactional API."""
    sender = sib_api_v3_sdk.SendSmtpEmailSender(name=SENDER_NAME, email=SENDER_EMAIL)
    to = [sib_api_v3_sdk.SendSmtpEmailTo(email=to_email)]
    
    # Encode the PDF attachment in base64
    encoded_content = base64.b64encode(attachment_bytes.read()).decode('utf-8')
    
    # Create the attachment object
    attachment = sib_api_v3_sdk.SendSmtpEmailAttachment(
        name="Arcane-2K25-Ticket.pdf",
        content=encoded_content
    )

    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        sender=sender,
        to=to,
        subject=subject,
        html_content=body,
        attachment=[attachment] # Attach the file here
    )

    try:
        api_response = transactional_api_instance.send_transac_email(send_smtp_email)
        print(f"✅ Ticket Email sent successfully to {to_email}. Response: {api_response}")
        return True
    except ApiException as e:
        print(f"❌ Failed to send ticket email via Brevo API: {e}")
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

@app.route("/health")
def health():
    return "OK", 200
