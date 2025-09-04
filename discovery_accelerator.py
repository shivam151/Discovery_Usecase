import os
import boto3
import requests
import json
import datetime
import time
import traceback
from typing import List, Dict, Any, Optional
from io import BytesIO
from sow_parser import SOWParser
from question_generator import QuestionGenerator
from transcript_analyzer import TranscriptAnalyzer
from discovery_db_postgresql import DiscoveryDatabase
from file_processing import ProjectDataPipeline
from additional_doc import AdditionalDocumentProcessor
from psycopg2.extras import RealDictCursor
import logging
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

class DiscoveryAccelerator:
    def __init__(
        self,
        bucket_name: str,
        gemini_api_key: str = None,
        inference_api_url: str = "http://localhost:5000",
    ):
        """Initialize the Discovery Accelerator with S3 and necessary components"""
        self.bucket_name = bucket_name
        self.gemini_api_key = gemini_api_key
        self.inference_api_url = inference_api_url

        # Initialize S3 client
        self.s3_client = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION"),
        )

        # Initialize database
        self.db = DiscoveryDatabase()

        # Initialize document processing pipeline
        self.pipeline = ProjectDataPipeline(
            bucket_name=bucket_name,
            inference_api_url=inference_api_url,
            gemini_api_key=gemini_api_key,
        )

        # Initialize other components
        self.sow_parser = SOWParser(gemini_api_key=gemini_api_key)
        self.question_generator = QuestionGenerator(
            self.db, gemini_api_key=gemini_api_key
        )
        self.transcript_analyzer = TranscriptAnalyzer(
            self.db, gemini_api_key=gemini_api_key
        )
        self.additional_doc_processor = AdditionalDocumentProcessor(
            db_connection=self.db,
            bucket_name=bucket_name,
            gemini_api_key=gemini_api_key,
        )

    def process_documents(
        self,
        project_name: str,
        sow_path: str,
        additional_docs_paths: List[str] = None,
        project_owner: str = "default_owner",
    ) -> Dict[str, Any]:
        logger.info(f"STARTING DOCUMENT PROCESSING FOR: {project_name}")
        try:
            # Create project in database
            logger.info("Step 1: Creating project in database...")
            project_id = self.db.create_project(project_name, sow_path, project_owner)
            logger.info(f"Project created with ID: {project_id}")

            # Parse SOW document from presigned URL
            #logger.info(f"Step 2: Parsing SOW document from URL: {sow_path}")
            try:
                response = requests.get(sow_path)
                response.raise_for_status()
                sow_file = BytesIO(response.content)
                # Extract filename from URL, handling query parameters
                sow_filename = sow_path.split("/")[-1].split("?")[0]
                file_extension = os.path.splitext(sow_filename)[1].lower()
                if file_extension not in ['.pdf', '.docx', '.pptx']:
                    raise ValueError(f"Unsupported file format: {file_extension} for file: {sow_filename}")
                logger.info(f"Extracted SOW filename: {sow_filename}")
                sow_data = self.sow_parser.parse_sow(sow_file, filename=sow_filename)
                logger.info(
                    f"SOW parsing completed successfully. Found {len(sow_data.get('requirements', []))} requirements and {len(sow_data.get('sections', {}))} sections"
                )
                self.db.store_sow_data(project_id, sow_data)
                logger.info("SOW data stored successfully")
            except requests.RequestException as e:
                logger.error(f"Failed to fetch SOW from URL: {str(e)}")
                raise Exception(f"Failed to fetch SOW document: {str(e)}")
            except Exception as sow_error:
                logger.error(f"SOW parsing failed: {str(sow_error)}", exc_info=True)
                raise

            # Process additional documents
            logger.info(f"Step 3: Preparing documents for processing...")
            doc_urls = additional_docs_paths or []
            logger.info(f"Total additional documents to process: {len(doc_urls)}")
            additional_data = []

            for doc_url in doc_urls:
                try:
                    response = requests.get(doc_url)
                    response.raise_for_status()
                    doc_file = BytesIO(response.content)
                    doc_filename = doc_url.split("/")[-1].split("?")[0]
                    doc_extension = os.path.splitext(doc_filename)[1].lower()
                    if doc_extension not in ['.pdf', '.docx', '.pptx']:
                        logger.warning(f"Skipping document {doc_filename} due to unsupported format: {doc_extension}")
                        continue
                    logger.info(f"Processing additional document: {doc_filename}")
                    doc_data = self.sow_parser.parse_sow(
                        doc_file, filename=doc_filename
                    )
                    additional_data.append(doc_data)
                except requests.RequestException as e:
                    logger.error(
                        f"Failed to fetch additional document {doc_url}: {str(e)}"
                    )
                    continue
                except Exception as doc_error:
                    logger.error(
                        f"Failed to parse additional document {doc_filename}: {str(doc_error)}",
                        exc_info=True,
                    )
                    continue

            # Process documents using pipeline
            logger.info(
                f"Step 4: Processing documents to create embeddings and match requirements..."
            )
            processed_results = self.pipeline.process_project(
                project_name, doc_urls, project_owner=project_owner, sow_data=sow_data
            )

            # Merge requirement matches from all documents
            all_requirement_matches = {}
            if (
                processed_results
                and "document_requirement_matches" in processed_results
            ):
                doc_req_matches = processed_results["document_requirement_matches"]
                for req_id, matches in doc_req_matches.items():
                    if req_id not in all_requirement_matches:
                        all_requirement_matches[req_id] = []
                    all_requirement_matches[req_id].extend(matches)

            # Add requirement matches to SOW data
            sow_data["requirement_matches"] = all_requirement_matches
            logger.info(
                f"Found supporting content for {len(all_requirement_matches)} requirements"
            )
            self.db.store_sow_data(project_id, sow_data)
            logger.info("Updated SOW data with requirement matches")

            logger.info(
                f"DOCUMENT PROCESSING COMPLETED SUCCESSFULLY FOR: {project_name}"
            )
            return {
                "status": "success",
                "project_id": project_id,
                "project_name": project_name,
                "sow_data": sow_data,
                "additional_data": additional_data,
            }

        except Exception as e:
            logger.error(
                f"DOCUMENT PROCESSING FAILED FOR: {project_name}: {str(e)}",
                exc_info=True,
            )
            raise

    def generate_questions(
        self, project_id: int, sow_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(f"STARTING QUESTION GENERATION FOR PROJECT ID: {project_id}")
        try:
            project = self.db.get_project_info(project_id)
            if not project:
                raise ValueError(f"Project with ID {project_id} not found")

            project_name = project["name"]
            logger.info(f"Generating questions for project: {project_name}")

            logger.info("Generating initial questions...")
            try:
                initial_questions = self.question_generator.generate_initial_questions(
                    sow_data, project_name
                )
                logger.info(
                    f"Question generation completed with {len(initial_questions)} questions"
                )

                logger.info("Storing questions in database...")
                # with open("questions.json", "w") as file:
                #     json.dump(initial_questions, file, indent=4)
                # logger.info("Questions stored in questions.json")
                question_ids = self.db.store_questions(
                    initial_questions["questions"], project_id
                )
                logger.info(f"Stored {len(question_ids)} questions in database")
            except Exception as question_error:
                logger.error(
                    f"Question generation failed: {str(question_error)}", exc_info=True
                )
                raise

            logger.info(
                f"QUESTION GENERATION COMPLETED SUCCESSFULLY FOR PROJECT ID: {project_id}"
            )
            return {
                "status": "success",
                "project_id": project_id,
                "project_name": project_name,
                "initial_questions_count": len(question_ids),
                "questions": initial_questions,
            }

        except Exception as e:
            logger.error(
                f"QUESTION GENERATION FAILED FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            raise

    def start_discovery(
        self,
        project_name: str,
        sow_path: str,
        additional_docs_paths: List[str] = None,
        project_owner: str = "default_owner",
    ) -> Dict[str, Any]:
        logger.info(f"STARTING COMBINED DISCOVERY PROCESS FOR: {project_name}")
        doc_result = self.process_documents(
            project_name, sow_path, additional_docs_paths, project_owner
        )

        if doc_result["status"] != "success":
            return doc_result

        question_result = self.generate_questions(
            project_id=doc_result["project_id"], sow_data=doc_result["sow_data"]
        )

        return question_result

    def process_meeting_transcript(
        self, project_id: int, transcript_text: str
    ) -> Dict[str, Any]:
        logger.info(f"PROCESSING MEETING TRANSCRIPT FOR PROJECT ID: {project_id}")
        try:
            transcript_results = self.transcript_analyzer.process_transcript(
                project_id, transcript_text
            )
            followup_questions = []
 
            if transcript_results.get("status") == "success":
                conn = self.db._get_connection()
                cursor = conn.cursor(cursor_factory=RealDictCursor)

                cursor.execute(
                    """
                    SELECT q.id, q.question, a.answer_text
                    FROM questions q 
                    JOIN answers a ON q.id = a.question_id
                    WHERE a.transcript_id = %s
                    """,
                    (transcript_results.get("transcript_id"),),
                )

                answered_questions = cursor.fetchall()

                for q_id, question, answer in answered_questions:
                    q_followups = self.question_generator.generate_followup_questions(
                        q_id, answer
                    )
                    followup_questions.extend(q_followups)

                if followup_questions:
                    self.db.store_questions(followup_questions, project_id)

                cursor.close()
                self.db.release_connection(conn)

            discovery_status = self.db.get_discovery_status(project_id)

            logger.info(f"TRANSCRIPT PROCESSING COMPLETED FOR PROJECT ID: {project_id}")
            return {
                "status": "success",
                "transcript_processed": transcript_results.get("status") == "success",
                "answers_found": transcript_results.get("answers_found", 0),
                "followup_questions_count": len(followup_questions),
                "followup_questions": followup_questions,
                "discovery_status": discovery_status,
            }

        except Exception as e:
            logger.error(
                f"TRANSCRIPT PROCESSING FAILED FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            raise


    def get_current_questions(self, project_id: int, status: str = None) -> List[Dict[str, Any]]:
        logger.info(f"FETCHING QUESTIONS FOR PROJECT ID: {project_id}")
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Base query for questions
            if status:
                cursor.execute(
                    "SELECT * FROM questions WHERE project_id = %s AND status = %s ORDER BY priority, id",
                    (project_id, status),
                )
            else:
                cursor.execute(
                    "SELECT * FROM questions WHERE project_id = %s ORDER BY status, priority, id",
                    (project_id,),
                )

            questions = [{key: row[key] for key in row.keys()} for row in cursor.fetchall()]

            # Fetch all question IDs at once
            question_ids = [q["id"] for q in questions]

            if question_ids:
                # Execute a single query to fetch all answers for the given question IDs
                query = """
                    WITH RankedAnswers AS (
                        SELECT a.*, t.meeting_date,
                            ROW_NUMBER() OVER (PARTITION BY a.question_id ORDER BY a.id DESC) AS rn
                        FROM answers a 
                        JOIN transcripts t ON a.transcript_id = t.id
                        WHERE a.question_id IN %s
                    )
                    SELECT * 
                    FROM RankedAnswers 
                    WHERE rn = 1
                """
                cursor.execute(query, (tuple(question_ids),))
                results = cursor.fetchall()

                # Create a dictionary to map question IDs to their answers
                answer_map = {row["question_id"]: {key: row[key] for key in row.keys()} for row in results}
                
            # Fetch answers for each question from both answers and document_answers tables
                for q in questions:
                    q["answer"] = answer_map.get(q["id"], None)
            else:
                # If no questions, set answer to None for all
                for q in questions:
                    q["answer"] = None

            cursor.close()
            self.db.release_connection(conn)
            return questions
        except Exception as e:
            logger.error(
                f"FAILED TO FETCH QUESTIONS FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            return []

    def is_discovery_complete(self, project_id: int) -> Dict[str, Any]:
        logger.info(f"CHECKING DISCOVERY STATUS FOR PROJECT ID: {project_id}")
        try:
            status = self.db.get_discovery_status(project_id)
            return status
        except Exception as e:
            logger.error(
                f"FAILED TO CHECK DISCOVERY STATUS FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            raise

    def generate_discovery_report(self, project_id: int) -> Dict[str, Any]:
        logger.info(f"GENERATING DISCOVERY REPORT FOR PROJECT ID: {project_id}")
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            project = cursor.fetchone()

            if not project:
                raise ValueError("Project not found")

            sow_data = self.db.get_project_sow_data(project_id)
            all_questions = self.get_current_questions(project_id)

            questions_by_status = {}
            for q in all_questions:
                status = q["status"]
                if status not in questions_by_status:
                    questions_by_status[status] = []
                questions_by_status[status].append(q)

            cursor.execute(
                "SELECT * FROM transcripts WHERE project_id = %s ORDER BY meeting_date",
                (project_id,),
            )
            transcripts = [
                {key: row[key] for key in row.keys()} for row in cursor.fetchall()
            ]

            cursor.execute(
                "SELECT * FROM new_information WHERE project_id = %s ORDER BY priority",
                (project_id,),
            )
            new_info = [
                {key: row[key] for key in row.keys()} for row in cursor.fetchall()
            ]

            cursor.close()
            self.db.release_connection(conn)

            discovery_status = self.db.get_discovery_status(project_id)

            return {
                "status": "success",
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "created_at": project["created_at"],
                },
                "discovery_status": discovery_status,
                "sow_summary": {
                    "sections_count": len(sow_data.get("sections", {})),
                    "requirements_count": len(sow_data.get("requirements", [])),
                    "in_scope_items": len(
                        sow_data.get("boundaries", {}).get("in_scope", [])
                    ),
                    "out_of_scope_items": len(
                        sow_data.get("boundaries", {}).get("out_of_scope", [])
                    ),
                    "unclear_items": len(
                        sow_data.get("boundaries", {}).get("unclear", [])
                    ),
                },
                "questions": {
                    "total": len(all_questions),
                    "by_status": {
                        status: len(questions)
                        for status, questions in questions_by_status.items()
                    },
                    "details": questions_by_status,
                },
                "transcripts": {"count": len(transcripts), "details": transcripts},
                "new_information": new_info,
            }

        except Exception as e:
            logger.error(
                f"FAILED TO GENERATE DISCOVERY REPORT FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            raise

    def process_additional_documents(
        self, project_id: int, doc_urls: List[str]
    ) -> Dict[str, Any]:
        """Process additional documents for a project."""
        logger.info(f"PROCESSING ADDITIONAL DOCUMENTS FOR PROJECT ID: {project_id}")
        project = self.db.get_project_info(project_id)
        if not project:
            logger.error(f"Project not found for project_id={project_id}")
            return {
                "status": "error",
                "project_id": project_id,
                "project_name": None,
                "documents_processed": 0,
                "answers_found": 0,
                "new_questions_generated": 0,
                "processed": [],
                "invalid_urls": [],
                "discovery_status": None,
                "failed_uploads": [],
                "total_documents_uploaded": 0,
            }

        project_name = project["name"]
        project_owner = project.get("owner", "unknown_user")
        processed_results = []
        invalid_urls = []

        if not doc_urls:
            logger.warning("No document URLs provided")
            return {
                "status": "error",
                "project_id": project_id,
                "project_name": project_name,
                "documents_processed": 0,
                "answers_found": 0,
                "new_questions_generated": 0,
                "processed": [],
                "invalid_urls": [],
                "discovery_status": None,
                "failed_uploads": [],
                "total_documents_uploaded": 0,
            }

        # Get SOW data for requirement matching
        sow_data = self.db.get_project_sow_data(project_id) or {"requirements": []}
        logger.debug(f"Requirements from sow_data: {sow_data.get('requirements', [])}")

        for doc_url in doc_urls:
            try:
                logger.debug(f"Fetching document from URL: {doc_url}")
                response = requests.get(doc_url, timeout=10)
                if response.status_code != 200:
                    logger.warning(
                        f"Invalid document URL (status {response.status_code}): {doc_url}"
                    )
                    invalid_urls.append(
                        {"url": doc_url, "error": f"HTTP {response.status_code}"}
                    )
                    continue

                # Extract and validate filename
                doc_filename = doc_url.split("/")[-1].split("?")[0]
                doc_extension = os.path.splitext(doc_filename)[1].lower()
                if doc_extension not in ['.pdf', '.docx', '.pptx']:
                    logger.warning(f"Skipping document {doc_filename} due to unsupported format: {doc_extension}")
                    invalid_urls.append({"url": doc_url, "error": f"Unsupported file format: {doc_extension}"})
                    continue
                logger.info(f"Processing additional document: {doc_filename}")

                # Parse document
                doc_file = BytesIO(response.content)
                doc_data = self.sow_parser.parse_sow(doc_file, filename=doc_filename)
                logger.debug(
                    f"Parsed data for {doc_filename}: {json.dumps(doc_data, indent=2)}"
                )

                # Store document metadata in database
                file_size = len(response.content)
                doc_id = self.db.store_additional_document(
                    project_id=project_id,
                    filename=doc_filename,
                    filepath=doc_url,
                    file_size=file_size,
                    notes="Uploaded via /upload_additional_documents",
                )

                # Process document using pipeline
                processed_result = self.pipeline.parse_file(doc_url, sow_data)
                if not processed_result:
                    logger.warning(f"Failed to process document {doc_filename}")
                    invalid_urls.append({"url": doc_url, "error": "Processing failed"})
                    continue

                # Extract content and requirement matches
                extracted_content = processed_result.get("extracted_content", "")
                requirement_matches = processed_result.get("requirement_matches", {})

                # Store requirement matches in database
                for req_id, matches in requirement_matches.items():
                    for match in matches:
                        self.db.store_requirement_match(
                            project_id=project_id,
                            document_id=doc_id,
                            requirement_id=req_id,
                            context=match["context"],
                        )

                # Update document processing status
                self.db.update_document_processing_status(
                    document_id=doc_id,
                    status="completed",
                    answers_found=0,
                    questions_generated=0,
                    requirement_matches=len(requirement_matches),
                )

                # Log processing step
                self.db.log_processing_step(
                    project_id=project_id,
                    document_id=doc_id,
                    step="document_processing",
                    status="completed",
                    details=f"Processed document {doc_filename} with {len(requirement_matches)} requirement matches",
                )

                processed_results.append(
                    {
                        "document_id": doc_id,
                        "filename": doc_filename,
                        "status": "completed",
                        "requirement_matches": len(requirement_matches),
                    }
                )

            except requests.RequestException as e:
                logger.error(f"Failed to fetch document {doc_url}: {str(e)}")
                invalid_urls.append({"url": doc_url, "error": str(e)})
                self.db.log_processing_step(
                    project_id=project_id,
                    document_id=None,
                    step="document_fetch",
                    status="failed",
                    error=str(e),
                )
                continue
            except Exception as e:
                logger.error(
                    f"Failed to process document {doc_filename}: {str(e)}",
                    exc_info=True,
                )
                invalid_urls.append({"url": doc_url, "error": str(e)})
                self.db.log_processing_step(
                    project_id=project_id,
                    document_id=None,
                    step="document_processing",
                    status="failed",
                    error=str(e),
                )
                continue

        if not processed_results and invalid_urls:
            logger.error(
                f"ADDITIONAL DOCUMENT PROCESSING FAILED FOR PROJECT ID: {project_id}: No valid documents processed"
            )
            return {
                "status": "error",
                "project_id": project_id,
                "project_name": project_name,
                "documents_processed": 0,
                "answers_found": 0,
                "new_questions_generated": 0,
                "processed": [],
                "invalid_urls": invalid_urls,
                "discovery_status": self.get_project_progress_summary(project_id),
                "failed_uploads": [],
                "total_documents_uploaded": len(doc_urls),
            }

        logger.info(
            f"ADDITIONAL DOCUMENT PROCESSING COMPLETED FOR PROJECT ID: {project_id}"
        )
        return {
            "status": "success",
            "project_id": project_id,
            "project_name": project_name,
            "documents_processed": len(processed_results),
            "answers_found": 0,
            "new_questions_generated": 0,
            "processed": processed_results,
            "invalid_urls": invalid_urls,
            "discovery_status": self.get_project_progress_summary(project_id),
            "failed_uploads": [],
            "total_documents_uploaded": len(doc_urls),
        }

    def get_project_progress_summary(self, project_id: int) -> Dict[str, Any]:
        """Fetch project progress summary from the database"""
        logger.info(f"FETCHING PROJECT PROGRESS SUMMARY FOR PROJECT ID: {project_id}")
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
                          # Query to fetch project details and progress
            query = """
                                SELECT 
                                    p.id AS project_id,
                                    p.name AS project_name,
                                    p.created_at,
                                    p.project_owner,
                                    p.sow_path,
                                    (SELECT COUNT(*) FROM questions q WHERE q.project_id = p.id) AS total_questions,
                                    (SELECT COUNT(*) FROM questions q JOIN answers a ON q.id = a.question_id 
                                    WHERE q.project_id = p.id) AS answered_questions,
                                    COALESCE(
                                        (SELECT json_array_length(requirements::json) 
                                        FROM sow_data s WHERE s.project_id = p.id LIMIT 1),
                                        0
                                    ) AS total_requirements,
                                    COALESCE(
                                        (SELECT COUNT(*) FROM requirement_matches rm 
                                        WHERE rm.project_id = p.id),
                                        0
                                    ) AS matched_requirements,
                                    (SELECT COUNT(*)
                                        FROM (
                                            SELECT DISTINCT original_filename, file_path
                                            FROM additional_documents ad where ad.project_id = %s
                                            ) AS additional_docs) as additional_doc_count
                                FROM projects p
                                WHERE p.id = %s
                    """
            logger.debug(
                     f"Executing query: {query} with params: ({project_id},)"
                )
            cursor.execute(query, (project_id, project_id))
            #logger.debug(f"Query executed successfully")
            result = cursor.fetchone()
            #logger.debug(f"Query result: {result}")
            if not result:
                logger.warning(f"No project found for ID: {project_id}")
                return {
                            "status": "error",
                            "message": f"Project ID {project_id} not found",
                            "progress": {},
                  }

            progress = {
                        "project_id": result["project_id"],
                        "project_name": result["project_name"],
                        "created_at": (
                            result["created_at"].isoformat()
                            if result["created_at"]
                            else None
                        ),
                        "project_owner": result["project_owner"],
                        "sow_path": result["sow_path"],
                        "total_questions": result["total_questions"],
                        "answered_questions": result["answered_questions"],
                        "total_requirements": result["total_requirements"],
                        "matched_requirements": result["matched_requirements"],
                        "additional_doc_count": result["additional_doc_count"],
                        "completion_percentage": (
                            (
                                result["answered_questions"]
                                / result["total_questions"]
                                * 100
                            )
                            if result["total_questions"] > 0
                            else 0
                        ),
              }

            logger.info(
                        f"Successfully fetched progress summary for project {project_id}"
                    )
            return {
                        "status": "success",
                        "project_id": project_id,
                        "progress": progress,
                    }

        except Exception as e:
            logger.error(
                f"FAILED TO FETCH PROJECT PROGRESS SUMMARY FOR PROJECT ID: {project_id}: {str(e)}",
                exc_info=True,
            )
            conn.rollback()
            raise Exception(f"Failed to fetch project progress: {str(e)}")
        finally:
            cursor.close()
            self.db.release_connection(conn)
   
    def get_answers_by_source(self, project_id: int) -> Dict[str, Any]:
            logger.info(f"FETCHING ANSWERS BY SOURCE FOR PROJECT ID: {project_id}")
            try:
                conn = self.db._get_connection()
                cursor = conn.cursor(cursor_factory=RealDictCursor)

            # Query transcript-based answers
                cursor.execute(
                    """
                    SELECT q.question, q.context, a.answer_text, a.confidence,
                           t.transcript_text as source_info, t.meeting_date,
                           NULL as document_section, NULL as source_document
                    FROM questions q
                    JOIN answers a ON q.id = a.question_id
                    JOIN transcripts t ON a.transcript_id = t.id
                    WHERE q.project_id = %s
                    """,
                    (project_id,),
                )
                transcript_rows = cursor.fetchall()

            # Query document-based answers
                cursor.execute(
                    """
                    SELECT q.question, q.context, da.answer_text, da.confidence,
                          ad.original_filename as source_info, da.created_at as meeting_date,
                          da.document_section, ad.original_filename as source_document
                    FROM questions q
                    JOIN document_answers da ON q.id = da.question_id
                    JOIN additional_documents ad ON da.document_id = ad.id
                    WHERE q.project_id = %s
                    """,
                    (project_id,),
                )
                document_rows = cursor.fetchall()

                cursor.close()
                self.db.release_connection(conn)

                document_answers = []
                transcript_answers = []

            # Process transcript answers
                for row in transcript_rows:
                    answer_data = {
                        "question": row["question"],
                        "context": row["context"],
                        "answer": row["answer_text"],
                        "confidence": row["confidence"],
                        "date": row["meeting_date"].isoformat() if row["meeting_date"] else None,
                        "source": "Meeting Transcript",
                        "document_section": None,
                    }
                    transcript_answers.append(answer_data)

            # Process document answers
                for row in document_rows:
                    answer_data = {
                        "question": row["question"],
                        "context": row["context"],
                        "answer": row["answer_text"],
                        "confidence": row["confidence"],
                        "date": row["meeting_date"].isoformat() if row["meeting_date"] else None,
                        "source": f"Additional Document: {row['source_document']}",
                        "document_section": row["document_section"],
                    }
                    document_answers.append(answer_data)

                return {
                    "status": "success",
                    "project_id": project_id,
                    "document_answers": document_answers,
                    "transcript_answers": transcript_answers,
                    "summary": {
                        "total_answers": len(transcript_rows) + len(document_rows),
                        "document_answers": len(document_rows),
                        "transcript_answers": len(transcript_rows),
                    },
                }

            except Exception as e:
                logger.error(
                    f"FAILED TO FETCH ANSWERS BY SOURCE FOR PROJECT ID: {project_id}: {str(e)}",
                    exc_info=True,
                )
                raise