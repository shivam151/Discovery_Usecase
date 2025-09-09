import os
import boto3
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uvicorn
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from psycopg2.extras import RealDictCursor
from file_processing import ProjectDataPipeline
from discovery_db_postgresql import DiscoveryDatabase
from discovery_accelerator import DiscoveryAccelerator
from discovery_db_postgresql import DiscoveryDatabase
from middleware.auth_middleware import KeycloakAuthMiddleware
from dotenv import load_dotenv
import logging
import time
from additional_doc import AdditionalDocumentProcessor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()  # Outputs logs to the console
    ]
)

logger = logging.getLogger(__name__)
load_dotenv()

# Initialize S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION')
)
S3_BUCKET = os.getenv('S3_BUCKET_NAME', 'ai-assessment-accelerator')

# Pydantic models for request/response validation
class DirectoryProcessRequest(BaseModel):
    directory_path: str = Field(..., description="Path to the directory to process")
    project_name: Optional[str] = Field(None, description="Optional custom project name")
    user_id: Optional[int] = Field(1, description="User ID for the project")

class QueryRequest(BaseModel):
    project_name: str = Field(..., description="Name of the project to query")
    query: str = Field(..., description="Query text")
    n_results: int = Field(default=5, description="Number of results to return")

class QueryResult(BaseModel):
    source: str
    type: str
    content: Optional[str] = None
    answer: Optional[str] = None

class QueryResponse(BaseModel):
    status: str
    results: List[QueryResult]

class ProjectsResponse(BaseModel):
    status: str
    projects: List[Dict[str, Any]]

class ProjectDeleteResponse(BaseModel):
    message: str
    deleted_project_id: int
    status: str

class HealthResponse(BaseModel):
    status: str
    bucket_name: str

class ProcessAdditionalDocsRequest(BaseModel):
    project_id: int = Field(..., description="ID of the existing project")
    document_paths: List[str] = Field(..., description="Paths to additional documents")

class ProcessAdditionalDocsResponse(BaseModel):
    status: str
    project_id: int
    project_name: Optional[str] = None
    documents_processed: int
    answers_found: int
    new_questions_generated: int
    discovery_status: Optional[Dict] = None
    processed_documents: List[Dict] = []

class ProcessDocumentsRequest(BaseModel):
    project_name: str = Field(..., description="Name of the project")
    sow_path: str = Field(..., description="Path to the SOW document")
    additional_docs_paths: Optional[List[str]] = Field(None, description="Paths to additional documents")

class GenerateQuestionsRequest(BaseModel):
    project_id: int = Field(..., description="ID of the project")
    sow_data: Dict[str, Any] = Field(..., description="SOW data from document processing")

class GenerateQuestionsByIdRequest(BaseModel):
    project_id: int = Field(..., description="ID of the project")

class StartDiscoveryRequest(BaseModel):
    project_name: str = Field(..., description="Name of the project")
    sow_path: str = Field(..., description="Path to the SOW document")
    additional_docs_paths: Optional[List[str]] = Field(None, description="Paths to additional documents")

class ProcessTranscriptRequest(BaseModel):
    project_id: int = Field(..., description="ID of the project")
    transcript_text: str = Field(..., description="Text of the meeting transcript")

# FastAPI app
app = FastAPI(
    title="Discovery Accelerator API",
    description="API for processing SOW documents, generating discovery questions, and analyzing meeting transcripts",
    version="1.0.0"
)

# CORS configuration
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:8080",
    "https://discovery.yashtech.link",
]

app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

app.add_middleware(KeycloakAuthMiddleware)

# Configuration
INFERENCE_API_URL = "http://localhost:5000"
GEMINI_API_KEY = os.getenv('GOOGLE_API_KEY')

# Initialize the pipeline
pipeline = ProjectDataPipeline(
    bucket_name=S3_BUCKET,
    inference_api_url=INFERENCE_API_URL,
    gemini_api_key=GEMINI_API_KEY,
)

def sanitize_filename(filename: str) -> str:
    """Sanitize filename to be safe for filesystem and S3 operations, preserving valid extensions"""
    sanitized = "".join(c for c in filename if c.isalnum() or c in ('-', '_', '.'))
    parts = sanitized.rsplit('.', 1)
    if len(parts) == 2:
        base, ext = parts
        base = base.replace('.', '')
        return f"{base}.{ext.lower()}" if ext else base
    return sanitized.rstrip('.')

def generate_presigned_url(bucket: str, key: str, expiration: int = 3600) -> str:
    """Generate a presigned URL for an S3 object"""
    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=expiration
        )
        return url
    except Exception as e:
        logger.error(f"Error generating presigned URL for {key}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate presigned URL: {str(e)}")

@app.post("/process_directory")
async def process_directory(request: DirectoryProcessRequest):
    """
    Process a directory containing documents and images from S3.
    """
    try:
        keycloak_user = request.state.user
        email = keycloak_user.get('email')
        project_name = request.project_name or os.path.basename(request.directory_path)
        project_name = sanitize_filename(project_name)

        s3_prefix = f"discovery_accelerator/uploads/{sanitize_filename(email)}/{project_name}/files/"
        
        response = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=s3_prefix)
        if 'Contents' not in response:
            raise HTTPException(status_code=404, detail="Directory does not exist in S3")
        
        # Generate presigned URLs for files
        file_urls = []
        for obj in response.get('Contents', []):
            presigned_url = generate_presigned_url(S3_BUCKET, obj['Key'])
            file_urls.append(presigned_url)
        
        result = pipeline.process_project(project_name, s3_prefix, email=email, sow_data={})
        
        return {
            'status': 'success',
            'project_name': project_name,
            'documents_processed': len(result.get('documents', [])),
            'requirement_matches': result.get('document_requirement_matches', {})
        }
    
    except Exception as e:
        logger.error(f"Error in process_directory: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/query_project", response_model=QueryResponse)
async def query_project(request: QueryRequest):
    """
    Query a processed project with text using Gemini.
    """
    try:
        # Retrieve documents from the project
        db = DiscoveryDatabase()
        conn = db._get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT name FROM projects WHERE name = %s", (request.project_name,))
        project = cursor.fetchone()
        cursor.close()
        db.release_connection(conn)
        
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Process the project to get documents
        result = pipeline.process_project(
            project_name=request.project_name,
            s3_prefix=f"discovery_accelerator/uploads/{sanitize_filename(request.state.user.get('email'))}/{request.project_name}/files/",
            project_owner=request.state.user.get('email'),
            sow_data={}
        )
        
        documents = result.get('documents', [])
        if not documents:
            return {
                'status': 'success',
                'results': []
            }
        
        # Query each document using Gemini
        formatted_results = []
        for doc in documents[:request.n_results]:
            answer = pipeline.answer_question_with_gemini(
                question=request.query,
                document_content=doc['content']
            )
            formatted_result = QueryResult(
                source=doc['source'],
                type=doc['type'],
                content=doc['content'] if doc['type'] == 'text' else None,
                answer=answer
            )
            formatted_results.append(formatted_result)
        
        return {
            'status': 'success',
            'results': formatted_results
        }
    
    except Exception as e:
        logger.error(f"Error in query_project: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete_project/{project_id}", response_model=ProjectDeleteResponse)
async def delete_project(project_id: int):
    """
    Delete a project, its associated database records, and S3 objects.
    """
    try:
        logger.info(f"Attempting to delete project with ID {project_id}")
        db = DiscoveryDatabase()
        result = db.delete_project_by_id(project_id)
        
        if result is None:
            logger.warning(f"Project {project_id} not found")
            return JSONResponse(
                status_code=404,
                content={
                    "message": f"Project {project_id} not found",
                    "deleted_project_id": project_id,
                    "status": "error",
                    "deleted_s3_objects": []
                }
            )

        return ProjectDeleteResponse(
            message=f"Project {project_id} deleted successfully",
            deleted_project_id=result['project_id'],
            status="success",
            deleted_s3_objects=result['deleted_s3_objects']
        )
    
    except Exception as e:
        logger.error(f"Error in delete_project: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list_projects", response_model=ProjectsResponse)
async def list_projects(request: Request):
    """
    List all available projects for the user in the system.
    """
    try:
        #print("API: Calling list_projects method...")
        keycloak_user = request.state.user
        email=keycloak_user.get('email')
        #projects = pipeline.list_projects(email)
        db = DiscoveryDatabase()
        projects = db.list_projects_byowner(email)
        if projects:
            print(f"API: Received {len(projects)} projects: {projects}")
        
        if projects is None:
            return {
                'status': 'success',
                'projects': []
            }
    
        projects_list = [dict(project) for project in projects]
        
        return {
            'status': 'success',
            'projects': projects_list
        }
    
    except Exception as e:
        logger.error(f"Error listing projects: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'status': 'warning',
            'projects': [],
            'error': str(e)
        }

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.
    """
    return {
        'status': 'healthy',
        'bucket_name': S3_BUCKET
    }

@app.post("/process_documents")
async def process_documents(
    request: Request,
    project_name: str = Form(..., description="Name of the project"),
    sow_file: UploadFile = File(..., description="SOW document file"),
    additional_docs: List[UploadFile] = File(None, description="Additional document files")
):
    """
    Process documents without generating questions.
    """
    logger.info("'/process_documents' endpoint called.")
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )

        keycloak_user = request.state.user
        email = keycloak_user.get('email')
        project_name = sanitize_filename(project_name)
        logger.info(f"Processing request for user: '{email}'. Project name: '{project_name}'.")

        # Preserve original file extension
        sow_filename = sanitize_filename(sow_file.filename)
        file_extension = os.path.splitext(sow_filename)[1].lower()
        if file_extension not in ['.pdf', '.docx', '.pptx' ,'.txt','.xlsx' , '.xls', '.csv'  ]:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_extension}")
        s3_prefix = f"discovery_accelerator/uploads/{sanitize_filename(email)}/{project_name}/"
        sow_key = f"{s3_prefix}files/{sow_filename}"
        logger.info(f"Uploading SOW file '{sow_filename}' to S3 key: {sow_key}")

        # Upload SOW file
        try:
            content = await sow_file.read()
            s3_client.put_object(
                Bucket=S3_BUCKET,
                Key=sow_key,
                Body=content,
                ContentType=sow_file.content_type or {
                    '.pdf': 'application/pdf',
                    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                    '.txt': 'text/plain',
                    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    '.xls': 'application/vnd.ms-excel',
                    '.csv': 'text/csv'
                    
                }.get(file_extension, 'application/octet-stream')
            )
        except s3_client.exceptions.ClientError as e:
            logger.error(f"Failed to upload SOW file to S3: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Failed to upload SOW file: {str(e)}")
        sow_url = generate_presigned_url(S3_BUCKET, sow_key)
        logger.info("SOW file uploaded successfully.")

        # Upload additional documents
        additional_docs_keys = []
        num_additional_docs = len(additional_docs) if additional_docs else 0
        logger.info(f"Found {num_additional_docs} additional document(s) to process.")

        if additional_docs:
            for i, doc in enumerate(additional_docs):
                if not doc.filename:
                    logger.warning(f"Skipping an additional document at index {i} because it has no filename.")
                    continue
                doc_filename = sanitize_filename(doc.filename)
                doc_extension = os.path.splitext(doc_filename)[1].lower()
                if doc_extension not in ['.pdf', '.docx', '.pptx' , '.txt','.xlsx' , '.xls', '.csv' ]:
                    logger.warning(f"Skipping document {doc_filename} due to unsupported format: {doc_extension}")
                    continue
                doc_key = f"{s3_prefix}additional_docs/files/{doc_filename}"
                logger.info(f"Uploading additional doc {i+1}/{num_additional_docs}: '{doc_filename}' to S3 key: {doc_key}")
                try:
                    content = await doc.read()
                    s3_client.put_object(
                        Bucket=S3_BUCKET,
                        Key=doc_key,
                        Body=content,
                        ContentType=doc.content_type or {
                            '.pdf': 'application/pdf',
                            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            '.xls': 'application/vnd.ms-excel',
                            '.csv': 'text/csv',
                            '.txt': 'text/plain',

                        }.get(doc_extension, 'application/octet-stream')
                    )
                    additional_docs_keys.append(generate_presigned_url(S3_BUCKET, doc_key))
                except s3_client.exceptions.ClientError as e:
                    logger.error(f"Failed to upload additional document '{doc_filename}': {str(e)}")
                    continue
            logger.info("All additional documents uploaded successfully.")

        logger.info(f"Starting document processing via accelerator for project '{project_name}'.")
        result = accelerator.process_documents(
            project_name=project_name,
            sow_path=sow_url,
            additional_docs_paths=additional_docs_keys if additional_docs_keys else None,
            project_owner=email
        )
        logger.info(f"Document processing finished for project '{project_name}'.")
        return JSONResponse(status_code=200, content=result)

    except ValueError as ve:
        logger.error(f"ValueError in '/process_documents': {str(ve)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(ve)}")
    except HTTPException as http_exc:
        logger.error(f"HTTPException in '/process_documents': {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.error(f"An unexpected error occurred in '/process_documents': {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"An internal server error occurred: {str(e)}")

@app.post("/generate_questions")
async def generate_questions(request: GenerateQuestionsRequest):
    """
    Generate questions from previously processed SOW data.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        result = accelerator.generate_questions(
            project_id=request.project_id,
            sow_data=request.sow_data
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error in generate_questions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/start_discovery")
async def start_discovery(
    request: Request,
    project_name: str = Form(..., description="Name of the project"),
    sow_file: UploadFile = File(..., description="SOW document file"),
    additional_docs: List[UploadFile] = File(None, description="Additional document files")
):
    """
    Combined endpoint that processes documents and generates questions.
    """
    try:
        keycloak_user = request.state.user
        email = keycloak_user.get('email')
        project_name = sanitize_filename(project_name)
        s3_prefix = f"discovery_accelerator/uploads/{sanitize_filename(email)}/{project_name}/"
        
        # Preserve original file extension
        sow_filename = sanitize_filename(sow_file.filename)
        file_extension = os.path.splitext(sow_filename)[1].lower()
        if file_extension not in ['.pdf', '.docx', '.pptx', '.txt','.xlsx' , '.xls', '.csv' ]:
            raise HTTPException(status_code=400, detail=f"Unsupported file format: {file_extension}")
        sow_key = f"{s3_prefix}files/{sow_filename}"
        logger.info(f"Uploading SOW file '{sow_filename}' to S3 at {sow_key}")
        content = await sow_file.read()
        s3_client.put_object(
            Bucket=S3_BUCKET,
            Key=sow_key,
            Body=content,
            ContentType=sow_file.content_type or {
                '.pdf': 'application/pdf',
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel',
                '.csv': 'text/csv',
                '.txt': 'text/plain'
            }.get(file_extension, 'application/octet-stream')
        )
        sow_url = generate_presigned_url(S3_BUCKET, sow_key)
        
        additional_docs_keys = []
        if additional_docs:
            for i, doc in enumerate(additional_docs):
                if not doc.filename:
                    logger.warning(f"Skipping document {i+1} upload due to missing filename")
                    continue
                doc_filename = sanitize_filename(doc.filename)
                doc_extension = os.path.splitext(doc_filename)[1].lower()
                if doc_extension not in ['.pdf', '.docx', '.pptx', '.txt','.xlsx' , '.xls', '.csv' ]:
                    logger.warning(f"Skipping document {doc_filename} due to unsupported format: {doc_extension}")
                    continue
                doc_key = f"{s3_prefix}additional_docs/files/{doc_filename}"
                logger.info(f"Uploading additional doc {i+1}/{len(additional_docs)}: '{doc_filename}' to S3 at {doc_key}")
                content = await doc.read()
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=doc_key,
                    Body=content,
                    ContentType=doc.content_type or {
                        '.pdf': 'application/pdf',
                        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        '.xls': 'application/vnd.ms-excel',
                        '.txt': 'text/plain',
                        '.csv': 'text/csv'
                    }.get(doc_extension, 'application/octet-stream')
                )
                additional_docs_keys.append(generate_presigned_url(S3_BUCKET, doc_key))
        
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        result = accelerator.start_discovery(
            project_name=project_name,
            sow_path=sow_url,
            additional_docs_paths=additional_docs_keys if additional_docs_keys else None,
            project_owner=email
        )
        
        return JSONResponse(status_code=200, content=result)

    except ValueError as ve:
        logger.error(f"ValueError in '/start_discovery': {str(ve)}", exc_info=True)
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(ve)}")
    except HTTPException as http_exc:
        logger.error(f"HTTPException in '/start_discovery': {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.error(f"Error in start_discovery: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@app.post("/process_transcript")
async def process_transcript(request: ProcessTranscriptRequest):
    """
    Process a meeting transcript to extract answers and generate follow-up questions.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        result = accelerator.process_meeting_transcript(
            project_id=request.project_id,
            transcript_text=request.transcript_text
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error in process_transcript: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get_questions/{project_id}")
async def get_questions(project_id: int, status: Optional[str] = None):
    """
    Get current questions for a project, optionally filtered by status.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        questions = accelerator.get_current_questions(
            project_id=project_id,
            status=status
        )
        
        return {
            'status': 'success',
            'questions': questions
        }
    
    except Exception as e:
        logger.error(f"Error in get_questions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/discovery_status/{project_id}")
async def discovery_status(project_id: int):
    """
    Check if the discovery process is complete.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        status = accelerator.is_discovery_complete(project_id)
        
        return {
            'status': 'success',
            'discovery_status': status
        }
    
    except Exception as e:
        logger.error(f"Error in discovery_status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/generate_questions_by_id")
async def generate_questions_by_id(request: GenerateQuestionsByIdRequest):
    """
    Generate questions using only the project ID.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        sow_data = accelerator.db.get_project_sow_data(request.project_id)
        
        if not sow_data:
            return {
                'status': 'error',
                'message': 'SOW data not found for this project'
            }
        
        result = accelerator.generate_questions(
            project_id=request.project_id,
            sow_data=sow_data
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error in generate_questions_by_id: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/get_sow_data/{project_id}")
async def get_sow_data(project_id: int):
    """
    Get SOW data for a specific project.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        sow_data = accelerator.db.get_project_sow_data(project_id)
        
        if not sow_data:
            return {
                'status': 'error',
                'message': 'SOW data not found for this project'
            }
        
        return {
            'status': 'success',
            'project_id': project_id,
            'sow_data': sow_data
        }
    
    except Exception as e:
        logger.error(f"Error in get_sow_data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/discovery_report/{project_id}")
async def discovery_report(project_id: int):
    """
    Generate a comprehensive report of the discovery process.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        report = accelerator.generate_discovery_report(project_id)
        
        return report
    
    except Exception as e:
        logger.error(f"Error in discovery_report: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/process_additional_documents", response_model=ProcessAdditionalDocsResponse)
async def process_additional_documents(request: ProcessAdditionalDocsRequest):
    """
    Process additional documents for an existing project.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        result = accelerator.process_additional_documents(
            project_id=request.project_id,
            doc_urls=request.document_paths
        )
        
        return result
    
    except Exception as e:
        logger.error(f"Error in process_additional_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload_additional_documents/{project_id}", response_model=ProcessAdditionalDocsResponse)
async def upload_additional_documents(
    request: Request,
    project_id: int,
    documents: List[UploadFile] = File(..., description="Additional document files to process"),
):
    """
    Upload and process additional documents for an existing project.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        project = accelerator.db.get_project_info(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        project_name = project['name']
        keycloak_user = request.state.user
        email = keycloak_user.get('email', 'unknown_user')
        s3_prefix = f"discovery_accelerator/uploads/{sanitize_filename(email)}/{sanitize_filename(project_name)}/additional_docs/files/"

        document_keys = []
        failed_uploads = []
        for doc in documents:
            try:
                if not doc.filename:
                    failed_uploads.append({
                        'filename': 'unnamed_file',
                        'error': 'No filename provided'
                    })
                    continue
                doc_filename = sanitize_filename(doc.filename)
                doc_extension = os.path.splitext(doc_filename)[1].lower()
                if doc_extension not in ['.pdf', '.docx', '.pptx', '.txt','.xlsx' , '.xls', '.csv' ]:
                    failed_uploads.append({
                        'filename': doc_filename,
                        'error': f"Unsupported file format: {doc_extension}"
                    })
                    continue
                doc_key = f"{s3_prefix}{doc_filename}"
                content = await doc.read()
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=doc_key,
                    Body=content,
                    ContentType=doc.content_type or {
                        '.pdf': 'application/pdf',
                        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        '.xls': 'application/vnd.ms-excel',
                        '.txt': 'text/plain',
                        '.csv': 'text/csv'
                    }.get(doc_extension, 'application/octet-stream')
                )
                document_keys.append(doc_key)
                logger.info(f"Document {doc_filename} uploaded to S3 at {doc_key}")
            except Exception as upload_error:
                failed_uploads.append({
                    'filename': doc.filename,
                    'error': str(upload_error)
                })
                logger.error(f"Failed to upload {doc.filename}: {str(upload_error)}")
                continue

        if not document_keys:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "No documents could be uploaded successfully",
                    "failed_uploads": failed_uploads
                }
            )

        # Use AdditionalDocumentProcessor to process S3 keys
        processor = AdditionalDocumentProcessor(accelerator.db, S3_BUCKET, GEMINI_API_KEY)
        result = processor.process_additional_documents(project_id, document_keys)

        result['project_name'] = project_name
        result['failed_uploads'] = failed_uploads
        result['total_documents_uploaded'] = len(document_keys)
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in upload_additional_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/project_progress/{project_id}")
async def get_project_progress(project_id: int):
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        progress = accelerator.get_project_progress_summary(project_id)
        if not progress:
            raise HTTPException(status_code=404, detail=f"Progress data not found for project {project_id}")
        return progress
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_project_progress: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/answers_by_source/{project_id}")
async def get_answers_by_source(project_id: int):
    """
    Get all answers grouped by their source.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        answers = accelerator.get_answers_by_source(project_id)
        
        return answers
    
    except Exception as e:
        logger.error(f"Error in get_answers_by_source: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/unanswered_questions/{project_id}")
async def get_unanswered_questions(project_id: int):
    """
    Get all unanswered questions for a project.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        questions = accelerator.get_current_questions(project_id, status='unanswered')
        
        return {
            'status': 'success',
            'project_id': project_id,
            'unanswered_questions': questions,
            'count': len(questions)
        }
    
    except Exception as e:
        logger.error(f"Error in get_unanswered_questions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/bulk_process_additional_documents")
async def bulk_process_additional_documents(
    request: Request,
    project_id: int = Form(...),
    documents: List[UploadFile] = File(...),
):
    """
    Bulk process multiple additional documents at once.
    """
    try:
        accelerator = DiscoveryAccelerator(
            bucket_name=S3_BUCKET,
            gemini_api_key=GEMINI_API_KEY,
            inference_api_url=INFERENCE_API_URL
        )
        
        project = accelerator.db.get_project_info(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        project_name = project['name']
        keycloak_user = request.state.user
        email = keycloak_user.get('email')
        s3_prefix = f"discovery_accelerator/uploads/{sanitize_filename(email)}/{sanitize_filename(project_name)}/bulk_additional_documents/files/batch_{int(time.time())}/"

        document_urls = []
        failed_uploads = []
        
        for doc in documents:
            try:
                if not doc.filename:
                    failed_uploads.append({
                        'filename': 'unnamed_file',
                        'error': 'No filename provided'
                    })
                    continue
                doc_filename = sanitize_filename(doc.filename)
                doc_extension = os.path.splitext(doc_filename)[1].lower()
                if doc_extension not in ['.pdf', '.docx', '.pptx','xlsx' , '.xls', '.csv', '.txt']:
                    failed_uploads.append({
                        'filename': doc_filename,
                        'error': f"Unsupported file format: {doc_extension}"
                    })
                    continue
                doc_key = f"{s3_prefix}{doc_filename}"
                content = await doc.read()
                s3_client.put_object(
                    Bucket=S3_BUCKET,
                    Key=doc_key,
                    Body=content,
                    ContentType=doc.content_type or {
                        '.pdf': 'application/pdf',
                        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        '.xls': 'application/vnd.ms-excel',
                        '.csv': 'text/csv',
                        '.txt': 'text/plain'
                    }.get(doc_extension, 'application/octet-stream')
                )
                presigned_url = generate_presigned_url(S3_BUCKET, doc_key)
                document_urls.append(presigned_url)
                logger.info(f"Saved bulk document: {doc_filename} to S3 at {doc_key}")
            except Exception as upload_error:
                failed_uploads.append({
                    'filename': doc.filename,
                    'error': str(upload_error)
                })
                logger.error(f"Failed to upload {doc.filename}: {str(upload_error)}")
                continue
        
        if not document_urls:
            return {
                'status': 'error',
                'message': 'No documents could be uploaded successfully',
                'failed_uploads': failed_uploads
            }
        
        batch_size = 3
        all_results = {
            'documents_processed': 0,
            'answers_found': 0,
            'new_questions_generated': 0,
            'batch_results': []
        }
        
        for i in range(0, len(document_urls), batch_size):
            batch_urls = document_urls[i:i+batch_size]
            logger.info(f"Processing batch {i//batch_size + 1}: {len(batch_urls)} documents")
            
            try:
                batch_result = accelerator.process_additional_documents(
                    project_id=project_id,
                    doc_urls=batch_urls
                )
                
                if batch_result.get('status') == 'success':
                    all_results['documents_processed'] += batch_result.get('documents_processed', 0)
                    all_results['answers_found'] += batch_result.get('answers_found', 0)
                    all_results['new_questions_generated'] += batch_result.get('new_questions_generated', 0)
                    all_results['batch_results'].append({
                        'batch_number': i//batch_size + 1,
                        'documents': [os.path.basename(p.split('?')[0]) for p in batch_urls],
                        'result': batch_result
                    })
                
                time.sleep(2)
                
            except Exception as batch_error:
                logger.error(f"Error processing batch {i//batch_size + 1}: {str(batch_error)}")
                all_results['batch_results'].append({
                    'batch_number': i//batch_size + 1,
                    'documents': [os.path.basename(p.split('?')[0]) for p in batch_urls],
                    'error': str(batch_error)
                })
        
        final_status = accelerator.get_project_progress_summary(project_id)
        
        return {
            'status': 'success',
            'project_id': project_id,
            'project_name': project_name,
            'total_documents_uploaded': len(document_urls),
            'failed_uploads': failed_uploads,
            'processing_results': all_results,
            'final_discovery_status': final_status.get('discovery_status', {}),
            'completion_percentage': final_status.get('progress', {}).get('completion_percentage', 0)
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk_process_additional_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "project_api:app",
        host="0.0.0.0",
        port=4000,
    )