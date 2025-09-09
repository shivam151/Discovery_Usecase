# import os
# import io
# import logging
# import psutil
# from typing import List, Dict, Any
# import fitz  # PyMuPDF for PDF processing
# from docx import Document
# from pptx import Presentation
# from PIL import Image
# import numpy as np
# import requests
# import base64
# import google.generativeai as genai
# import json
# import time
# import boto3
# import gc
# from botocore.exceptions import ClientError
# from dotenv import load_dotenv
# from discovery_db_postgresql import DiscoveryDatabase
# from psycopg2.extras import RealDictCursor
# import re
# try:
#     import magic  # python-magic for file type detection
# except ImportError:
#     magic = None
#     logging.getLogger(__name__).warning("python-magic not available, falling back to extension-based file type detection")

# # Load environment variables from .env file
# load_dotenv()

# # Set up Gemini API
# genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))

# def get_memory_usage():
#     """Get current memory usage of the process"""
#     process = psutil.Process(os.getpid())
#     return process.memory_info().rss / 1024 / 1024  # in MB

# class ProjectDataPipeline:
#     def __init__(self, bucket_name: str, inference_api_url: str, gemini_api_key: str = None):
#         """Initialize the ProjectDataPipeline with S3 and Gemini configurations"""
#         self.inference_api_url = inference_api_url.rstrip('/')
#         self.gemini_api_key = gemini_api_key
#         self.bucket_name = bucket_name
        
#         # Initialize S3 client
#         self.s3_client = boto3.client(
#             's3',
#             aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
#             aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
#             region_name=os.getenv('AWS_REGION')
#         )
        
#         # Setup logging
#         logging.basicConfig(level=logging.INFO)
#         self.logger = logging.getLogger(__name__)
#         self.logger.info(f"Initial RAM usage: {get_memory_usage():.2f} MB")
        
#         self.logger.info(f"Using inference API at: {inference_api_url}")
#         self.logger.info(f"Using S3 bucket: {bucket_name}")
    
#     def clear_memory(self):
#         """Clear system memory"""
#         gc.collect()
#         memory_mb = get_memory_usage()
#         self.logger.info(f"Current RAM usage: {memory_mb:.2f} MB")
    
#     def preprocess_image(self, image: Image.Image, max_size: int = 384) -> Image.Image:
#         """Resize image to reduce memory usage while maintaining aspect ratio"""
#         try:
#             if image.mode != 'RGB':
#                 image = image.convert('RGB')
            
#             ratio = max_size / max(image.size)
#             if ratio < 1:
#                 new_size = tuple(int(dim * ratio) for dim in image.size)
#                 image = image.resize(new_size, Image.Resampling.LANCZOS)
            
#             return image
#         except Exception as e:
#             self.logger.error(f"Error preprocessing image: {str(e)}")
#             return image
    
#     def process_image(self, image: Image.Image) -> np.ndarray:
#         """Process image using the inference API"""
#         try:
#             image = self.preprocess_image(image)
#             buffered = io.BytesIO()
#             image.save(buffered, format="PNG")
#             img_str = base64.b64encode(buffered.getvalue()).decode()
            
#             response = requests.post(
#                 f"{self.inference_api_url}/process_image",
#                 json={'image': img_str}
#             )
            
#             if response.status_code != 200:
#                 raise Exception(f"API Error: {response.text}")
            
#             result = response.json()
#             return np.array(result['embedding'])
        
#         except Exception as e:
#             self.logger.error(f"Error in process_image: {str(e)}")
#             return np.zeros((1, 384), dtype=np.float32)
    
#     def get_text_embedding(self, text: str) -> np.ndarray:
#         """Get text embedding from the inference API"""
#         try:
#             self.logger.info(f"Getting text embedding for text of length {len(text)}")
#             response = requests.post(
#                 f"{self.inference_api_url}/embed_text",
#                 json={'text': text}
#             )
            
#             if response.status_code == 200:
#                 result = response.json()
#                 self.logger.info("Successfully got embedding from API")
#                 return np.array(result['embedding'])
#             else:
#                 raise Exception(f"API Error ({response.status_code}): {response.text}")
#         except Exception as e:
#             self.logger.error(f"Error getting text embedding: {str(e)}")
#             return np.zeros((384,), dtype=np.float32)  # Keep zero-vector fallback
    
#     def create_embeddings(self, documents: List[Dict[str, Any]]) -> Dict[str, List]:
#         """Create embeddings for text and images with memory management"""
#         self.logger.info(f"Creating embeddings for {len(documents)} documents")
#         embeddings = []
#         metadatas = []
#         ids = []
        
#         batch_size = 10
#         for idx in range(0, len(documents), batch_size):
#             batch = documents[idx:idx + batch_size]
#             self.logger.info(f"Processing batch {idx//batch_size + 1}/{(len(documents)-1)//batch_size + 1}")
            
#             for doc_idx, doc in enumerate(batch):
#                 try:
#                     if doc['type'] == 'text':
#                         self.logger.info(f"Creating text embedding for document {idx + doc_idx} from source {doc['source']}")
#                         text_content = doc['content']
#                         if isinstance(text_content, str) and len(text_content) > 0:
#                             embedding = self.get_text_embedding(text_content).tolist()
#                         else:
#                             self.logger.warning(f"Empty or invalid text content in document {idx + doc_idx}")
#                             continue
#                     else:  # image
#                         self.logger.info(f"Processing image embedding for document {idx + doc_idx} from source {doc['source']}")
#                         embedding = doc['content'].flatten().tolist()
                    
#                     if embedding and len(embedding) > 0:
#                         embeddings.append(embedding)
#                         metadatas.append({
#                             'source': doc['source'],
#                             'type': doc['type']
#                         })
#                         ids.append(f"doc_{idx + doc_idx}")
#                         self.logger.info(f"Successfully created embedding for document {idx + doc_idx}")
#                     else:
#                         self.logger.warning(f"Skipping document {idx + doc_idx} due to empty embedding")
#                 except Exception as e:
#                     self.logger.error(f"Error creating embedding for document {idx + doc_idx}: {str(e)}")
#                     continue
            
#             self.clear_memory()
        
#         self.logger.info(f"Successfully created {len(embeddings)} embeddings")
        
#         result = {
#             'embeddings': embeddings,
#             'metadatas': metadatas,
#             'ids': ids
#         }
        
#         if not embeddings:
#             self.logger.warning("No embeddings were created!")
        
#         return result
    
#     def answer_question_with_gemini(self, question: str, document_content: str) -> str:
#         """Use Gemini to answer a question based on document content"""
#         try:
#             model = genai.GenerativeModel('gemini-2.0-flash')
#             prompt = f"""
#             Based on the following document content, answer the question: {question}
            
#             Document Content:
#             {document_content[:10000]}  # Limit to avoid token limits
            
#             Answer:
#             """
#             response = model.generate_content(prompt)
#             return response.text.strip()
#         except Exception as e:
#             self.logger.error(f"Error answering question with Gemini: {str(e)}")
#             return f"Error answering question: {str(e)}"
    
#     def process_project(self, project_name: str, s3_prefix: str, project_owner: str, sow_data: Dict) -> Dict:
#         """Process all files in an S3 prefix with memory management"""
#         self.logger.info(f"\n==== Processing project: {project_name} ====\nS3 Prefix: {s3_prefix}")
        
#         documents = []
#         document_requirement_matches = {}
        
#         # List objects in S3 prefix
#         try:
#             response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=s3_prefix)
#             if 'Contents' not in response:
#                 self.logger.warning(f"No files found in S3 prefix: {s3_prefix}")
#                 return {'document_requirement_matches': {}, 'documents': []}
            
#             files = [obj['Key'] for obj in response.get('Contents', [])]
#             self.logger.info(f"Found {len(files)} files to process: {[os.path.basename(f) for f in files]}")
            
#             for s3_key in files:
#                 try:
#                     result = self.parse_file(s3_key, sow_data)
#                     if result:
#                         documents.append({
#                             'content': result['extracted_content'],
#                             'type': 'text',
#                             'source': s3_key
#                         })
#                         if 'requirement_matches' in result:
#                             document_requirement_matches.update(result['requirement_matches'])
#                     self.clear_memory()
#                 except Exception as e:
#                     self.logger.error(f"Error processing file {s3_key}: {str(e)}")
#                     continue
            
#             # Create embeddings but do not store in ChromaDB
#             if documents:
#                 embed_data = self.create_embeddings(documents)
#                 self.logger.info(f"Created embeddings for compatibility, but not storing in ChromaDB")
            
#             self.logger.info(f"\n==== Completed processing project: {project_name} ====\n")
#             return {'document_requirement_matches': document_requirement_matches, 'documents': documents}
        
#         except Exception as e:
#             self.logger.error(f"Error processing project {project_name}: {str(e)}")
#             return {'document_requirement_matches': {}, 'documents': []}
    
#     def _get_s3_object(self, s3_key: str) -> bytes:
#         """Retrieve an object from S3"""
#         try:
#             response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
#             return response['Body'].read()
#         except ClientError as e:
#             self.logger.error(f"Error retrieving S3 object {s3_key}: {str(e)}")
#             raise Exception(f"Failed to retrieve S3 object: {str(e)}")
    
#     def parse_file(self, s3_key: str, sow_data: Dict) -> Dict:
#         """Process a file stored in S3"""
#         self.logger.info(f"\nProcessing S3 file: {s3_key}")
#         self.logger.info(f"Current RAM usage: {get_memory_usage():.2f} MB")
        
#         try:
#             # Fetch file from S3
#             file_data = self._get_s3_object(s3_key)
#             file_extension = os.path.splitext(s3_key)[1].lower()
            
#             # Process based on file extension
#             if file_extension not in ['.pdf', '.docx', '.pptx']:
#                 self.logger.warning(f"Unsupported file type {file_extension} for {s3_key}")
#                 return {}
            
#             # Detect MIME type
#             mime_type = None
#             if magic:
#                 try:
#                     mime_detector = magic.Magic(mime=True)
#                     mime_type = mime_detector.from_buffer(file_data[:1024])
#                     self.logger.info(f"Detected MIME type: {mime_type} for file: {s3_key}")
#                 except Exception as e:
#                     self.logger.warning(f"Failed to detect MIME type with python-magic: {str(e)}")
#                     mime_type = None
            
#             # Fallback to extension-based detection if MIME type detection fails
#             if not mime_type:
#                 self.logger.info("Using file extension for type detection")
#                 if file_extension == '.pdf':
#                     mime_type = 'application/pdf'
#                 elif file_extension == '.docx':
#                     mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
#                 elif file_extension == '.pptx':
#                     mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
#                 else:
#                     raise ValueError(f"Unsupported file extension: {file_extension} for file: {s3_key}")
            
#             # Validate MIME type for DOCX
#             if file_extension == '.docx' and mime_type == 'application/zip':
#                 self.logger.info(f"Detected application/zip for .docx file, validating DOCX structure")
#                 try:
#                     Document(io.BytesIO(file_data))
#                     self.logger.info("File confirmed as valid DOCX despite application/zip MIME type")
#                 except Exception as e:
#                     self.logger.error(f"File is not a valid DOCX: {str(e)}")
#                     raise ValueError(f"File is not a valid DOCX (MIME type: {mime_type}, file: {s3_key}): {str(e)}")
            
#             mime_map = {
#                 '.pdf': ['application/pdf'],
#                 '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
#                 '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation']
#             }
#             if mime_type not in mime_map.get(file_extension, []):
#                 raise ValueError(f"File is not a valid {file_extension.upper()[1:]} (MIME type: {mime_type}, file: {s3_key})")
            
#             requirements = sow_data.get('requirements', [])
            
#             if file_extension == '.pdf':
#                 extracted_info = extract_text_with_gemini_chunked(
#                     file_data,
#                     self.gemini_api_key,
#                     sow_data,
#                     max_pages_per_chunk=30,
#                     is_stream=True
#                 )
#             elif file_extension == '.docx':
#                 doc = Document(io.BytesIO(file_data))
#                 text = "\n".join([para.text for para in doc.paragraphs if para.text])
#                 extracted_info = self._process_text_with_gemini(text, sow_data)
#             elif file_extension == '.pptx':
#                 prs = Presentation(io.BytesIO(file_data))
#                 text = "\n".join([shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, 'text') and shape.text])
#                 extracted_info = self._process_text_with_gemini(text, sow_data)
            
#             requirement_matches = match_requirements_to_document(requirements, extracted_info)
            
#             result = {
#                 'extracted_content': extracted_info,
#                 'requirement_matches': requirement_matches,
#                 'source_file': s3_key,
#                 'processing_method': 'chunked_extraction' if isinstance(extracted_info, dict) and extracted_info.get('extraction_type', '').startswith('chunked') else 'single_extraction'
#             }
            
#             return result
        
#         except Exception as e:
#             self.logger.error(f"Error processing S3 file {s3_key}: {str(e)}")
#             return {}
#         finally:
#             self.clear_memory()
    
#     def _process_text_with_gemini(self, text: str, sow_data: Dict) -> Dict:
#         """Process extracted text with Gemini API to extract structured information"""
#         try:
#             model = genai.GenerativeModel('gemini-2.0-flash')
#             requirements_text = ""
#             if sow_data and 'requirements' in sow_data:
#                 for req in sow_data['requirements'][:10]:
#                     requirements_text += f"- {req.get('id', '')}: {req.get('text', '')}\n"
            
#             prompt = f"""
#             Extract all the text and understanding from this document content.
            
#             Additionally, I'm providing a list of key requirements from the SOW.
#             In your analysis, please identify any content in this document that relates to these requirements:
            
#             {requirements_text}
            
#             Format the output as structured JSON with:
#             1. Clear sections of the document content
#             2. Any key information related to the requirements
#             3. Important technical details and specifications
#             """
            
#             response = model.generate_content(prompt + f"\n\nDocument Content:\n{text[:10000]}")
#             return json.loads(response.text.strip('```json\n').strip('```'))
#         except Exception as e:
#             self.logger.error(f"Error processing text with Gemini: {str(e)}")
#             return {"error": f"Could not process text: {str(e)}"}
    
#     # def list_projects(self, email: str) -> List[Dict]:
#     #     """List projects for a given user"""
#     #     try:
#     #         db = DiscoveryDatabase()
#     #         conn = db._get_connection()
#     #         cursor = conn.cursor(cursor_factory=RealDictCursor)
#     #         cursor.execute("SELECT id, name FROM projects WHERE project_owner=%s", (email,))
#     #         projects = cursor.fetchall()
#     #         cursor.close()
#     #         db.release_connection(conn)
#     #         return projects
#     #     except Exception as e:
#     #         self.logger.error(f"Error listing projects: {str(e)}")
#     #         return []
    
#     # def delete_project(self, project_id: int):
#     #     """Delete a project and its associated data"""
#     #     db = DiscoveryDatabase()
#     #     conn = db._get_connection()
#     #     cursor = conn.cursor()
        
#     #     cursor.execute("SELECT id, name FROM projects WHERE id = %s", (project_id,))
#     #     project = cursor.fetchone()
#     #     if not project:
#     #         cursor.close()
#     #         db.release_connection(conn)
#     #         return None
        
#     #     cursor.execute(""" 
#     #         SELECT table_name FROM information_schema.tables 
#     #         WHERE table_schema = 'public' AND table_name = 'requirement_matches'
#     #     """)
#     #     if cursor.fetchone():
#     #         cursor.execute("DELETE FROM requirement_matches WHERE project_id = %s", (project_id,))
        
#     #     cursor.execute("DELETE FROM sow_data WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM answers WHERE question_id IN (SELECT id FROM questions WHERE project_id = %s)", (project_id,))
#     #     cursor.execute("DELETE FROM document_answers WHERE document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
#     #     cursor.execute("DELETE FROM document_questions WHERE source_document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
#     #     cursor.execute("DELETE FROM document_processing_log WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM new_information WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM transcripts WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM additional_documents WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM questions WHERE project_id = %s", (project_id,))
#     #     cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        
#     #     conn.commit()
#     #     cursor.close()
#     #     db.release_connection(conn)
        
#     #     return project_id,

# def chunk_pdf(pdf_data: bytes, max_pages_per_chunk: int = 20) -> List[bytes]:
#     """Split a PDF stream into smaller chunks based on page count"""
#     chunk_data = []
    
#     try:
#         source_doc = fitz.open(stream=pdf_data, filetype="pdf")
#         total_pages = len(source_doc)
        
#         if total_pages <= max_pages_per_chunk:
#             source_doc.close()
#             return [pdf_data]
        
#         chunk_number = 1
#         for start_page in range(0, total_pages, max_pages_per_chunk):
#             end_page = min(start_page + max_pages_per_chunk - 1, total_pages - 1)
#             chunk_doc = fitz.open()
            
#             for page_num in range(start_page, end_page + 1):
#                 chunk_doc.insert_pdf(source_doc, from_page=page_num, to_page=page_num)
            
#             output_stream = io.BytesIO()
#             chunk_doc.save(output_stream)
#             chunk_data.append(output_stream.getvalue())
#             chunk_doc.close()
            
#             chunk_number += 1
        
#         source_doc.close()
        
#     except Exception as e:
#         logging.getLogger(__name__).error(f"Error chunking PDF: {str(e)}")
#         return [pdf_data]
    
#     return chunk_data

# def extract_text_with_gemini_chunked(pdf_data: bytes, gemini_api_key: str, sow_data: Dict, max_pages_per_chunk: int = 20, is_stream: bool = False) -> Dict:
#     """Use Gemini to extract structured information from a PDF stream with chunking support"""
#     genai.configure(api_key=gemini_api_key)
#     model = genai.GenerativeModel('gemini-2.0-flash')
    
#     try:
#         doc = fitz.open(stream=pdf_data, filetype="pdf")
#         total_pages = len(doc)
#         doc.close()
#     except Exception as e:
#         return {"error": f"Could not read PDF: {str(e)}"}
    
#     logging.getLogger(__name__).info(f"PDF has {total_pages} pages")
    
#     chunk_data = chunk_pdf(pdf_data, max_pages_per_chunk)
    
#     requirements_text = ""
#     if sow_data and 'requirements' in sow_data:
#         for req in sow_data['requirements'][:10]:
#             requirements_text += f"- {req.get('id', '')}: {req.get('text', '')}\n"
    
#     all_extractions = []
    
#     for i, chunk in enumerate(chunk_data):
#         logging.getLogger(__name__).info(f"Processing chunk {i + 1}/{len(chunk_data)}")
        
#         chunk_prompt = f"""
#         Extract all the text and understanding from this document chunk (part {i + 1} of {len(chunk_data)}).
#         For diagrams, properly extract the text and understanding from them.
        
#         {"This is part of a larger document that was split into chunks for processing." if len(chunk_data) > 1 else ""}
        
#         Additionally, I'm providing a list of key requirements from the SOW. 
#         In your analysis, please identify any content in this document chunk that relates to these requirements:
        
#         {requirements_text}
        
#         Format the output as structured JSON with:
#         1. Clear sections of the document content
#         2. Any key information related to the requirements
#         3. Important technical details and specifications
#         4. Page range information for this chunk
        
#         Begin your response with valid JSON.
#         """
        
#         try:
#             response = model.generate_content([
#                 chunk_prompt,
#                 {'mime_type': 'application/pdf', 'data': chunk}
#             ])
            
#             chunk_result = {
#                 'chunk_number': i + 1,
#                 'total_chunks': len(chunk_data),
#                 'extraction': response.text
#             }
            
#             all_extractions.append(chunk_result)
#             logging.getLogger(__name__).info(f"Successfully processed chunk {i + 1}")
            
#             if i < len(chunk_data) - 1:
#                 time.sleep(2)
                
#         except Exception as e:
#             logging.getLogger(__name__).error(f"Error processing chunk {i + 1}: {str(e)}")
#             error_result = {
#                 'chunk_number': i + 1,
#                 'total_chunks': len(chunk_data),
#                 'error': str(e)
#             }
#             all_extractions.append(error_result)
    
#     if len(chunk_data) > 1:
#         logging.getLogger(__name__).info("Creating combined summary from all chunks...")
#         try:
#             combined_result = create_combined_summary(all_extractions, model, requirements_text)
#             return combined_result
#         except Exception as e:
#             logging.getLogger(__name__).error(f"Error creating combined summary: {str(e)}")
#             return {
#                 'extraction_type': 'chunked_individual',
#                 'total_chunks': len(chunk_data),
#                 'chunks': all_extractions
#             }
#     else:
#         return json.loads(all_extractions[0]['extraction'].strip('```json\n').strip('```')) if all_extractions else {"error": "No extraction results"}

# def create_combined_summary(chunk_extractions: List[Dict], model, requirements_text: str) -> Dict:
#     """Create a combined summary from multiple chunk extractions"""
#     try:
#         combined_content = ""
#         successful_chunks = []
        
#         for chunk in chunk_extractions:
#             if 'extraction' in chunk:
#                 combined_content += f"\n--- CHUNK {chunk['chunk_number']} ---\n"
#                 combined_content += chunk['extraction']
#                 combined_content += "\n"
#                 successful_chunks.append(chunk['chunk_number'])
#             elif 'error' in chunk:
#                 combined_content += f"\n--- CHUNK {chunk['chunk_number']} ERROR ---\n"
#                 combined_content += f"Error: {chunk['error']}\n"
        
#         summary_prompt = f"""
#         The following content was extracted from a large PDF document that was split into {len(chunk_extractions)} chunks.
#         Please create a comprehensive, unified summary that combines all the information coherently.
        
#         Remove redundancies, organize the information logically, and ensure the final summary captures all key points.
        
#         Pay special attention to these SOW requirements when creating the summary:
#         {requirements_text}
        
#         EXTRACTED CONTENT FROM ALL CHUNKS:
#         {combined_content}
        
#         Please provide a unified, structured summary as JSON with:
#         1. Overall document summary
#         2. Key sections and their content
#         3. Requirements mapping (which content relates to which SOW requirements)
#         4. Technical specifications and important details
#         5. Processing metadata (successful chunks, any errors)
        
#         Begin your response with valid JSON.
#         """
        
#         response = model.generate_content(summary_prompt)
        
#         return {
#             'extraction_type': 'chunked_combined',
#             'total_chunks': len(chunk_extractions),
#             'successful_chunks': successful_chunks,
#             'combined_summary': response.text,
#             'individual_chunks': chunk_extractions
#         }
        
#     except Exception as e:
#         logging.getLogger(__name__).error(f"Error in create_combined_summary: {str(e)}")
#         return {
#             'extraction_type': 'chunked_individual_fallback',
#             'total_chunks': len(chunk_extractions),
#             'error': f"Summary creation failed: {str(e)}",
#             'chunks': chunk_extractions
#         }

# def match_requirements_to_document(requirements: List[Dict], document_content: Any) -> Dict:
#     """Match requirements to document content to find supporting sections"""
#     requirement_matches = {}
    
#     try:
#         if isinstance(document_content, dict):
#             document_text = json.dumps(document_content)
#         else:
#             document_text = str(document_content)
        
#         for req in requirements:
#             req_id = req.get('id', '')
#             req_text = req.get('text', '')
            
#             if not req_text.strip():
#                 continue
            
#             keywords = extract_keywords(req_text)
#             matches = []
#             for keyword in keywords:
#                 if len(keyword) < 4:
#                     continue
                
#                 pattern = re.compile(r'(.{0,100}' + re.escape(keyword) + r'.{0,100})', re.IGNORECASE)
#                 for match in pattern.finditer(document_text):
#                     context = match.group(1).strip()
#                     if context:
#                         matches.append({
#                             'keyword': keyword,
#                             'context': context
#                         })
            
#             if matches:
#                 requirement_matches[req_id] = matches
        
#         return requirement_matches
    
#     except Exception as e:
#         logging.getLogger(__name__).error(f"Error matching requirements to document: {str(e)}")
#         return {}

# def extract_keywords(text: str) -> List[str]:
#     """Extract keywords and phrases from requirement text"""
#     keywords = []
#     stop_words = ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 'shall', 'will', 'should', 'must']
    
#     words = text.lower().split()
#     filtered_words = [word.strip(',.()[]{}:;\'\"') for word in words if word.lower() not in stop_words and len(word) > 3]
#     keywords.extend(filtered_words)
    
#     for i in range(len(words) - 1):
#         phrase = words[i] + ' ' + words[i+1]
#         keywords.append(phrase.strip(',.()[]{}:;\'\"'))
    
#     for i in range(len(words) - 2):
#         phrase = words[i] + ' ' + words[i+1] + ' ' + words[i+2]
#         keywords.append(phrase.strip(',.()[]{}:;\'\"'))
    
#     keywords = list(set(keywords))
    
#     return keywords





import os
import io
import logging
import psutil
from typing import List, Dict, Any
import fitz  # PyMuPDF for PDF processing
from docx import Document
from pptx import Presentation
from PIL import Image
import numpy as np
import requests
import base64
import google.generativeai as genai
import json
import time
import boto3
import gc
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from discovery_db_postgresql import DiscoveryDatabase
from psycopg2.extras import RealDictCursor
import re
try:
    import magic  # python-magic for file type detection
except ImportError:
    magic = None
    logging.getLogger(__name__).warning("python-magic not available, falling back to extension-based file type detection")

# Load environment variables from .env file
load_dotenv()

# Set up Gemini API
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))

def get_memory_usage():
    """Get current memory usage of the process"""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / 1024 / 1024  # in MB

class ProjectDataPipeline:
    def __init__(self, bucket_name: str, inference_api_url: str, gemini_api_key: str = None):
        """Initialize the ProjectDataPipeline with S3 and Gemini configurations"""
        self.inference_api_url = inference_api_url.rstrip('/')
        self.gemini_api_key = gemini_api_key
        self.bucket_name = bucket_name
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION')
        )
        
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.logger.info(f"Initial RAM usage: {get_memory_usage():.2f} MB")
        
        self.logger.info(f"Using inference API at: {inference_api_url}")
        self.logger.info(f"Using S3 bucket: {bucket_name}")
    
    def clear_memory(self):
        """Clear system memory"""
        gc.collect()
        memory_mb = get_memory_usage()
        self.logger.info(f"Current RAM usage: {memory_mb:.2f} MB")
    
    def preprocess_image(self, image: Image.Image, max_size: int = 384) -> Image.Image:
        """Resize image to reduce memory usage while maintaining aspect ratio"""
        try:
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            ratio = max_size / max(image.size)
            if ratio < 1:
                new_size = tuple(int(dim * ratio) for dim in image.size)
                image = image.resize(new_size, Image.Resampling.LANCZOS)
            
            return image
        except Exception as e:
            self.logger.error(f"Error preprocessing image: {str(e)}")
            return image
    
    def process_image(self, image: Image.Image) -> np.ndarray:
        """Process image using the inference API"""
        try:
            image = self.preprocess_image(image)
            buffered = io.BytesIO()
            image.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            
            response = requests.post(
                f"{self.inference_api_url}/process_image",
                json={'image': img_str}
            )
            
            if response.status_code != 200:
                raise Exception(f"API Error: {response.text}")
            
            result = response.json()
            return np.array(result['embedding'])
        
        except Exception as e:
            self.logger.error(f"Error in process_image: {str(e)}")
            return np.zeros((1, 384), dtype=np.float32)
    
    def get_text_embedding(self, text: str) -> np.ndarray:
        """Get text embedding from the inference API"""
        try:
            self.logger.info(f"Getting text embedding for text of length {len(text)}")
            response = requests.post(
                f"{self.inference_api_url}/embed_text",
                json={'text': text}
            )
            
            if response.status_code == 200:
                result = response.json()
                self.logger.info("Successfully got embedding from API")
                return np.array(result['embedding'])
            else:
                raise Exception(f"API Error ({response.status_code}): {response.text}")
        except Exception as e:
            self.logger.error(f"Error getting text embedding: {str(e)}")
            return np.zeros((384,), dtype=np.float32)  # Keep zero-vector fallback
    
    def create_embeddings(self, documents: List[Dict[str, Any]]) -> Dict[str, List]:
        """Create embeddings for text and images with memory management"""
        self.logger.info(f"Creating embeddings for {len(documents)} documents")
        embeddings = []
        metadatas = []
        ids = []
        
        batch_size = 10
        for idx in range(0, len(documents), batch_size):
            batch = documents[idx:idx + batch_size]
            self.logger.info(f"Processing batch {idx//batch_size + 1}/{(len(documents)-1)//batch_size + 1}")
            
            for doc_idx, doc in enumerate(batch):
                try:
                    if doc['type'] == 'text':
                        self.logger.info(f"Creating text embedding for document {idx + doc_idx} from source {doc['source']}")
                        text_content = doc['content']
                        if isinstance(text_content, str) and len(text_content) > 0:
                            embedding = self.get_text_embedding(text_content).tolist()
                        else:
                            self.logger.warning(f"Empty or invalid text content in document {idx + doc_idx}")
                            continue
                    else:  # image
                        self.logger.info(f"Processing image embedding for document {idx + doc_idx} from source {doc['source']}")
                        embedding = doc['content'].flatten().tolist()
                    
                    if embedding and len(embedding) > 0:
                        embeddings.append(embedding)
                        metadatas.append({
                            'source': doc['source'],
                            'type': doc['type']
                        })
                        ids.append(f"doc_{idx + doc_idx}")
                        self.logger.info(f"Successfully created embedding for document {idx + doc_idx}")
                    else:
                        self.logger.warning(f"Skipping document {idx + doc_idx} due to empty embedding")
                except Exception as e:
                    self.logger.error(f"Error creating embedding for document {idx + doc_idx}: {str(e)}")
                    continue
            
            self.clear_memory()
        
        self.logger.info(f"Successfully created {len(embeddings)} embeddings")
        
        result = {
            'embeddings': embeddings,
            'metadatas': metadatas,
            'ids': ids
        }
        
        if not embeddings:
            self.logger.warning("No embeddings were created!")
        
        return result
    
    def answer_question_with_gemini(self, question: str, document_content: str) -> str:
        """Use Gemini to answer a question based on document content"""
        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
            prompt = f"""
            Based on the following document content, answer the question: {question}
            
            Document Content:
            {document_content[:10000]}  # Limit to avoid token limits
            
            Answer:
            """
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            self.logger.error(f"Error answering question with Gemini: {str(e)}")
            return f"Error answering question: {str(e)}"
    
    # def process_project(self, project_name: str, s3_prefix: str, project_owner: str, sow_data: Dict) -> Dict:
    #     """Process all files in an S3 prefix with memory management"""
    #     self.logger.info(f"\n==== Processing project: {project_name} ====\nS3 Prefix: {s3_prefix}")
        
    #     documents = []
    #     document_requirement_matches = {}

        
    #     # List objects in S3 prefix
    #     try:
    #         response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=s3_prefix)
    #         if 'Contents' not in response:
    #             self.logger.warning(f"No files found in S3 prefix: {s3_prefix}")
    #             return {'document_requirement_matches': {}, 'documents': []}
            
    #         files = [obj['Key'] for obj in response.get('Contents', [])]
    #         self.logger.info(f"Found {len(files)} files to process: {[os.path.basename(f) for f in files]}")
            
    #         for s3_key in files:
    #             try:
    #                 result = self.parse_file(s3_key, sow_data)
    #                 if result:
    #                     documents.append({
    #                         'content': result['extracted_content'],
    #                         'type': 'text',
    #                         'source': s3_key
    #                     })
    #                     if 'requirement_matches' in result:
    #                         document_requirement_matches.update(result['requirement_matches'])
    #                 self.clear_memory()
    #             except Exception as e:
    #                 self.logger.error(f"Error processing file {s3_key}: {str(e)}")
    #                 continue
            
    #         # Create embeddings but do not store in ChromaDB
    #         if documents:
    #             embed_data = self.create_embeddings(documents)
    #             self.logger.info(f"Created embeddings for compatibility, but not storing in ChromaDB")
            
    #         self.logger.info(f"\n==== Completed processing project: {project_name} ====\n")
    #         return {'document_requirement_matches': document_requirement_matches, 'documents': documents}
        
    #     except Exception as e:
    #         self.logger.error(f"Error processing project {project_name}: {str(e)}")
    #         return {'document_requirement_matches': {}, 'documents': []}
    def process_project(self, project_name: str, s3_prefix: str, project_owner: str, sow_data: Dict) -> Dict:
        """Process all files in an S3 prefix with memory management"""
        self.logger.info(f"\n==== Processing project: {project_name} ====\nS3 Prefix: {s3_prefix}")
        
        documents = []
        document_requirement_matches = {}

        # Validate s3_prefix
        if not isinstance(s3_prefix, str) or not s3_prefix.strip():
            self.logger.error(f"Invalid S3 prefix: {s3_prefix}. It must be a non-empty string.")
            return {'document_requirement_matches': {}, 'documents': []}

        # List objects in S3 prefix
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=s3_prefix)
            if 'Contents' not in response:
                self.logger.warning(f"No files found in S3 prefix: {s3_prefix}")
                return {'document_requirement_matches': {}, 'documents': []}
            
            files = [obj['Key'] for obj in response.get('Contents', [])]
            self.logger.info(f"Found {len(files)} files to process: {[os.path.basename(f) for f in files]}")
            
            for s3_key in files:
                try:
                    result = self.parse_file(s3_key, sow_data)
                    if result:
                        documents.append({
                            'content': result['extracted_content'],
                            'type': 'text',
                            'source': s3_key
                        })
                        if 'requirement_matches' in result:
                            document_requirement_matches.update(result['requirement_matches'])
                    self.clear_memory()
                except Exception as e:
                    self.logger.error(f"Error processing file {s3_key}: {str(e)}")
                    continue
            
            # Create embeddings but do not store in ChromaDB
            if documents:
                embed_data = self.create_embeddings(documents)
                self.logger.info(f"Created embeddings for compatibility, but not storing in ChromaDB")
            
            self.logger.info(f"\n==== Completed processing project: {project_name} ====\n")
            return {'document_requirement_matches': document_requirement_matches, 'documents': documents}
        
        except Exception as e:
            self.logger.error(f"Error processing project {project_name}: {str(e)}")
            return {'document_requirement_matches': {}, 'documents': []}
        
    def _get_s3_object(self, s3_key: str) -> bytes:
        """Retrieve an object from S3"""
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            return response['Body'].read()
        except ClientError as e:
            self.logger.error(f"Error retrieving S3 object {s3_key}: {str(e)}")
            raise Exception(f"Failed to retrieve S3 object: {str(e)}")
    
    def parse_file(self, s3_key: str, sow_data: Dict) -> Dict:
        """Process a file stored in S3"""
        self.logger.info(f"\nProcessing S3 file: {s3_key}")
        self.logger.info(f"Current RAM usage: {get_memory_usage():.2f} MB")
        
        try:
            # Fetch file from S3
            file_data = self._get_s3_object(s3_key)
            file_extension = os.path.splitext(s3_key)[1].lower()
            
            # Process based on file extension
            if file_extension not in ['.pdf', '.docx', '.pptx', '.txt','xlsx','.xls']:
                self.logger.warning(f"Unsupported file type {file_extension} for {s3_key}")
                return {}
            
            # Detect MIME type
            mime_type = None
            if magic:
                try:
                    mime_detector = magic.Magic(mime=True)
                    mime_type = mime_detector.from_buffer(file_data[:1024])
                    self.logger.info(f"Detected MIME type: {mime_type} for file: {s3_key}")
                except Exception as e:
                    self.logger.warning(f"Failed to detect MIME type with python-magic: {str(e)}")
                    mime_type = None
            
            # Fallback to extension-based detection if MIME type detection fails
            if not mime_type:
                self.logger.info("Using file extension for type detection")
                if file_extension == '.pdf':
                    mime_type = 'application/pdf'
                elif file_extension == '.docx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif file_extension == '.pptx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                elif file_extension == '.xlsx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                elif file_extension == '.xls':
                    mime_type = 'application/vnd.ms-excel'
                elif file_extension == '.csv':
                    mime_type = 'text/csv'
                elif file_extension == '.txt':
                    mime_type = 'text/plain'
                else:
                    raise ValueError(f"Unsupported file extension: {file_extension} for file: {s3_key}")
            
            # Validate MIME type for DOCX
            if file_extension == '.docx' and mime_type == 'application/zip':
                self.logger.info(f"Detected application/zip for .docx file, validating DOCX structure")
                try:
                    Document(io.BytesIO(file_data))
                    self.logger.info("File confirmed as valid DOCX despite application/zip MIME type")
                except Exception as e:
                    self.logger.error(f"File is not a valid DOCX: {str(e)}")
                    raise ValueError(f"File is not a valid DOCX (MIME type: {mime_type}, file: {s3_key}): {str(e)}")
            
            mime_map = {
                '.pdf': ['application/pdf'],
                '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
                '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
                '.txt': ['text/plain'],
                '.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
                '.xls':['application/vnd.ms-excel'],
                '.csv':['text/csv'],
            }
            if mime_type not in mime_map.get(file_extension, []):
                raise ValueError(f"File is not a valid {file_extension.upper()[1:]} (MIME type: {mime_type}, file: {s3_key})")
            
            requirements = sow_data.get('requirements', [])
            
            if file_extension == '.pdf':
                extracted_info = extract_text_with_gemini_chunked(
                    file_data,
                    self.gemini_api_key,
                    sow_data,
                    max_pages_per_chunk=30,
                    is_stream=True
                )
            elif file_extension == '.docx':
                doc = Document(io.BytesIO(file_data))
                text = "\n".join([para.text for para in doc.paragraphs if para.text])
                extracted_info = self._process_text_with_gemini(text, sow_data)
            elif file_extension == '.pptx':
                prs = Presentation(io.BytesIO(file_data))
                text = "\n".join([shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, 'text') and shape.text])
                extracted_info = self._process_text_with_gemini(text, sow_data)
            elif file_extension == '.xlsx':
                # Process XLSX file
                workbook = openpyxl.load_workbook(io.BytesIO(file_data))
                text_parts = []
                for sheet_name in workbook.sheetnames:
                    sheet = workbook[sheet_name]
                    text_parts.append(f"Sheet: {sheet_name}")
                    for row in sheet.iter_rows(values_only=True):
                        row_text = "\t".join([str(cell) if cell is not None else "" for cell in row])
                        if row_text.strip():
                            text_parts.append(row_text)
                text = "\n".join(text_parts)
                extracted_info = self._process_text_with_gemini(text, sow_data)
            elif file_extension == '.xls':
                # Process XLS file
                workbook = xlrd.open_workbook(file_contents=file_data)
                text_parts = []
                for sheet_index in range(workbook.nsheets):
                    sheet = workbook.sheet_by_index(sheet_index)
                    text_parts.append(f"Sheet: {sheet.name}")
                    for row_index in range(sheet.nrows):
                        row_values = []
                        for col_index in range(sheet.ncols):
                            cell_value = sheet.cell_value(row_index, col_index)
                            row_values.append(str(cell_value) if cell_value != "" else "")
                        row_text = "\t".join(row_values)
                        if row_text.strip():
                            text_parts.append(row_text)
                text = "\n".join(text_parts)
                extracted_info = self._process_text_with_gemini(text, sow_data)
            elif file_extension == '.csv':
                # Process CSV file
                text = file_data.decode('utf-8', errors='ignore')
                # Parse CSV to ensure proper formatting
                csv_reader = csv.reader(io.StringIO(text))
                formatted_rows = []
                for row in csv_reader:
                    formatted_rows.append("\t".join(row))
                text = "\n".join(formatted_rows)
                extracted_info = self._process_text_with_gemini(text, sow_data)
            elif file_extension == '.txt':
                # Process TXT file - decode bytes to string
                text = file_data.decode('utf-8', errors='ignore')
                extracted_info = self._process_text_with_gemini(text, sow_data)
            
            requirement_matches = match_requirements_to_document(requirements, extracted_info)
            
            result = {
                'extracted_content': extracted_info,
                'requirement_matches': requirement_matches,
                'source_file': s3_key,
                'processing_method': 'chunked_extraction' if isinstance(extracted_info, dict) and extracted_info.get('extraction_type', '').startswith('chunked') else 'single_extraction'
            }
            
            return result
        
        except Exception as e:
            self.logger.error(f"Error processing S3 file {s3_key}: {str(e)}")
            return {}
        finally:
            self.clear_memory()
    
    def _process_text_with_gemini(self, text: str, sow_data: Dict) -> Dict:
        """Process extracted text with Gemini API to extract structured information"""
        try:
            model = genai.GenerativeModel('gemini-2.0-flash')
            requirements_text = ""
            if sow_data and 'requirements' in sow_data:
                for req in sow_data['requirements'][:10]:
                    requirements_text += f"- {req.get('id', '')}: {req.get('text', '')}\n"
            
            prompt = f"""
            Extract all the text and understanding from this document content.
            
            Additionally, I'm providing a list of key requirements from the SOW.
            In your analysis, please identify any content in this document that relates to these requirements:
            
            {requirements_text}
            
            Format the output as structured JSON with:
            1. Clear sections of the document content
            2. Any key information related to the requirements
            3. Important technical details and specifications
            """
            
            response = model.generate_content(prompt + f"\n\nDocument Content:\n{text[:10000]}")
            return json.loads(response.text.strip('```json\n').strip('```'))
        except Exception as e:
            self.logger.error(f"Error processing text with Gemini: {str(e)}")
            return {"error": f"Could not process text: {str(e)}"}
    
    # def list_projects(self, email: str) -> List[Dict]:
    #     """List projects for a given user"""
    #     try:
    #         db = DiscoveryDatabase()
    #         conn = db._get_connection()
    #         cursor = conn.cursor(cursor_factory=RealDictCursor)
    #         cursor.execute("SELECT id, name FROM projects WHERE project_owner=%s", (email,))
    #         projects = cursor.fetchall()
    #         cursor.close()
    #         db.release_connection(conn)
    #         return projects
    #     except Exception as e:
    #         self.logger.error(f"Error listing projects: {str(e)}")
    #         return []
    
    # def delete_project(self, project_id: int):
    #     """Delete a project and its associated data"""
    #     db = DiscoveryDatabase()
    #     conn = db._get_connection()
    #     cursor = conn.cursor()
        
    #     cursor.execute("SELECT id, name FROM projects WHERE id = %s", (project_id,))
    #     project = cursor.fetchone()
    #     if not project:
    #         cursor.close()
    #         db.release_connection(conn)
    #         return None
        
    #     cursor.execute(""" 
    #         SELECT table_name FROM information_schema.tables 
    #         WHERE table_schema = 'public' AND table_name = 'requirement_matches'
    #     """)
    #     if cursor.fetchone():
    #         cursor.execute("DELETE FROM requirement_matches WHERE project_id = %s", (project_id,))
        
    #     cursor.execute("DELETE FROM sow_data WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM answers WHERE question_id IN (SELECT id FROM questions WHERE project_id = %s)", (project_id,))
    #     cursor.execute("DELETE FROM document_answers WHERE document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
    #     cursor.execute("DELETE FROM document_questions WHERE source_document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
    #     cursor.execute("DELETE FROM document_processing_log WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM new_information WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM transcripts WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM additional_documents WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM questions WHERE project_id = %s", (project_id,))
    #     cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        
    #     conn.commit()
    #     cursor.close()
    #     db.release_connection(conn)
        
    #     return project_id,

def chunk_pdf(pdf_data: bytes, max_pages_per_chunk: int = 20) -> List[bytes]:
    """Split a PDF stream into smaller chunks based on page count"""
    chunk_data = []
    
    try:
        source_doc = fitz.open(stream=pdf_data, filetype="pdf")
        total_pages = len(source_doc)
        
        if total_pages <= max_pages_per_chunk:
            source_doc.close()
            return [pdf_data]
        
        chunk_number = 1
        for start_page in range(0, total_pages, max_pages_per_chunk):
            end_page = min(start_page + max_pages_per_chunk - 1, total_pages - 1)
            chunk_doc = fitz.open()
            
            for page_num in range(start_page, end_page + 1):
                chunk_doc.insert_pdf(source_doc, from_page=page_num, to_page=page_num)
            
            output_stream = io.BytesIO()
            chunk_doc.save(output_stream)
            chunk_data.append(output_stream.getvalue())
            chunk_doc.close()
            
            chunk_number += 1
        
        source_doc.close()
        
    except Exception as e:
        logging.getLogger(__name__).error(f"Error chunking PDF: {str(e)}")
        return [pdf_data]
    
    return chunk_data

def extract_text_with_gemini_chunked(pdf_data: bytes, gemini_api_key: str, sow_data: Dict, max_pages_per_chunk: int = 20, is_stream: bool = False) -> Dict:
    """Use Gemini to extract structured information from a PDF stream with chunking support"""
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    try:
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        return {"error": f"Could not read PDF: {str(e)}"}
    
    logging.getLogger(__name__).info(f"PDF has {total_pages} pages")
    
    chunk_data = chunk_pdf(pdf_data, max_pages_per_chunk)
    
    requirements_text = ""
    if sow_data and 'requirements' in sow_data:
        for req in sow_data['requirements'][:10]:
            requirements_text += f"- {req.get('id', '')}: {req.get('text', '')}\n"
    
    all_extractions = []
    
    for i, chunk in enumerate(chunk_data):
        logging.getLogger(__name__).info(f"Processing chunk {i + 1}/{len(chunk_data)}")
        
        chunk_prompt = f"""
        Extract all the text and understanding from this document chunk (part {i + 1} of {len(chunk_data)}).
        For diagrams, properly extract the text and understanding from them.
        
        {"This is part of a larger document that was split into chunks for processing." if len(chunk_data) > 1 else ""}
        
        Additionally, I'm providing a list of key requirements from the SOW. 
        In your analysis, please identify any content in this document chunk that relates to these requirements:
        
        {requirements_text}
        
        Format the output as structured JSON with:
        1. Clear sections of the document content
        2. Any key information related to the requirements
        3. Important technical details and specifications
        4. Page range information for this chunk
        
        Begin your response with valid JSON.
        """
        
        try:
            response = model.generate_content([
                chunk_prompt,
                {'mime_type': 'application/pdf', 'data': chunk}
            ])
            
            chunk_result = {
                'chunk_number': i + 1,
                'total_chunks': len(chunk_data),
                'extraction': response.text
            }
            
            all_extractions.append(chunk_result)
            logging.getLogger(__name__).info(f"Successfully processed chunk {i + 1}")
            
            if i < len(chunk_data) - 1:
                time.sleep(2)
                
        except Exception as e:
            logging.getLogger(__name__).error(f"Error processing chunk {i + 1}: {str(e)}")
            error_result = {
                'chunk_number': i + 1,
                'total_chunks': len(chunk_data),
                'error': str(e)
            }
            all_extractions.append(error_result)
    
    if len(chunk_data) > 1:
        logging.getLogger(__name__).info("Creating combined summary from all chunks...")
        try:
            combined_result = create_combined_summary(all_extractions, model, requirements_text)
            return combined_result
        except Exception as e:
            logging.getLogger(__name__).error(f"Error creating combined summary: {str(e)}")
            return {
                'extraction_type': 'chunked_individual',
                'total_chunks': len(chunk_data),
                'chunks': all_extractions
            }
    else:
        return json.loads(all_extractions[0]['extraction'].strip('```json\n').strip('```')) if all_extractions else {"error": "No extraction results"}

def create_combined_summary(chunk_extractions: List[Dict], model, requirements_text: str) -> Dict:
    """Create a combined summary from multiple chunk extractions"""
    try:
        combined_content = ""
        successful_chunks = []
        
        for chunk in chunk_extractions:
            if 'extraction' in chunk:
                combined_content += f"\n--- CHUNK {chunk['chunk_number']} ---\n"
                combined_content += chunk['extraction']
                combined_content += "\n"
                successful_chunks.append(chunk['chunk_number'])
            elif 'error' in chunk:
                combined_content += f"\n--- CHUNK {chunk['chunk_number']} ERROR ---\n"
                combined_content += f"Error: {chunk['error']}\n"
        
        summary_prompt = f"""
        The following content was extracted from a large PDF document that was split into {len(chunk_extractions)} chunks.
        Please create a comprehensive, unified summary that combines all the information coherently.
        
        Remove redundancies, organize the information logically, and ensure the final summary captures all key points.
        
        Pay special attention to these SOW requirements when creating the summary:
        {requirements_text}
        
        EXTRACTED CONTENT FROM ALL CHUNKS:
        {combined_content}
        
        Please provide a unified, structured summary as JSON with:
        1. Overall document summary
        2. Key sections and their content
        3. Requirements mapping (which content relates to which SOW requirements)
        4. Technical specifications and important details
        5. Processing metadata (successful chunks, any errors)
        
        Begin your response with valid JSON.
        """
        
        response = model.generate_content(summary_prompt)
        
        return {
            'extraction_type': 'chunked_combined',
            'total_chunks': len(chunk_extractions),
            'successful_chunks': successful_chunks,
            'combined_summary': response.text,
            'individual_chunks': chunk_extractions
        }
        
    except Exception as e:
        logging.getLogger(__name__).error(f"Error in create_combined_summary: {str(e)}")
        return {
            'extraction_type': 'chunked_individual_fallback',
            'total_chunks': len(chunk_extractions),
            'error': f"Summary creation failed: {str(e)}",
            'chunks': chunk_extractions
        }

def match_requirements_to_document(requirements: List[Dict], document_content: Any) -> Dict:
    """Match requirements to document content to find supporting sections"""
    requirement_matches = {}
    
    try:
        if isinstance(document_content, dict):
            document_text = json.dumps(document_content)
        else:
            document_text = str(document_content)
        
        for req in requirements:
            req_id = req.get('id', '')
            req_text = req.get('text', '')
            
            if not req_text.strip():
                continue
            
            keywords = extract_keywords(req_text)
            matches = []
            for keyword in keywords:
                if len(keyword) < 4:
                    continue
                
                pattern = re.compile(r'(.{0,100}' + re.escape(keyword) + r'.{0,100})', re.IGNORECASE)
                for match in pattern.finditer(document_text):
                    context = match.group(1).strip()
                    if context:
                        matches.append({
                            'keyword': keyword,
                            'context': context
                        })
            
            if matches:
                requirement_matches[req_id] = matches
        
        return requirement_matches
    
    except Exception as e:
        logging.getLogger(__name__).error(f"Error matching requirements to document: {str(e)}")
        return {}

def extract_keywords(text: str) -> List[str]:
    """Extract keywords and phrases from requirement text"""
    keywords = []
    stop_words = ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'with', 'by', 'shall', 'will', 'should', 'must']
    
    words = text.lower().split()
    filtered_words = [word.strip(',.()[]{}:;\'\"') for word in words if word.lower() not in stop_words and len(word) > 3]
    keywords.extend(filtered_words)
    
    for i in range(len(words) - 1):
        phrase = words[i] + ' ' + words[i+1]
        keywords.append(phrase.strip(',.()[]{}:;\'\"'))
    
    for i in range(len(words) - 2):
        phrase = words[i] + ' ' + words[i+1] + ' ' + words[i+2]
        keywords.append(phrase.strip(',.()[]{}:;\'\"'))
    
    keywords = list(set(keywords))
    
    return keywords