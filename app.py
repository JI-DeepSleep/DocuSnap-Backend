import os
from flask import Flask, request, jsonify, render_template
import requests
import sqlite3
from queue import Queue
import threading
import time
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from io import BytesIO
import base64
from zhipuai import ZhipuAI
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from concurrent.futures import ThreadPoolExecutor, as_completed
from priv_sets import (
    LLM_API_KEY,
    EXPIRE_MINUTES,
    LLM_MODEL,
    RSA_PRIVATE_KEY,
    OCR_API_PREFIX,
    RSA_PUBLIC_KEY,
    MAX_OCR_CONCURRENCY,
    MAX_REQUEST_CONCURRENCY,
)

from prompts import DOC_PROMPT, FORM_PROMPT, FILL_PROMPT

app = Flask(__name__)
task_queue = Queue()
client = ZhipuAI(api_key=LLM_API_KEY)
ocr_semaphore = threading.Semaphore(MAX_OCR_CONCURRENCY)


def init_db():
    conn = sqlite3.connect("tasks.db")
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            client_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            error_detail TEXT,
            created_at TEXT,
            last_accessed TEXT,
            PRIMARY KEY (client_id, sha256, type)
        )
    """
    )
    c.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_client_sha ON tasks (client_id, sha256)
    """
    )
    conn.commit()
    conn.close()


init_db()


def get_current_utc_time():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


RSA_PRIVATE_KEY_OBJ = serialization.load_pem_private_key(
    RSA_PRIVATE_KEY.encode(), password=None, backend=default_backend()
)


def rsa_decrypt_key(encrypted_key_b64):
    """Decrypt AES key using RSA"""
    try:
        ciphertext = base64.b64decode(encrypted_key_b64)
        return RSA_PRIVATE_KEY_OBJ.decrypt(
            ciphertext,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as e:
        app.logger.error(f"RSA key decryption failed: {str(e)}")
        raise


def aes_encrypt(data, key):
    if isinstance(key, bytes):
        key = key
    else:
        key = key.encode("utf-8")

    iv = os.urandom(16)
    if isinstance(data, str):
        data = data.encode("utf-8")

    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data) + padder.finalize()
    cipher = Cipher(
        algorithms.AES(key),
        modes.CBC(iv),
        backend=default_backend(),
    )
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode()


def aes_decrypt(encrypted_data, key):
    if isinstance(key, bytes):
        key = key
    else:
        key = key.encode("utf-8")

    encrypted_bytes = base64.b64decode(encrypted_data)
    iv = encrypted_bytes[:16]
    ciphertext = encrypted_bytes[16:]
    cipher = Cipher(
        algorithms.AES(key),
        modes.CBC(iv),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded_data = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded_data) + unpadder.finalize()


def perform_ocr_base64(image_base64):
    with ocr_semaphore:
        image_bytes = base64.b64decode(image_base64)
        image_file = BytesIO(image_bytes)
        r = requests.post(
            f"{OCR_API_PREFIX}/ocr",
            files={"image": ("", image_file, "image/png")},
            timeout=60,
        )
        r.raise_for_status()
        out = r.json()["results"]
        rst = " ".join([item["text"] for item in out])
        print("OCR results:\n", rst)
        return rst


def expand_json(data, parent_key="", separator="."):
    items = {}
    if isinstance(data, dict):
        for key, value in data.items():
            new_key = f"{parent_key}{separator}{key}" if parent_key else key
            items.update(expand_json(value, new_key, separator))
    elif isinstance(data, list):
        for index, value in enumerate(data, 1):
            new_key = (
                f"{parent_key}{separator}{index}" if parent_key else str(index)
            )
            items.update(expand_json(value, new_key, separator))
    else:
        items[parent_key] = data
    return items


def call_llm(prompt, type):
    response = client.chat.asyncCompletions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    )
    task_id = response.id
    while True:
        result_response = (
            client.chat.asyncCompletions.retrieve_completion_result(id=task_id)
        )
        if result_response.task_status == "SUCCESS":
            rst = remove_think_tags(result_response.choices[0].message.content)
            print("LLM response:\n", rst)
            if type != "fill":
                rst = json.loads(rst)
                rst["kv"] = expand_json(rst["kv"])
                rst = json.dumps(rst)
            return rst
        elif result_response.task_status == "FAILED":
            raise Exception("LLM processing failed")
        time.sleep(1)


def cleanup_old_entries():
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=EXPIRE_MINUTES)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect("tasks.db")
    c = conn.cursor()
    c.execute(
        "DELETE FROM tasks WHERE last_accessed < ? ",
        (cutoff_str,),
    )
    conn.commit()
    conn.close()


def remove_think_tags(input_string):
    pattern = r"<think\s*>.*?</think\s*>"
    return re.sub(pattern, "", input_string, flags=re.DOTALL)


def construct_prompt_doc(doc_text, file_lib):
    print("LLM content\n", doc_text)
    return (
        DOC_PROMPT
        + "  <ocr_content>  "
        + doc_text
        + "</ocr_content>  <file_lib>"
        + json.dumps(file_lib)
        + "</file_lib>"
    )


def construct_prompt_form(form_text, file_lib):
    print("LLM content\n", form_text)
    return (
        FORM_PROMPT
        + "  <ocr_content>  "
        + form_text
        + "</ocr_content>  <file_lib>"
        + json.dumps(file_lib)
        + "</file_lib>"
    )


def construct_prompt_fill(form_obj, file_lib):
    return (
        FILL_PROMPT
        + "  <form>  "
        + json.dumps(form_obj)
        + "</form>  <file_lib>"
        + json.dumps(file_lib)
        + "</file_lib>"
    )


def images_to_text(images):
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(perform_ocr_base64, img): i
            for i, img in enumerate(images)
        }
        texts = [None] * len(images)
        for future in as_completed(futures):
            idx = futures[future]
            texts[idx] = future.result()
    combined_text = ""
    for i, text in enumerate(texts):
        combined_text += f"\n----page {i+1}----\n{text}"
    return combined_text


def write_error_to_cache(task, error_detail):
    with sqlite3.connect("tasks.db") as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE tasks
            SET status='error',
            error_detail=?,
            last_accessed=?
            WHERE client_id=?
            AND sha256=?
            AND type=?
            """,
            (
                error_detail,
                get_current_utc_time(),
                task["client_id"],
                task["sha256"],
                task["type"],
            ),
        )
        conn.commit()


def write_result_to_cache(task, raw_result):

    aes_key = task["aes_key"]

    encrypted_result = aes_encrypt(raw_result, aes_key)

    with sqlite3.connect("tasks.db") as conn:
        c = conn.cursor()
        c.execute(
            """
            UPDATE tasks
            SET status='completed',
            result=?,
            last_accessed=?
            WHERE client_id=?
            AND sha256=?
            AND type=?
            """,
            (
                encrypted_result,
                get_current_utc_time(),
                task["client_id"],
                task["sha256"],
                task["type"],
            ),
        )
        conn.commit()


def process_task(task):
    prompt = None
    if task["type"] == "fill":
        prompt = construct_prompt_fill(
            task["content"]["to_process"], task["content"]["file_lib"]
        )
    else:
        try:
            text = images_to_text(task["content"]["to_process"])
        except Exception as e:
            print(f"OCR fails, {str(e)}")
            write_error_to_cache(task, "OCR fails")
            return
        if task["type"] == "doc":
            prompt = construct_prompt_doc(text, task["content"]["file_lib"])
        else:
            prompt = construct_prompt_form(text, task["content"]["file_lib"])
    result = None
    try:
        result = call_llm(prompt, task["type"])
    except Exception as e:
        print(f"calling llm fails, {str(e)}")
        write_error_to_cache(task, "calling llm fails")
        return
    write_result_to_cache(task, result)


def worker_process():
    while True:
        cleanup_old_entries()
        if not task_queue.empty():
            task = task_queue.get()
            process_task(task)
        else:
            time.sleep(MAX_REQUEST_CONCURRENCY)


for _ in range(MAX_REQUEST_CONCURRENCY):
    threading.Thread(target=worker_process, daemon=True).start()


def construct_error_result(error_detail):
    return (jsonify({"status": "error", "error_detail": error_detail}), 400)


def construct_task_result(task):
    status, error_message, result = task
    if status == "error":
        return construct_error_result(error_message)
    if status == "processing":
        return jsonify({"status": "processing"}), 202
    return jsonify({"status": "completed", "result": result}), 200


@app.route("/process", methods=["POST"])
def unified_process():
    data = request.get_json()
    required = ["client_id", "type", "SHA256", "has_content"]
    for field in required:
        if field not in data:
            return construct_error_result(f"Missing required field: {field}")

    client_id = data["client_id"]
    task_type = data["type"]
    sha256 = data["SHA256"]
    has_content = data["has_content"]

    if task_type not in ["doc", "form", "fill"]:
        return construct_error_result(
            "Invalid type. Must be 'doc', 'form', or 'fill'"
        )

    if has_content and "content" not in data:
        return construct_error_result(
            "content required when has_content is true"
        )

    current_time = get_current_utc_time()

    with sqlite3.connect("tasks.db") as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT status,error_detail, result 
            FROM tasks 
            WHERE client_id = ? 
            AND sha256 = ? 
            AND type = ?
            """,
            (client_id, sha256, task_type),
        )
        task = c.fetchone()

        if task:
            c.execute(
                """
                UPDATE tasks 
                SET last_accessed = ? 
                WHERE client_id = ? 
                AND sha256 = ? 
                AND type = ?
                """,
                (current_time, client_id, sha256, task_type),
            )
            conn.commit()
            return construct_task_result(task)

        if not has_content:
            return construct_error_result("task not found")

        try:
            computed_sha256 = hashlib.sha256(
                data["content"].encode()
            ).hexdigest()
            if computed_sha256 != sha256:
                return construct_error_result("SHA256 mismatch")
        except Exception as e:
            print(f"SHA256 verification failed: {str(e)}")
            return construct_error_result("SHA256 verification failed")

        print("SHA256 verified, start RSA decryption")
        try:
            aes_key_bytes = rsa_decrypt_key(data["aes_key"])
        except Exception as e:
            print(f"RSA decryption failed: {str(e)}")
            return construct_error_result("RSA decryption failed")

        print("RSA complete, start AES decryption")
        try:
            decrypted_content = aes_decrypt(data["content"], aes_key_bytes)
        except Exception as e:
            print(f"AES decryption failed: {str(e)}")
            return construct_error_result("AES decryption failed")

        print("AES complete, parse JSON")
        try:
            inner_payload = json.loads(decrypted_content)
        except Exception as e:
            print(f"Parsing content into json failed: {str(e)}")
            return construct_error_result("Parsing content into json failed")

        c.execute(
            """
            INSERT INTO tasks (
                client_id, 
                sha256, 
                type, 
                status,
                created_at, 
                last_accessed
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                sha256,
                task_type,
                "processing",
                current_time,
                current_time,
            ),
        )
        conn.commit()

        task_queue.put(
            {
                "client_id": client_id,
                "sha256": sha256,
                "type": task_type,
                "content": inner_payload,
                "aes_key": aes_key_bytes,
            }
        )
        return jsonify({"status": "processing"}), 202


@app.route("/clear", methods=["POST"])
def clear_cache():
    data = request.get_json()
    if "client_id" not in data:
        return jsonify({"error": "Missing client_id"}), 400

    client_id = data["client_id"]
    sha256 = data.get("SHA256")
    task_type = data.get("type")

    with sqlite3.connect("tasks.db") as conn:
        c = conn.cursor()
        if sha256 and task_type:
            c.execute(
                """
                DELETE FROM tasks 
                WHERE client_id = ? 
                  AND sha256 = ?
                  AND type = ?
                """,
                (client_id, sha256, task_type),
            )
        else:
            c.execute(
                """
                DELETE FROM tasks 
                WHERE client_id = ?
                """,
                (client_id,),
            )
        conn.commit()
        return jsonify({"status": "ok"}), 200


@app.route("/check_status")
def check_status():
    return jsonify({"server_status": "ok"}), 200


@app.route("/")
def index():
    clean_key = RSA_PUBLIC_KEY.strip().replace("\n", "\\n")
    with open("static/mockup_file_lib.json", "r", encoding="utf-8") as f:
        mockup_file_lib = json.load(f)
    return render_template(
        "ocr.html", public_key=clean_key, mockup_file_lib=mockup_file_lib
    )


if __name__ == "__main__":
    app.run(debug=True)
