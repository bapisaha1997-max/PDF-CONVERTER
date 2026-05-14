# AI PDF Translator (English to Bengali)

A professional web application that extracts English text from PDFs and translates it into Bengali using Gemini/Groq AI. Designed for exam papers and documents with complex layouts.

## Features
- **Smart Extraction:** Filters out garbled legacy fonts and focuses on English content.
- **Side-by-Side PDF Generation:** Creates a bilingual PDF with original and translated text aligned.
- **Dual AI Engines:** Supports Google Gemini 2.0 Flash and Groq (Llama 3) for high-quality translations.
- **Modern UI:** A sleek, responsive dark-mode interface.

## How to Run Locally
1. Install dependencies: `pip install -r requirements.txt`
2. Add your API keys to a `.env` file:
   ```env
   GEMINI_API_KEY=your_key
   GROQ_API_KEY=your_key
   ```
3. Run the app: `python app.py`

## Hosting
This project is configured for deployment on **Render** using the included `Procfile`.

## License
[MIT](LICENSE)
