import os
import json
import logging

from uuid import uuid4

from quart import Quart, websocket, render_template, request, jsonify
from quart_cors import cors
import httpx

app = Quart(__name__, static_folder='static')
app = cors(app, allow_origin=["http://localhost:5000", "https://ai.mitta.ai"])

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

@app.route('/', methods=['GET', 'POST'])
@app.route('/convert', methods=['GET', 'POST'])
async def home():
    # Initialize the default instructions
    instructions = [
        "Rotate image by 90 degrees",
        "Resize width to 420 and crop to 420 high"
        "Convert to JPEG with quality 85",
        "Resize to 1080p HD resolution",
        "Extract first frame as PNG",
        "Convert to grayscale",
        "Apply sepia tone effect",
        "Enhance brightness and contrast",
        "Create a 5-second GIF from video",
        "Extract audio from video as MP3",
        "Convert to MP4 with H.264 encoding",
        "Trim video to first 10 seconds",
        "Convert to 360p WebM format",
        "Increase playback speed by 2x",
        "Create a thumbnail at the first minute",
        "Overlay text 'LOL' on image",
        "Flip image horizontally",
        "Normalize audio in a video file",
        "Compress to a smaller file size",
        "Convert audio to MP3 format",
        "Convert video to animated GIF",
        "Extract subtitles from a video"
    ]

    if request.method == 'POST':
        form_data = await request.form
        posted_instruction = form_data.get('instructions')

        # If a new instruction is posted, add it to the top of the list
        if posted_instruction and posted_instruction not in instructions:
            instructions.insert(0, posted_instruction)

    # Pass the (possibly updated) instructions list to the template
    return await render_template('index.html', instructions=instructions)


@app.route('/upload', methods=['POST'])
async def upload():
    if 'file' in await request.files:
        file = (await request.files)['file']
        form_data = await request.form
        instructions = form_data.get('instructions', 'Convert to a 640 wide gif')
        uuid = form_data.get('uuid')
        
        # Log the received instructions for debugging
        logging.info(f"Received instructions: {instructions}")

        # Prepare the JSON payload and encode it into bytes
        # httpx recent versions may not like non-encoded payloads
        json_data = json.dumps({
            "user_document": {"uuid": uuid},
            "ffmpeg_request": instructions
        }).encode('utf-8')

        logging.info(json_data)

        # Prepare the file to be uploaded to the external handler
        files = {
            'file': (file.filename, file.read(), file.content_type),
            'json_data': (None, json_data, 'application/json')
        }
        
        # Define the endpoint and token
        pipeline = os.getenv('FFMPEG_PIPELINE')
        mitta_token = os.getenv('MITTA_TOKEN')
        url = f"https://mitta.ai/pipeline/{pipeline}/task?token={mitta_token}"

        # Send the file using httpx
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, files=files)
        logging.info(f"JSON task response: {response.json()}")
        
        # Check the response from the external handler
        if response.status_code == 200:
            await broadcast({"status": "success", "message": "File uploaded successfully!"})
            return jsonify({"status": "success", "message": "File uploaded successfully"})
        else:
            await broadcast({"status": "success", "message": "File upload failed, sorry."})
            return jsonify({"status": "error", "message": "Failed to upload file"})

    return jsonify({"status": "error", "message": "No file received"})


@app.route('/callback', methods=['POST'])
async def callback():
    data = await request.get_json()
    logging.info(data)

    # uuid and message
    message = "Processing..."  # Default message

    # Iterate through the keys in the data dictionary
    for key, value in data.items():
        if 'message' in key:
            message = value

    # Other variables
    convert_uris = data.get('convert_uri', [])
    user_document = data.get('user_document', {})
    filenames = data.get('filename', [])

    # Check if convert_uri is provided and download the file
    if convert_uris:
        # Ensure the download directory exists
        download_dir = 'download'
        os.makedirs(download_dir, exist_ok=True)

        # Download the first file in the list
        convert_uri = convert_uris[0]
        if filenames:
            filename = filenames[0]
            filepath = os.path.join(download_dir, filename)

            async with httpx.AsyncClient() as client:
                mitta_token = os.getenv('MITTA_TOKEN')
                mitta_url = f"{convert_uri}?token={mitta_token}"
                response = await client.get(mitta_url)

                if response.status_code == 200:
                    with open(filepath, 'wb') as f:
                        f.write(response.content)
                    logging.info(f"File downloaded successfully: {filepath}")
                else:
                    logging.error(f"Failed to download file from {convert_uri}")
                    return jsonify({"status": "failed"})

            convert_uri = f"https://ai.mitta.ai/download/{filename}"
            logging.info(convert_uri)
        else:
            filename = ''
    else:
        convert_uri = ''
        filename = ''

    if isinstance(user_document, dict):
        uuid = user_document.get('uuid', 'anonymous')
    else:
        uuid = 'anonymous'

    logging.info(f"uuid: {uuid}")

    await broadcast(
        {
            "status": "success", 
            "message": message, 
            "convert_uri": convert_uri,
            "filename": filename
        }, 
        recipient_id=uuid
    )

    return jsonify({"status": "success"})


connected_websockets = {}

from quart import send_from_directory

@app.route('/download/<filename>')
async def download_file(filename):
    download_dir = 'download'  # Same directory you used for saving the files
    return await send_from_directory(download_dir, filename, as_attachment=True)


@app.websocket('/ws')
async def ws():
    unique_id = str(uuid4())  # Generate a unique ID for the session
    ws_obj = websocket._get_current_object()
    connected_websockets[unique_id] = ws_obj  # Store the WebSocket object with the unique ID

    try:
        await ws_obj.send_json({'uuid': unique_id})
        while True:
            data = await websocket.receive()
    except:
        pass
    finally:
        connected_websockets.pop(unique_id, None)  # Remove the WebSocket from the dictionary on disconnect


async def broadcast(message, recipient_id=None):
    if recipient_id:
        # If a recipient ID is provided, only send to that WebSocket
        ws = connected_websockets.get(recipient_id)
        if ws:
            await ws.send_json(message)
    else:
        # If no recipient ID is provided, broadcast to all connected WebSockets
        for ws in connected_websockets.values():
            await ws.send_json(message)


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
