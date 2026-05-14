import subprocess
import webbrowser
import time
import sys
import os

def run_application():
    """
    Starts the Flask server and automatically opens the application in the default web browser.
    """
    try:
        # Get the directory where this script is located
        base_dir = os.path.dirname(os.path.abspath(__file__))
        app_path = os.path.join(base_dir, "app.py")
        
        if not os.path.exists(app_path):
            print(f"Error: app.py not found at {app_path}")
            input("\nPress Enter to exit...")
            return

        print("--- PDF Translator Starter ---")
        
        # Check if dependencies are installed
        print("Checking dependencies...")
        try:
            import flask
            import fitz
            import groq
            import fpdf
            print("Dependencies verified.")
        except ImportError as e:
            missing = getattr(e, 'name', 'unknown')
            print(f"Error: Missing dependency: '{missing}'")
            print("Please run: python -m pip install -r requirements.txt")
            input("\nPress Enter to exit...")
            return

        print("1. Starting Flask backend...")
        
        # Start the Flask app as a subprocess
        # Using sys.executable ensures we use the same Python that is running this script
        server_process = subprocess.Popen([sys.executable, app_path], cwd=base_dir)
        
        # Give the server a moment to initialize
        print("Waiting for server to start...")
        time.sleep(3)
        
        # Check if process is still running
        if server_process.poll() is not None:
            print("\nError: The Flask server failed to start immediately.")
            print("This usually happens if Port 5000 is already in use or there is a code error.")
            input("\nPress Enter to exit...")
            return

        print("2. Opening the application in your browser...")
        webbrowser.open("http://127.0.0.1:5000")
        
        print("\nSUCCESS: Application is running!")
        print("---------------------------------------")
        print("Keep this window open to keep the website running.")
        print("Press Ctrl+C here to stop the server.")
        print("---------------------------------------")
        
        # Keep the script running while the server is active
        server_process.wait()

    except KeyboardInterrupt:
        print("\nShutting down server...")
        if 'server_process' in locals():
            server_process.terminate()
            server_process.wait()
        print("Server stopped.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        input("\nPress Enter to exit...")

if __name__ == "__main__":
    run_application()
