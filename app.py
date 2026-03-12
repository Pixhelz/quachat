import os, base64, secrets
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from qiskit_ibm_runtime import QiskitRuntimeService, SamplerV2
from qiskit import QuantumCircuit, transpile
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# --- BELLEK ÜSTÜ VERİTABANI (Sözlük Yapısı) ---
# Program kapandığında veriler uçar, tam istediğin güvenlik!
DB = {
    "users": {},    # {"eren": "sifre123"}
    "messages": []  # [{"sender": "a", "receiver": "b", "content": "...", "q_key": "..."}]
}

# --- KUANTUM MOTORU ---
def get_q_key():
    try:
        # İnternet hatası veya IBM meşguliyeti durumunda sistemin çökmemesi için try-except şart
        service = QiskitRuntimeService(channel="ibm_quantum_platform", token="FL3f9A65VPZxOusXSIIPZA8fXGNUNvXvHdk3TVJRvcuC")
        backend = service.least_busy(operational=True, simulator=False)
        qc = QuantumCircuit(1); qc.h(0); qc.measure_all()
        t_qc = transpile(qc, backend=backend)
        sampler = SamplerV2(backend)
        result = sampler.run([t_qc], shots=256).result()[0].data.meas.get_counts()
        bit_string = ("".join(list(result.keys())) * 256)[:256]
        return base64.urlsafe_b64encode(int(bit_string, 2).to_bytes(32, 'big')).decode()
    except Exception as e:
        print(f"Kuantum Hatası: {e}. Yerel güvenli anahtar üretiliyor...")
        return Fernet.generate_key().decode()

# --- ROUTERLAR ---
@app.route('/')
def index():
    if 'user' in session: return render_template('index.html', user=session['user'])
    return redirect(url_for('login_page'))

@app.route('/login')
def login_page(): return render_template('auth.html')

@app.route('/auth', methods=['POST'])
def auth():
    data = request.json
    username = data['user']
    password = data['pass']
    
    if data['type'] == 'register':
        if username in DB["users"]:
            return jsonify({"status": "Bu kullanıcı zaten var"}), 400
        DB["users"][username] = password
        return jsonify({"status": "ok"})
    
    if username in DB["users"] and DB["users"][username] == password:
        session['user'] = username
        return jsonify({"status": "ok"})
    
    return jsonify({"status": "Hatalı giriş"}), 401

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login_page'))

@app.route('/send', methods=['POST'])
def send():
    if 'user' not in session: return jsonify({"status": "fail"}), 401
    data = request.json
    q_key = get_q_key()
    f = Fernet(q_key.encode())
    encrypted = f.encrypt(data['text'].encode()).decode()
    
    DB["messages"].append({
        "sender": session['user'],
        "receiver": data['to'],
        "content": encrypted,
        "q_key": q_key
    })
    return jsonify({"status": "ok"})

@app.route('/get_chats')
def get_chats():
    if 'user' not in session: return jsonify({"chats": []})
    # Sadece aktif kullanıcıya gelen mesajların gönderenlerini bul
    senders = list(set([m['sender'] for m in DB["messages"] if m['receiver'] == session['user']]))
    return jsonify({"chats": senders})

@app.route('/read_chat/<sender>')
def read_chat(sender):
    if 'user' not in session: return jsonify({"messages": []})
    
    received_msgs = []
    updated_db_messages = []
    
    for m in DB["messages"]:
        if m['sender'] == sender and m['receiver'] == session['user']:
            f = Fernet(m['q_key'].encode())
            received_msgs.append(f.decrypt(m['content'].encode()).decode())
            # Bu mesaj okundu, listeden çıkarılacak (updated_db_messages'a eklemiyoruz)
        else:
            updated_db_messages.append(m)
            
    DB["messages"] = updated_db_messages # Okunanları sildik
    return jsonify({"messages": received_msgs})

if __name__ == '__main__':
    # Render'ın atayacağı portu kullanabilmesi için
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)