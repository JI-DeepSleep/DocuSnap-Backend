# Quick Start Guide

This page provides a quick start guide for the DocuSnap-Backend system, helping developers and operations personnel quickly deploy, configure, and use the system.

## System Requirements

Before you begin, please ensure your environment meets the following requirements:

### Minimum Requirements

- **Operating System**: Linux (Ubuntu 18.04+/CentOS 7+) or macOS 10.15+
- **Python**: Python 3.8 or higher
- **Storage**: At least 1GB of available space
- **Memory**: At least 4GB RAM
- **Network**: Internet connection (for installing dependencies and calling LLM API)

### Recommended Configuration

- **Operating System**: Ubuntu 20.04 LTS or higher
- **Python**: Python 3.9 or higher
- **Storage**: 10GB+ SSD storage
- **Memory**: 8GB+ RAM
- **CPU**: 4+ cores
- **Network**: High-speed internet connection

## Quick Installation

### 1. Clone the Repository

First, clone the DocuSnap-Backend repository to your local machine:

```bash
git clone https://github.com/JI-DeepSleep/DocuSnap-Backend.git
cd DocuSnap-Backend
```

### 2. Create a Virtual Environment

Create and activate a Python virtual environment:

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# Linux/macOS
source venv/bin/activate
# Windows
venv\Scripts\activate
```

### 3. Install Dependencies

Install the required Python dependencies:

```bash
pip install -r requirements.txt
```

### 4. Generate Key Pairs

Use the provided script to generate RSA key pairs:

```bash
# Add execution permission to the script
chmod +x genKeyPairs.sh

# Run the script to generate key pairs
./genKeyPairs.sh
```

This will generate `private_key.pem` and `public_key.pem` files in the current directory.

### 5. Configure the System

Create a configuration file:

```bash
# Copy the sample configuration file
cp priv_sets.py.sample priv_sets.py

# Edit the configuration file
nano priv_sets.py  # or use your preferred editor
```

In the configuration file, you need to set at least the following parameters:

- **Zhipu AI API Key**: For calling the LLM service
- **OCR Service URL**: Address pointing to the OCR service
- **Key File Paths**: Paths to the RSA private and public keys

### 6. Start the OCR Service

DocuSnap-Backend requires an external OCR service. You can use CnOCR or another compatible OCR service.

If you're using CnOCR, you can start the service with the following steps:

```bash
# Install CnOCR
pip install cnocr

# Create a simple OCR service (example)
cat > ocr_service.py << 'EOF'
from flask import Flask, request, jsonify
from cnocr import CnOcr
import io
from PIL import Image

app = Flask(__name__)
ocr = CnOcr()

@app.route('/process', methods=['POST'])
def process():
    if 'image' not in request.files:
        return jsonify({"error": "No image provided"}), 400
    
    image_file = request.files['image']
    img = Image.open(io.BytesIO(image_file.read()))
    result = ocr.ocr(img)
    
    text = ''.join([''.join(line) for line in result])
    return jsonify({"text": text})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
EOF

# Start the OCR service
python ocr_service.py &
```

### 7. Start the Application

Start the DocuSnap-Backend application:

```bash
# Development environment
flask run --host=0.0.0.0 --port=5000

# Production environment
gunicorn --workers=4 --bind=0.0.0.0:8000 app:app
```

Now, DocuSnap-Backend should be running and listening on the specified port.

## Basic Usage

### 1. API Endpoints

DocuSnap-Backend provides the following main API endpoints:

- **`/api/process_document`**: Process document images
- **`/api/process_form`**: Process form images
- **`/api/process_form_filling`**: Process form auto-filling
- **`/api/task_status`**: Query task status and results

All API requests and responses need to use end-to-end encryption.

### 2. Client Example

Below is an example of using Python to call the API:

```python
import requests
import json
import base64
import hashlib
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad

# Load public key
with open('public_key.pem', 'rb') as f:
    public_key = RSA.import_key(f.read())

# Generate AES key
aes_key = get_random_bytes(16)

# Encrypt AES key with RSA public key
cipher_rsa = PKCS1_OAEP.new(public_key)
encrypted_aes_key = base64.b64encode(cipher_rsa.encrypt(aes_key)).decode('utf-8')

# Prepare request data
data = {
    "images": [
        # Base64 encoded image data
        "base64_image_data_here"
    ]
}

# Convert data to JSON string
data_json = json.dumps(data)

# Calculate SHA256 hash of data as signature
signature = hashlib.sha256(data_json.encode('utf-8')).hexdigest()

# Encrypt data using AES
iv = get_random_bytes(16)
cipher_aes = AES.new(aes_key, AES.MODE_CBC, iv)
padded_data = pad(data_json.encode('utf-8'), AES.block_size)
encrypted_data = base64.b64encode(iv + cipher_aes.encrypt(padded_data)).decode('utf-8')

# Build request
request_data = {
    "encrypted_data": encrypted_data,
    "encrypted_key": encrypted_aes_key,
    "signature": signature
}

# Send request
response = requests.post(
    "http://localhost:8000/api/process_document",
    json=request_data
)

# Parse response
if response.status_code == 202:
    # Asynchronous task created successfully
    response_data = response.json()
    
    # Decrypt response
    encrypted_response = response_data["encrypted_data"]
    encrypted_response_bytes = base64.b64decode(encrypted_response)
    iv = encrypted_response_bytes[:16]
    ciphertext = encrypted_response_bytes[16:]
    
    cipher_aes = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted_data = unpad(cipher_aes.decrypt(ciphertext), AES.block_size)
    result = json.loads(decrypted_data)
    
    # Get task ID
    task_id = result["task_id"]
    print(f"Task created with ID: {task_id}")
    
    # Query task status
    while True:
        # Build status query request
        status_data = {
            "task_id": task_id
        }
        status_data_json = json.dumps(status_data)
        status_signature = hashlib.sha256(status_data_json.encode('utf-8')).hexdigest()
        
        iv = get_random_bytes(16)
        cipher_aes = AES.new(aes_key, AES.MODE_CBC, iv)
        padded_status_data = pad(status_data_json.encode('utf-8'), AES.block_size)
        encrypted_status_data = base64.b64encode(iv + cipher_aes.encrypt(padded_status_data)).decode('utf-8')
        
        status_request = {
            "encrypted_data": encrypted_status_data,
            "encrypted_key": encrypted_aes_key,
            "signature": status_signature
        }
        
        status_response = requests.post(
            "http://localhost:8000/api/task_status",
            json=status_request
        )
        
        if status_response.status_code == 200:
            # Decrypt status response
            status_response_data = status_response.json()
            encrypted_status = status_response_data["encrypted_data"]
            encrypted_status_bytes = base64.b64decode(encrypted_status)
            iv = encrypted_status_bytes[:16]
            ciphertext = encrypted_status_bytes[16:]
            
            cipher_aes = AES.new(aes_key, AES.MODE_CBC, iv)
            decrypted_status = unpad(cipher_aes.decrypt(ciphertext), AES.block_size)
            status_result = json.loads(decrypted_status)
            
            if status_result["status"] == "completed":
                print("Task completed!")
                print(f"Result: {status_result['result']}")
                break
            elif status_result["status"] == "error":
                print(f"Task failed: {status_result.get('error', 'Unknown error')}")
                break
            else:
                print(f"Task status: {status_result['status']}")
                import time
                time.sleep(2)  # Wait 2 seconds before querying again
        else:
            print(f"Failed to get task status: {status_response.status_code}")
            break
else:
    print(f"Request failed: {response.status_code}")
    print(response.text)
```

### 3. Web Interface

DocuSnap-Backend also provides a simple Web interface for testing and demonstration:

1. Access `http://localhost:8000/ocr` in your browser
2. Upload image files
3. Select processing type (document, form, or form filling)
4. Click the submit button
5. View processing results

## Common Issues

### 1. OCR Service Connection Failure

**Issue**: The application cannot connect to the OCR service.

**Solution**:
- Confirm the OCR service is running
- Check if the OCR service URL configuration is correct
- Verify network connections and firewall settings

### 2. LLM API Call Failure

**Issue**: Calling the Zhipu AI API fails.

**Solution**:
- Check if the API key is correct
- Verify network connection
- Confirm API usage quota has not been exceeded

### 3. End-to-End Encryption Issues

**Issue**: Encryption or decryption operations fail.

**Solution**:
- Confirm key file paths are configured correctly
- Verify key file permission settings
- Check encryption and decryption code implementation

### 4. Performance Issues

**Issue**: System responds slowly or processing times out.

**Solution**:
- Increase the number of worker threads
- Optimize OCR and LLM service concurrency settings
- Consider upgrading hardware resources or using higher-performance servers

## Next Steps

After completing the basic deployment and configuration, you can consider the following next steps:

1. **Configure Nginx Reverse Proxy**: Improve security and performance
2. **Set up HTTPS**: Obtain SSL certificates using Let's Encrypt
3. **Implement Monitoring**: Monitor system status using Prometheus and Grafana
4. **Configure Log Management**: Centralize log management using the ELK stack
5. **Implement High Availability Deployment**: Configure multi-instance deployment and load balancing

For more detailed information, please refer to the [Deployment Architecture](../deployment/overview.md) and [Scalability and Fault Tolerance](../deployment/scalability-fault-tolerance.md) documentation.

## Resource Links

- [GitHub Repository](https://github.com/JI-DeepSleep/DocuSnap-Backend)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [CnOCR Documentation](https://github.com/breezedeus/cnocr)
- [Zhipu AI Documentation](https://www.zhipuai.cn/documentation)
