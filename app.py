import os
import base64
import psycopg2
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash, check_password_hash
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit import QuantumCircuit, transpile

app = Flask(__name__)
# Render Environment Variables'dan çekilir
app.secret_key = os.getenv("SECRET_KEY", "quantum_stealth_default_77")

# --- DATABASE BAĞLANTISI ---
def get_db_connection():
    # Render'daki Internal Database URL'i kullanır
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    return conn

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # 1. Kullanıcılar: Şifreler hash'li (encrypted) saklanır
    cur.execute('CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password_hash TEXT)')
    
    # 2. Mesajlar: İçerik cryptli, timestamp benzersiz aktivite numarası görevi görür
    cur.execute('''CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        sender TEXT,
        receiver TEXT,
        encrypted_content TEXT,
        q_key TEXT,
        timestamp TEXT
    )''')
    conn.commit()
    cur.close()
    conn.close()

# Uygulama her başladığında tabloları kontrol eder
try:
    init_db()
except Exception as e:
    print(f"DB Bağlantı Hatası: {e}")

# --- KUANTUM MOTORU ---
def get_q_key():
    try:
        # Render panelinde IBM_KEY tanımlıysa kuantum anahtar üretir
        service = QiskitRuntimeService(channel="ibm_quantum_platform", token=os.getenv("IBM_KEY"))
        backend = service.least_busy(operational=True, simulator=False)
        qc = QuantumCircuit(1); qc.h(0); qc.measure_all()
        t_qc = transpile(qc, backend=backend)
        sampler = SamplerV2(backend)
        result = sampler.run([t_qc], shots=256).result()[0].data.meas.get_counts()
        bit_string = ("".join(list(result.keys())) * 256)[:256]
        return base64.urlsafe_b64encode(int(bit_string, 2).to_bytes(32, 'big')).decode()
    except Exception as e:
        # Hata durumunda (internet/limit vb.) güvenli yerel anahtar üretir
        return Fernet.generate_key().decode()

# --- ROUTERLAR ---
@app.route('/')
def index():
    if 'user' in session:
        return render_template('index.html', user=session['user'])
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page():
    return render_template('auth.html')

@app.route('/auth', methods=['POST'])
def auth():
    data = request.json
    username = data.get('user')
    password = data.get('pass')
    
    conn = get_db_connection()
    cur = conn.cursor()

    if data['type'] == 'register':
        pw_hash = generate_password_hash(password)
        try:
            cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s)", (username, pw_hash))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({"status": "ok"})
        except:
            return jsonify({"status": "Bu kullanıcı zaten mevcut"}), 400

    # Giriş teyit: Kullanıcıyı bul, şifreyi doğrula
    cur.execute("SELECT password_hash FROM users WHERE username = %s", (username,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row and check_password_hash(row[0], password):
        session['user'] = username
        return jsonify({"status": "ok"})
    
    return jsonify({"status": "Kullanıcı adı veya şifre hatalı"}), 401

@app.route('/send', methods=['POST'])
def send():
    if 'user' not in session: return jsonify({"status": "fail"}), 401
    data = request.json
    
    # Zaman Damgası (Senin istediğin zamanlama takibi)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    q_key = get_q_key()
    f = Fernet(q_key.encode())
    encrypted_msg = f.encrypt(data['text'].encode()).decode()
    
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''INSERT INTO messages (sender, receiver, encrypted_content, q_key, timestamp) 
                   VALUES (%s, %s, %s, %s, %s)''', 
                (session['user'], data['to'], encrypted_msg, q_key, now))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"status": "ok", "time": now})

@app.route('/get_chats')
def get_chats():
    if 'user' not in session: return jsonify({"chats": []})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT sender FROM messages WHERE receiver = %s", (session['user'],))
    senders = [r[0] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return jsonify({"chats": senders})

@app.route('/read_chat/<sender>')
def read_chat(sender):
    if 'user' not in session: return jsonify({"messages": []})
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Belirtilen kişiden gelen zaman damgalı mesajları çek
    cur.execute("SELECT id, encrypted_content, q_key, timestamp FROM messages WHERE sender = %s AND receiver = %s", 
                (sender, session['user']))
    rows = cur.fetchall()
    
    decrypted_messages = []
    ids_to_delete = []

    for r in rows:
        msg_id, content, key, tstamp = r
        try:
            f = Fernet(key.encode())
            text = f.decrypt(content.encode()).decode()
            decrypted_messages.append({"text": text, "time": tstamp})
            ids_to_delete.append(msg_id)
        except:
            continue

    # İMHA PROTOKOLÜ: Okunan mesajlar veritabanından kalıcı olarak silinir
    if ids_to_delete:
        cur.execute("DELETE FROM messages WHERE id IN %s", (tuple(ids_to_delete),))
        conn.commit()

    cur.close()
    conn.close()
    return jsonify({"messages": decrypted_messages})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

if __name__ == '__main__':
    # Render'ın dinamik port ataması için host ve port ayarı
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
