from flask import Flask, request, jsonify
import io
import base64
from PIL import Image
import google.generativeai as genai
import os
import numpy as np
from dotenv import load_dotenv
load_dotenv()

print("\n=== Starting Discovery Accelerator Model Inference Server ===\n")

app = Flask(__name__)

# Configure Gemini API
api_key = os.getenv('GOOGLE_API_KEY')
if not api_key:
    print("WARNING: No GOOGLE_API_KEY found in environment variables!")
    print("Please set the GOOGLE_API_KEY environment variable to use Gemini features.")
    print("Continuing with limited functionality...")
    client = None
else:
    print(f"Using Gemini API key: {api_key[:5]}...{api_key[-5:] if len(api_key) > 10 else ''}")
    try:
        # Configure the genai client
        genai.configure(api_key=api_key)
        client = genai.Client(api_key=api_key)
        
        print("Initializing Gemini models...")
        # Model for text generation
        gen_model = genai.GenerativeModel('gemini-2.0-flash')
        print("Gemini generative model initialized successfully")
        
        # Verify embedding model is available
        print("Initializing Gemini embedding model...")
        embedding_model_name = "gemini-embedding-exp-03-07"
        print(f"Gemini embedding model '{embedding_model_name}' initialized successfully")
    except Exception as e:
        print(f"ERROR initializing Gemini models: {str(e)}")
        print("Continuing with limited functionality...")
        client = None

def preprocess_image(image: Image, max_size: int = 384) -> Image:
    """Resize image while maintaining aspect ratio"""
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    ratio = max_size / max(image.size)
    if ratio < 1:
        new_size = tuple(int(dim * ratio) for dim in image.size)
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    
    return image

@app.route('/', methods=['GET'])
def home():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'services': {
            'embed_text': '/embed_text',
            'process_image': '/process_image',
            'process_image_batch': '/process_image_batch'
        }
    })

@app.route('/embed_text', methods=['POST'])
def embed_text():
    try:
        print("\n=== Processing embed_text request ===")
        data = request.get_json()
        text = data.get('text')
        
        if not text:
            print("ERROR: No text provided")
            return jsonify({'error': 'No text provided'}), 400
        
        if not client:
            print("ERROR: Google API client not initialized")
            return jsonify({'error': 'Embedding service not available - Google API client not initialized'}), 500
        
        print(f"Creating embedding for text of length {len(text)}")
        
        # Use Google's embedding model
        result = client.models.embed_content(
            model="gemini-embedding-exp-03-07",
            contents=text
        )
        
        # Extract the embedding values
        embedding = np.array(result.embeddings[0].values)
        
        print(f"Successfully created embedding with shape {embedding.shape}")
        return jsonify({
            'embedding': embedding.tolist()
        })
    
    except Exception as e:
        print(f"ERROR in embed_text: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/process_image', methods=['POST'])
def process_image():
    try:
        print("\n=== Processing process_image request ===")
        data = request.get_json()
        image_data = data.get('image')  # Base64 encoded image
        
        if not image_data:
            print("ERROR: No image provided")
            return jsonify({'error': 'No image provided'}), 400
        
        if not client:
            print("ERROR: Google API client not initialized")
            return jsonify({'error': 'Image processing service not available - Google API client not initialized'}), 500
        
        # Decode base64 image
        print("Decoding base64 image")
        image_bytes = base64.b64decode(image_data)
        image = Image.open(io.BytesIO(image_bytes))
        print(f"Image decoded successfully: size {image.size}, mode {image.mode}")
        
        # Preprocess image
        image = preprocess_image(image)
        print(f"Image preprocessed to size {image.size}")
        
        try:
            # Use Gemini to caption the image
            print("Generating image caption with Gemini")
            response = gen_model.generate_content([
                "Describe the content of this image in detail.",
                image
            ])
            
            description = response.text
            print(f"Caption generated: {description[:100]}...")
            
            # Get text embedding for the generated description using Google's embedding model
            print("Creating embedding for image caption")
            embed_result = client.models.embed_content(
                model="gemini-embedding-exp-03-07",
                contents=description
            )
            text_embedding = np.array(embed_result.embeddings[0].values)
            
            print(f"Successfully created embedding with shape {text_embedding.shape}")
            
            return jsonify({
                'embedding': text_embedding.reshape(1, -1).tolist(),
                'description': description
            })
        except Exception as gemini_error:
            print(f"ERROR using Gemini: {str(gemini_error)}")
            print("Falling back to simple embedding of 'image content'")
            
            # Fallback to simple embedding if Gemini fails
            fallback_text = "image content"
            embed_result = client.models.embed_content(
                model="gemini-embedding-exp-03-07",
                contents=fallback_text
            )
            text_embedding = np.array(embed_result.embeddings[0].values)
            
            return jsonify({
                'embedding': text_embedding.reshape(1, -1).tolist(),
                'description': "Image processing failed, using fallback embedding"
            })
    
    except Exception as e:
        print(f"ERROR in process_image: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/process_image_batch', methods=['POST'])
def process_image_batch():
    try:
        print("\n=== Processing process_image_batch request ===")
        data = request.get_json()
        image_data_batch = data.get('images')  # List of base64 encoded images
        
        if not image_data_batch or not isinstance(image_data_batch, list):
            print("ERROR: No images provided or invalid format")
            return jsonify({'error': 'No images provided or invalid format'}), 400
        
        if not client:
            print("ERROR: Google API client not initialized")
            return jsonify({'error': 'Image processing service not available - Google API client not initialized'}), 500
        
        print(f"Processing batch of {len(image_data_batch)} images")
        results = []
        
        for i, image_data in enumerate(image_data_batch):
            try:
                print(f"Processing image {i+1}/{len(image_data_batch)}")
                # Decode base64 image
                image_bytes = base64.b64decode(image_data)
                image = Image.open(io.BytesIO(image_bytes))
                
                # Preprocess image
                image = preprocess_image(image)
                
                try:
                    # Use Gemini to caption the image
                    response = gen_model.generate_content([
                        "Describe the content of this image in detail.",
                        image
                    ])
                    
                    description = response.text
                    
                    # Get text embedding for the generated description using Google's embedding model
                    embed_result = client.models.embed_content(
                        model="gemini-embedding-exp-03-07",
                        contents=description
                    )
                    text_embedding = np.array(embed_result.embeddings[0].values)
                    
                    results.append({
                        'embedding': text_embedding.reshape(1, -1).tolist(),
                        'description': description
                    })
                    print(f"Successfully processed image {i+1}")
                except Exception as gemini_error:
                    print(f"ERROR using Gemini for image {i+1}: {str(gemini_error)}")
                    
                    # Fallback to simple embedding
                    fallback_text = "image content"
                    embed_result = client.models.embed_content(
                        model="gemini-embedding-exp-03-07",
                        contents=fallback_text
                    )
                    text_embedding = np.array(embed_result.embeddings[0].values)
                    
                    results.append({
                        'embedding': text_embedding.reshape(1, -1).tolist(),
                        'description': "Image processing failed, using fallback embedding"
                    })
            except Exception as img_error:
                print(f"ERROR processing image {i+1}: {str(img_error)}")
                # Skip this image
                continue
        
        print(f"Successfully processed {len(results)}/{len(image_data_batch)} images")
        return jsonify({
            'results': results
        })
    
    except Exception as e:
        print(f"ERROR in process_image_batch: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("\nStarting inference server on http://localhost:5000")
    print("Available endpoints:")
    print("- /embed_text: Create embeddings from text")
    print("- /process_image: Process images and create embeddings")
    print("- /process_image_batch: Process multiple images at once")
    print("\nServer is ready to accept requests!")
    
    # Save local URL to a file for other processes to use
    with open("inference_url.txt", "w") as f:
        f.write("http://localhost:5000")
    print("Inference URL saved to inference_url.txt")
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=False)