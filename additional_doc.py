import os
import json
import time
import traceback
from typing import List, Dict, Any, Optional
import google.generativeai as genai
import requests
from io import BytesIO
import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values
from psycopg2.extras import RealDictCursor
from file_processing import ProjectDataPipeline, extract_text_with_gemini_chunked, match_requirements_to_document
import logging
from urllib.parse import urlparse
import fitz  # PyMuPDF for PDF text extraction
try:
    from PIL import Image
    import pytesseract
except ImportError:
    Image = None
    pytesseract = None

class AdditionalDocumentProcessor:
    def __init__(self, db_connection, bucket_name: str, gemini_api_key: str = None):
        """Initialize the Additional Document Processor with S3 and pipeline configurations"""
        self.bucket_name = bucket_name
        self.db = db_connection
        
        # Initialize S3 client
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
            aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
            region_name=os.getenv('AWS_REGION')
        )
        
        # Resolve Gemini API key
        resolved_gemini_api_key = gemini_api_key
        if not resolved_gemini_api_key:
            env_vars_from_file = dotenv_values()
            resolved_gemini_api_key = env_vars_from_file.get("GOOGLE_API_KEY")
        
        if not resolved_gemini_api_key:
            raise ValueError(
                "No Gemini API key provided. "
                "Please set GOOGLE_API_KEY in your .env file "
                "or pass it directly to the AdditionalDocumentProcessor constructor."
            )
        
        genai.configure(api_key=resolved_gemini_api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.gemini_api_key = resolved_gemini_api_key
        
        # Initialize the file processing pipeline
        self.pipeline = ProjectDataPipeline(
            bucket_name=bucket_name,
            inference_api_url="http://localhost:5000",
            gemini_api_key=resolved_gemini_api_key
        )
        
        # Set up logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.logger.info("Initialized AdditionalDocumentProcessor with updated version (2025-07-29 v5)")
        
        # Check OCR availability
        self.ocr_available = Image is not None and pytesseract is not None
        if not self.ocr_available:
            self.logger.warning("OCR dependencies (PIL, pytesseract) not installed. Image-based PDFs may not be processed correctly.")

    def _get_s3_object(self, s3_key: str) -> bytes:
        """Retrieve an object from S3"""
        try:
            self.logger.info(f"Attempting to fetch S3 object with key: {s3_key}")
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            self.logger.info(f"Successfully fetched S3 object: {s3_key}")
            return response['Body'].read()
        except ClientError as e:
            self.logger.error(f"Error retrieving S3 object {s3_key}: {str(e)}")
            raise Exception(f"Failed to retrieve S3 object: {str(e)}")

    def _fetch_document_content(self, doc_path: str) -> str:
        """Fetch document content from S3 or URL and extract text, with OCR fallback for image-based PDFs"""
        self.logger.info(f"Processing document path: {doc_path}")
        try:
            if doc_path.startswith('https://'):
                self.logger.info(f"Fetching content from presigned URL: {doc_path}")
                response = requests.get(doc_path, timeout=10)
                response.raise_for_status()
                content = response.content
            else:
                self.logger.info(f"Fetching content from S3 key: {doc_path}")
                content = self._get_s3_object(doc_path)
            
            # Extract text from PDF
            doc = fitz.open(stream=BytesIO(content), filetype='pdf')
            text = ""
            for page in doc:
                text += page.get_text()
            
            # Fallback to OCR if no text is extracted and OCR is available
            if not text.strip() and self.ocr_available:
                self.logger.info(f"No text extracted from {doc_path}, attempting OCR")
                for page in doc:
                    pix = page.get_pixmap()
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.rgb)
                    text += pytesseract.image_to_string(img)
            
            doc.close()
            self.logger.info(f"Extracted content from {doc_path}, length: {len(text)} characters")
            return text
        except requests.exceptions.RequestException as e:
            self.logger.error(f"Failed to fetch presigned URL {doc_path}: {str(e)}, Status Code: {e.response.status_code if e.response else 'No response'}")
            raise Exception(f"Failed to fetch presigned URL: {str(e)}")
        except Exception as e:
            self.logger.error(f"Error fetching document {doc_path}: {str(e)}")
            raise

    def process_additional_documents(self, project_id: int, document_paths: List[str]) -> Dict[str, Any]:
        """
        Process additional documents after project initialization
        
        Args:
            project_id: ID of the existing project
            document_paths: List of S3 keys or presigned URLs to additional documents
            
        Returns:
            Dictionary with processing results
        """
        self.logger.info(f"\n==== PROCESSING ADDITIONAL DOCUMENTS FOR PROJECT {project_id} ====")
        self.logger.info(f"Documents to process: {len(document_paths)}")
        
        try:
            # Get SOW data for context
            sow_data = self.db.get_project_sow_data(project_id)
            if not sow_data:
                self.logger.error("No SOW data found for this project")
                return {
                    'status': 'error',
                    'message': 'No SOW data found for this project'
                }
            
            # Get existing unanswered questions
            existing_questions = self.db.get_unanswered_questions(project_id)
            self.logger.info(f"Found {len(existing_questions)} existing unanswered questions")
            
            # Process each document
            processed_docs = []
            all_answers = []
            all_new_questions = []
            
            for doc_path in document_paths:
                doc_basename = doc_path.split('/')[-1].split('?')[0]
                self.logger.info(f"\nProcessing document: {doc_basename}")
                
                try:
                    # Fetch document content
                    doc_content = self._fetch_document_content(doc_path)
                    
                    # Extract S3 key for parse_file
                    if doc_path.startswith('https://'):
                        parsed_url = urlparse(doc_path)
                        s3_key = parsed_url.path.lstrip('/')
                        self.logger.info(f"Extracted S3 key from presigned URL: {s3_key}")
                    else:
                        s3_key = doc_path
                        self.logger.info(f"Using provided S3 key: {s3_key}")
                    
                    # Store document metadata in database
                    doc_id = self.db.store_additional_document(
                        project_id=project_id,
                        filename=doc_basename,
                        filepath=doc_path,
                        file_size=len(doc_content.encode('utf-8') if isinstance(doc_content, str) else doc_content)
                    )
                    self.logger.info(f"Stored document metadata with ID: {doc_id}")
                    
                    # Create a placeholder transcript entry for document-based answers
                    transcript_id = self.db.store_transcript(
                        project_id=project_id,
                        transcript_text=f"Placeholder transcript for document: {doc_basename}"
                    )
                    self.logger.info(f"Created placeholder transcript with ID: {transcript_id}")
                    
                    # Process the document using the pipeline
                    doc_result = self.pipeline.parse_file(s3_key, sow_data)
                    
                    if not doc_result or 'extracted_content' not in doc_result:
                        self.logger.error(f"Failed to extract content from {doc_basename}")
                        self.db.log_processing_step(
                            project_id=project_id,
                            document_id=doc_id,
                            step="content_extraction",
                            status="failed",
                            error="Failed to extract content"
                        )
                        self.db.update_document_processing_status(
                            document_id=doc_id,
                            status="failed"
                        )
                        processed_docs.append({
                            'document_path': doc_path,
                            'content_extracted': False,
                            'error': 'Failed to extract content',
                            'answers_found': 0,
                            'new_questions_generated': 0,
                            'requirement_matches': 0
                        })
                        continue
                    
                    extracted_content = doc_result['extracted_content']
                    requirement_matches = doc_result.get('requirement_matches', {})
                    
                    # Handle case where extracted_content is a dictionary
                    if isinstance(extracted_content, dict):
                        self.logger.warning(f"extracted_content is a dictionary for {doc_basename}: {extracted_content}")
                        # Attempt to extract a string from common fields
                        for key in ['text', 'content', 'extracted_text', 'body']:
                            if key in extracted_content and isinstance(extracted_content[key], str):
                                extracted_content = extracted_content[key]
                                self.logger.info(f"Extracted string content from dictionary key '{key}' for {doc_basename}")
                                break
                        else:
                            self.logger.error(f"No valid string content found in dictionary for {doc_basename}")
                            # Fallback to _fetch_document_content
                            extracted_content = self._fetch_document_content(doc_path)
                            self.logger.info(f"Fallback to _fetch_document_content for {doc_basename}, content length: {len(extracted_content)} characters")
                    
                    # Validate extracted content
                    if not isinstance(extracted_content, str):
                        self.logger.error(f"Invalid content type for {doc_basename}: {type(extracted_content)}")
                        self.db.log_processing_step(
                            project_id=project_id,
                            document_id=doc_id,
                            step="content_extraction",
                            status="failed",
                            error=f"Invalid content type: {type(extracted_content)}"
                        )
                        self.db.update_document_processing_status(
                            document_id=doc_id,
                            status="failed"
                        )
                        processed_docs.append({
                            'document_path': doc_path,
                            'content_extracted': False,
                            'error': f"Invalid content type: {type(extracted_content)}",
                            'answers_found': 0,
                            'new_questions_generated': 0,
                            'requirement_matches': len(requirement_matches)
                        })
                        continue
                    
                    if len(extracted_content.strip()) < 10:
                        self.logger.warning(f"Content too short for {doc_basename}: {len(extracted_content)} characters")
                        self.db.log_processing_step(
                            project_id=project_id,
                            document_id=doc_id,
                            step="content_extraction",
                            status="failed",
                            error="Content too short"
                        )
                        self.db.update_document_processing_status(
                            document_id=doc_id,
                            status="failed"
                        )
                        processed_docs.append({
                            'document_path': doc_path,
                            'content_extracted': False,
                            'error': 'Content too short',
                            'answers_found': 0,
                            'new_questions_generated': 0,
                            'requirement_matches': len(requirement_matches)
                        })
                        continue
                    
                    self.logger.info(f"Successfully extracted content from {doc_basename}, content length: {len(extracted_content)} characters")
                    
                    # Try to answer existing questions with this document
                    answers = self._extract_answers_from_document(
                        extracted_content, existing_questions, doc_path, transcript_id
                    )
                    all_answers.extend(answers)
                    
                    # Generate new questions based on document content
                    new_questions = self._generate_questions_from_document(
                        project_id, extracted_content, sow_data, existing_questions, doc_path
                    )
                    all_new_questions.extend(new_questions)
                    
                    # Update document processing status
                    self.db.update_document_processing_status(
                        document_id=doc_id,
                        status="completed",
                        answers_found=len(answers),
                        questions_generated=len(new_questions),
                        requirement_matches=len(requirement_matches)
                    )
                    self.db.log_processing_step(
                        project_id=project_id,
                        document_id=doc_id,
                        step="processing_complete",
                        status="success",
                        details=f"Processed with {len(answers)} answers, {len(new_questions)} questions, {len(requirement_matches)} requirement matches"
                    )
                    
                    processed_docs.append({
                        'document_path': doc_path,
                        'content_extracted': True,
                        'answers_found': len(answers),
                        'new_questions_generated': len(new_questions),
                        'requirement_matches': len(requirement_matches)
                    })
                    
                    self.logger.info(f"Document {doc_basename}: {len(answers)} answers, {len(new_questions)} questions, {len(requirement_matches)} requirement matches")
                    
                except Exception as doc_error:
                    self.logger.error(f"Error processing document {doc_basename}: {str(doc_error)}")
                    traceback.print_exc()
                    self.db.log_processing_step(
                        project_id=project_id,
                        document_id=doc_id if 'doc_id' in locals() else None,
                        step="processing",
                        status="failed",
                        error=str(doc_error)
                    )
                    processed_docs.append({
                        'document_path': doc_path,
                        'content_extracted': False,
                        'error': str(doc_error),
                        'answers_found': 0,
                        'new_questions_generated': 0,
                        'requirement_matches': 0
                    })
                    continue
            
            # Store answers in database
            answer_count = 0
            for answer in all_answers:
                try:
                    # Store the answer in the answers table using the placeholder transcript_id
                    stored = self.db.store_answer(
                        question_id=answer['question_id'],
                        transcript_id=answer['transcript_id'],
                        answer_text=answer['answer'],
                        confidence=answer['confidence']
                    )
                    if stored:
                        answer_count += 1
                        # Update question status
                        self.db.update_question_status(
                            answer['question_id'], 
                            'answered' if answer['confidence'] > 0.8 else 'partially_answered'
                        )
                except Exception as e:
                    self.logger.error(f"Error storing answer: {str(e)}")
            
            # Store new questions
            new_question_count = 0
            if all_new_questions:
                question_ids = self.db.store_questions(all_new_questions, project_id)
                new_question_count = len(question_ids)
            
            self.logger.info(f"\n==== COMPLETED PROCESSING ADDITIONAL DOCUMENTS ====")
            self.logger.info(f"Documents processed: {len(processed_docs)}")
            self.logger.info(f"Answers found: {answer_count}")
            self.logger.info(f"New questions generated: {new_question_count}")
            
            return {
                'status': 'success',
                'project_id': project_id,
                'documents_processed': len([d for d in processed_docs if d.get('content_extracted', False)]),
                'answers_found': answer_count,
                'new_questions_generated': new_question_count,
                'processed_documents': processed_docs
            }
            
        except Exception as e:
            self.logger.error(f"ERROR processing additional documents: {str(e)}")
            traceback.print_exc()
            return {
                'status': 'error',
                'message': str(e)
            }

    def _extract_answers_from_document(
        self, doc_content: str, questions: List[Dict], source_doc: str, transcript_id: int
    ) -> List[Dict]:
        """Extract answers to existing questions from document content"""
        if not questions:
            self.logger.info(f"No questions provided for document {source_doc}")
            return []
        
        # Validate doc_content
        if not isinstance(doc_content, str):
            self.logger.error(f"Invalid content type for {source_doc}: {type(doc_content)}")
            return []
        if len(doc_content.strip()) < 10:
            self.logger.warning(f"Content too short for {source_doc}: {len(doc_content)} characters")
            return []

        answers = []
        batch_size = 5
        for i in range(0, len(questions), batch_size):
            batch_questions = questions[i:i + batch_size]
            
            questions_text = ""
            for idx, q in enumerate(batch_questions):
                questions_text += f"{idx+1}. {q['question']}\n"
                if q.get('context'):
                    questions_text += f"   Context: {q['context']}\n"
                questions_text += "\n"
            
            prompt = f"""
            You are analyzing a document to find answers to specific project discovery questions.
            
            QUESTIONS TO ANSWER:
            {questions_text}
            
            DOCUMENT CONTENT:
            {doc_content[:20000]}  # Limit content to avoid token limits
            
            For each question, determine if there is a relevant answer in the document content.
            Look for both direct answers and indirect information that addresses the question.
            
            Format your response as a JSON array of objects with these keys:
            - question_index: The number of the question (1-based)
            - answer_found: Boolean indicating if an answer was found
            - answer: The extracted answer text (if found)
            - confidence: A value from 0.0 to 1.0 indicating confidence in the answer
            - explanation: Brief explanation of why this answers the question
            - document_section: Which part of the document contains the answer
            
            Only extract answers that directly or clearly address the questions. 
            Be conservative - if you're not confident, mark confidence as low.
            """
            
            try:
                response = self.model.generate_content(prompt)
                batch_answers = self._parse_answers_from_response(response.text)
                
                for answer in batch_answers:
                    if answer.get('answer_found', False):
                        q_idx = answer.get('question_index', 0) - 1
                        if 0 <= q_idx < len(batch_questions):
                            answers.append({
                                'question_id': batch_questions[q_idx].get('id'),
                                'answer': answer.get('answer', ''),
                                'confidence': answer.get('confidence', 0.0),
                                'explanation': answer.get('explanation', ''),
                                'source_document': source_doc,
                                'document_section': answer.get('document_section', ''),
                                'transcript_id': transcript_id
                            })
                            
            except Exception as e:
                self.logger.error(f"Error extracting answers from document batch for {source_doc}: {str(e)}")
                continue
        
        self.logger.info(f"Extracted {len(answers)} answers from {source_doc}")
        return answers
    
    def _generate_questions_from_document(
        self, project_id: int, doc_content: str, sow_data: Dict, 
        existing_questions: List[Dict], source_doc: str
    ) -> List[Dict]:
        """Generate new questions based on document content"""
        # Validate doc_content
        if not isinstance(doc_content, str):
            self.logger.error(f"Invalid content type for {source_doc}: {type(doc_content)}")
            return []
        if len(doc_content.strip()) < 10:
            self.logger.warning(f"Content too short for {source_doc}: {len(doc_content)} characters")
            return []

        try:
            existing_q_text = "\n".join([f"- {q['question']}" for q in existing_questions[:20]])
            requirements_text = ""
            if sow_data.get('requirements'):
                for req in sow_data['requirements'][:10]:
                    requirements_text += f"- {req.get('id', '')}: {req.get('text', '')}\n"
            
            prompt = f"""
            You are a technical analyst reviewing a new document that has been added to an ongoing project.
            Generate specific, technical questions that arise from this document content that haven't been covered by existing questions.
            
            PROJECT REQUIREMENTS CONTEXT:
            {requirements_text}
            
            EXISTING QUESTIONS (avoid duplicating these):
            {existing_q_text}
            
            NEW DOCUMENT CONTENT:
            {doc_content[:20000]}
            
            Based on this new document, generate 3-7 specific questions that:
            1. Address technical details mentioned in the document
            2. Clarify implementation aspects revealed by the document
            3. Identify dependencies or constraints mentioned
            4. Explore integration points discussed
            5. Investigate any new requirements or scope items
            
            Focus on questions that:
            - Are technically specific and actionable
            - Address gaps or new information from this document
            - Would help with project implementation
            - Are NOT duplicates of existing questions
            
            Format your response as a JSON array of objects with keys:
            - question: The specific question text
            - context: Why this question is important based on the document
            - priority: A value from 1-3 (1 being highest priority)
            - category: Type of question (e.g., "Technical", "Integration", "Requirements")
            - target_stakeholder: Who should answer (e.g., "Technical Lead", "Business Analyst")
            - document_reference: Brief reference to relevant part of document
            """
            
            response = self.model.generate_content(prompt)
            questions = self._parse_questions_from_response(response.text)
            
            for q in questions:
                q['source'] = f"Additional Document: {source_doc.split('/')[-1].split('?')[0]}"
                q['source_text'] = f"Generated from document analysis"
                q['status'] = 'unanswered'
                q['additional_document'] = source_doc
                q['source_type'] = 'document'
                
            return questions
            
        except Exception as e:
            self.logger.error(f"Error generating questions from document {source_doc}: {str(e)}")
            return []
    
    def _store_document_reference(self, project_id: int, doc_path: str) -> int:
        """Store document reference for answer tracking"""
        try:
            doc_id = self.db.store_additional_document(
                project_id=project_id,
                filename=doc_path.split('/')[-1].split('?')[0],
                filepath=doc_path
            )
            return doc_id
        except Exception as e:
            self.logger.error(f"Error storing document reference: {str(e)}")
            return 0
    
    def _parse_answers_from_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse answers from Gemini response text"""
        import re
        import json
        
        answers = []
        
        try:
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, response_text)
            
            if json_match:
                answers = json.loads(json_match.group(1))
            else:
                try:
                    answers = json.loads(response_text)
                except:
                    self.logger.error("Failed to parse JSON, using fallback parsing")
                    answers = []
                    
        except Exception as e:
            self.logger.error(f"Error parsing answers from response: {str(e)}")
        
        return answers
    
    def _parse_questions_from_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse questions from Gemini response text"""
        import re
        import json
        
        questions = []
        
        try:
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, response_text)
            
            if json_match:
                questions = json.loads(json_match.group(1))
            else:
                try:
                    questions = json.loads(response_text)
                except:
                    self.logger.error("Failed to parse JSON, using fallback parsing")
                    questions = []
                    
        except Exception as e:
            self.logger.error(f"Error parsing questions from response: {str(e)}")
        
        return questions