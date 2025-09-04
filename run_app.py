import subprocess
import sys
import os
import time
import webbrowser
import threading
from dotenv import dotenv_values 


def run_inference_server():
    print("Starting Inference Server...")
    subprocess.Popen([sys.executable, "model_inference.py"]) 
    time.sleep(5)

def run_api_server():
    print("Starting API Server...")
    subprocess.Popen([sys.executable, "project_api.py"]) 
    time.sleep(5)

def run_streamlit():
    print("Starting Streamlit App...")
    subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py"]) 
    time.sleep(5)

def open_browser():
    time.sleep(8)
    print("Opening web browser...")
    webbrowser.open("http://localhost:8501")

if __name__ == "__main__":
    # Create directory for uploaded files
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("temp", exist_ok=True)
   
    env_vars_from_file = dotenv_values()
    

    if not env_vars_from_file.get("GOOGLE_API_KEY"):
        print("WARNING: GOOGLE_API_KEY is not set or is empty in your .env file.")
        print("Gemini API functionality will not work without an API key.")
        print("\nPlease ensure you have a .env file in the same directory as this script,")
        print("or in a parent directory, with the following content:")
        print("  GOOGLE_API_KEY=\"your_api_key_here\"")
        
        # Ask if user wants to continue anyway
        response = input("\nDo you want to continue without setting the API key? (y/n): ")
        if response.lower() != 'y':
            print("Exiting. Please set the API key in .env and try again.")
            sys.exit(1)
    
    # Start all servers
    run_inference_server()
    run_api_server()
    run_streamlit()
    
    # Open browser in a separate thread
    threading.Thread(target=open_browser).start()
    
    print("\nDiscovery Accelerator is running!")
    print("Inference Server: http://localhost:5000")
    print("API Server: http://localhost:8000")
    print("Streamlit App: http://localhost:8501")
    print("\nPress Ctrl+C to stop all servers")
    
    try:
        # Keep the script running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down all servers...")
        # Subprocesses started with Popen will typically terminate naturally
        # when the main process ends. For more robust shutdown, you might
        # store Popen objects and call .terminate() on them.
        print("Done.")