# Discovery Accelerator

A comprehensive framework for accelerating the discovery phase of projects for service-based companies. This tool parses Statement of Work (SOW) documents, extracts requirements and boundaries, generates discovery questions, and processes meeting transcripts to extract answers and identify follow-up questions.

## Overview

The Discovery Accelerator streamlines the initial discovery process by:

1. Analyzing SOW documents to identify scope, requirements, and boundaries
2. Automatically generating relevant discovery questions for ambiguous requirements
3. Processing meeting transcripts to extract answers to questions
4. Generating follow-up questions based on meeting content
5. Tracking the discovery process until all questions are answered

## Components

### Core Modules

- **SOW Parser**: Extracts sections, requirements, and boundaries from SOW documents
- **Question Generator**: Creates initial and follow-up questions
- **Transcript Analyzer**: Processes meeting transcripts to find answers and identify new information
- **Discovery Database**: Tracks questions, answers, and discovery status
- **File Processing**: Processes various document types (PDF, DOCX, PPT, images)
- **Discovery Accelerator**: Orchestrates the entire discovery process

### API Endpoints

The system provides a RESTful API with the following endpoints:

- `/start_discovery`: Start a new discovery process with a SOW document
- `/process_transcript`: Process a meeting transcript to find answers
- `/get_questions/{project_id}`: Get all questions for a project
- `/discovery_status/{project_id}`: Check if the discovery process is complete
- `/discovery_report/{project_id}`: Generate a comprehensive discovery report

## Installation

### Prerequisites

- Python 3.8+
- Google Gemini API key
- Required packages (see requirements.txt)

### Setup

1. Clone the repository
2. Install dependencies: `pip install -r requirements.txt`
3. Set environment variables:
   ```bash
   export GOOGLE_API_KEY=your_gemini_api_key
   ```
4. Start the model inference service:
   ```bash
   python model_inference.py
   ```
5. Start the API server:
   ```bash
   python project_api.py
   ```

## Usage

### Starting a New Discovery

```bash
curl -X POST "http://localhost:8000/start_discovery" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "Project X",
    "sow_path": "/path/to/sow.pdf",
    "additional_docs_paths": ["/path/to/supporting.docx"]
  }'
```

### Processing a Meeting Transcript

```bash
curl -X POST "http://localhost:8000/process_transcript" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 1,
    "transcript_text": "Meeting transcript content..."
  }'
```

### Getting Questions

```bash
curl -X GET "http://localhost:8000/get_questions/1?status=unanswered"
```

### Checking Discovery Status

```bash
curl -X GET "http://localhost:8000/discovery_status/1"
```

### Generating a Discovery Report

```bash
curl -X GET "http://localhost:8000/discovery_report/1"
```

## File Types Supported

- PDF documents
- Microsoft Word documents (DOCX)
- Microsoft PowerPoint presentations (PPTX)
- Image files (JPG, PNG)

## Development

### Adding New Features

1. Add new functionality to the appropriate module
2. Update the API endpoints in `project_api.py`
3. Run tests to ensure everything works as expected

### Running Tests

```bash
pytest tests/
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
