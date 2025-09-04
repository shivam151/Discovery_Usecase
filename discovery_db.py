import sqlite3
import json
import os
import threading
from contextlib import contextmanager
from typing import List, Dict, Any, Optional, Tuple
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from dotenv import load_dotenv
load_dotenv()

class DiscoveryDatabase:
    def __init__(self):
        DB_CONFIG = {
            'host': os.getenv('DB_HOST'),
            'database': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'port': os.getenv('DB_PORT')
        }
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                os.getenv('POOL_MIN_SIZE', 1), os.getenv('POOL_MAX_SIZE', 10),
                **DB_CONFIG
            )
            self.initialize_db()
        except Exception as e:
            print(f"DiscoveryDatabase: Error initializing database: {e}")
            raise

    def _get_connection(self):
        return self.connection_pool.getconn()

    def release_connection(self, conn):
        self.connection_pool.putconn(conn)

    def initialize_db(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS projects (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            sow_path TEXT,
            project_owner TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sow_data (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            sections TEXT,
            requirements TEXT,
            boundaries TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            parent_question_id INTEGER,
            question TEXT NOT NULL,
            context TEXT,
            source TEXT,
            source_text TEXT,
            priority INTEGER DEFAULT 3,
            status TEXT DEFAULT 'unanswered',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (parent_question_id) REFERENCES questions(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS transcripts (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            meeting_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            transcript_text TEXT,
            processed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS answers (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            transcript_id INTEGER NOT NULL,
            answer_text TEXT,
            confidence FLOAT DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (transcript_id) REFERENCES transcripts(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS new_information (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            transcript_id INTEGER,
            topic TEXT,
            transcript_excerpt TEXT,
            impact TEXT,
            priority INTEGER DEFAULT 3,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (transcript_id) REFERENCES transcripts(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS additional_documents (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            s3_key VARCHAR(1024),  -- Added s3_key column
            file_size INTEGER,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_date TIMESTAMP,
            processing_status TEXT DEFAULT 'pending',
            content_extracted TEXT,
            answers_found INTEGER DEFAULT 0,
            questions_generated INTEGER DEFAULT 0,
            requirement_matches INTEGER DEFAULT 0,
            notes TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_answers (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            answer_text TEXT,
            confidence FLOAT DEFAULT 0.0,
            document_section TEXT,
            extraction_method TEXT DEFAULT 'gemini',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_questions (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            source_document_id INTEGER NOT NULL,
            document_section TEXT,
            generated_from TEXT,
            relevance_score FLOAT DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (source_document_id) REFERENCES additional_documents(id)
        )
        ''')
        
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_processing_log (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            document_id INTEGER,
            processing_step TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            processing_details TEXT,
            error_message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        
        conn.commit()
        cursor.close()
        self.release_connection(conn)

    def create_project(self, name: str, sow_path: Optional[str] = None, project_owner=None) -> int:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO projects (name, sow_path, project_owner) VALUES (%s, %s, %s) RETURNING id",
                (name, sow_path, project_owner)
            )
            project_id = cursor.fetchone()[0]
            conn.commit()
            return project_id
        except Exception as e:
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def get_project_info(self, project_id: int) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {key: row[key] for key in row.keys()}
        except Exception as e:
            print(f"Error getting project data: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)

    def store_sow_data(self, project_id: int, sow_data: Dict[str, Any]) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            requirement_matches = sow_data.pop('requirement_matches', {}) if 'requirement_matches' in sow_data else {}
            sections_json = json.dumps(sow_data.get('sections', {}))
            requirements_json = json.dumps(sow_data.get('requirements', []))
            boundaries_json = json.dumps(sow_data.get('boundaries', {}))
            if requirement_matches:
                sow_data['requirement_matches'] = requirement_matches
            cursor.execute(
                "SELECT id FROM sow_data WHERE project_id = %s",
                (project_id,)
            )
            existing_id = cursor.fetchone()
            if existing_id:
                cursor.execute(
                    "UPDATE sow_data SET sections = %s, requirements = %s, boundaries = %s WHERE id = %s",
                    (sections_json, requirements_json, boundaries_json, existing_id[0])
                )
            else:
                cursor.execute(
                    "INSERT INTO sow_data (project_id, sections, requirements, boundaries) VALUES (%s, %s, %s, %s)",
                    (project_id, sections_json, requirements_json, boundaries_json)
                )
            if requirement_matches:
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS requirement_matches (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL,
                    requirement_id TEXT NOT NULL,
                    source_file TEXT,
                    keyword TEXT,
                    context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
                ''')
                cursor.execute(
                    "DELETE FROM requirement_matches WHERE project_id = %s",
                    (project_id,)
                )
                for req_id, matches in requirement_matches.items():
                    for match in matches:
                        cursor.execute(
                            """
                            INSERT INTO requirement_matches 
                            (project_id, requirement_id, source_file, keyword, context) 
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            (
                                project_id,
                                req_id,
                                match.get('source_file', ''),
                                match.get('keyword', ''),
                                match.get('context', '')
                            )
                        )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error storing SOW data: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)

    def get_project_sow_data(self, project_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "SELECT * FROM sow_data WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (project_id,)
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            self.release_connection(conn)
            return None
        result = {
            'id': row['id'],
            'project_id': row['project_id'],
            'sections': json.loads(row['sections']),
            'requirements': json.loads(row['requirements']),
            'boundaries': json.loads(row['boundaries']),
            'created_at': row['created_at']
        }
        cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'requirement_matches'"
        )
        if cursor.fetchone():
            cursor.execute(
                "SELECT * FROM requirement_matches WHERE project_id = %s",
                (project_id,)
            )
            requirement_matches = {}
            for match_row in cursor.fetchall():
                req_id = match_row['requirement_id']
                if req_id not in requirement_matches:
                    requirement_matches[req_id] = []
                requirement_matches[req_id].append({
                    'source_file': match_row['source_file'],
                    'keyword': match_row['keyword'],
                    'context': match_row['context']
                })
            result['requirement_matches'] = requirement_matches
        cursor.close()
        self.release_connection(conn)
        return result
    
    def store_questions(self, questions: List[Dict[str, Any]], project_id: Optional[int] = None) -> List[int]:
        if not questions:
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            question_ids = []
            for q in questions:
                q_project_id = project_id if project_id is not None else q.get('project_id')
                if not q_project_id:
                    continue
                try:
                    cursor.execute(
                        """
                        INSERT INTO questions 
                        (project_id, parent_question_id, question, context, source, source_text, priority, status) 
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                        """,
                        (
                            q_project_id,
                            q.get('parent_question_id'),
                            q.get('question', ''),
                            q.get('context', ''),
                            q.get('source', ''),
                            q.get('source_text', ''),
                            q.get('priority', 3),
                            q.get('status', 'unanswered')
                        )
                    )
                    question_ids.append(cursor.fetchone()[0])
                except Exception as e:
                    print(f"Error storing question: {str(e)}")
            conn.commit()
            return question_ids
        except Exception as e:
            print(f"Error storing questions: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def get_question(self, question_id: int) -> Optional[Dict[str, Any]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT * FROM questions WHERE id = %s", (question_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {key: row[key] for key in row.keys()}
        except Exception as e:
            print(f"Error fetching question: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def get_unanswered_questions(self, project_id: int) -> List[Dict[str, Any]]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT * FROM questions WHERE project_id = %s AND status = 'unanswered' ORDER BY priority",
                (project_id,)
            )
            rows = cursor.fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]
        except Exception as e:
            print(f"Error fetching question: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def update_question_status(self, question_id: int, status: str, answer: Optional[str] = None) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE questions SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status, question_id)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating question status: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def store_transcript(self, project_id: int, transcript_text: str) -> int:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transcripts (project_id, transcript_text) VALUES (%s, %s) RETURNING id",
                (project_id, transcript_text)
            )
            transcript_id = cursor.fetchone()[0]
            conn.commit()
            return transcript_id
        except Exception as e:
            print(f"Error storing transcript: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def store_answer(self, question_id: int, transcript_id: int, answer_text: str, confidence: float = 0.0) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO answers (question_id, transcript_id, answer_text, confidence) VALUES (%s, %s, %s, %s)",
                (question_id, transcript_id, answer_text, confidence)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error storing answer: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def store_new_information(self, project_id: int, new_info: List[Dict[str, Any]], transcript_id: Optional[int] = None) -> List[int]:
        if not new_info:
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            info_ids = []
            for info in new_info:
                try:
                    cursor.execute(
                        """
                        INSERT INTO new_information 
                        (project_id, transcript_id, topic, transcript_excerpt, impact, priority) 
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                        """,
                        (
                            project_id,
                            transcript_id,
                            info.get('topic', ''),
                            info.get('transcript_excerpt', ''),
                            info.get('impact', ''),
                            info.get('priority', 3)
                        )
                    )
                    info_ids.append(cursor.fetchone()[0])
                except Exception as e:
                    print(f"Error storing new information: {str(e)}")
            conn.commit()
            return info_ids
        except Exception as e:
            print(f"Error storing new information: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def get_discovery_status(self, project_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, COUNT(*) as count FROM questions WHERE project_id = %s GROUP BY status",
            (project_id,)
        )
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT COUNT(*) FROM transcripts WHERE project_id = %s",
            (project_id,)
        )
        transcript_count = cursor.fetchone()[0]
        total_questions = sum(status_counts.values())
        unanswered = status_counts.get('unanswered', 0) + status_counts.get('partially_answered', 0)
        discovery_complete = unanswered == 0 and total_questions > 0
        cursor.close()
        self.release_connection(conn)
        return {
            'project_id': project_id,
            'total_questions': total_questions,
            'question_status': status_counts,
            'transcript_count': transcript_count,
            'discovery_complete': discovery_complete
        }

    def initialize_enhanced_schema(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS additional_documents (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            s3_key VARCHAR(1024),  -- Added s3_key column
            file_size INTEGER,
            upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_date TIMESTAMP,
            processing_status TEXT DEFAULT 'pending',
            content_extracted TEXT,
            answers_found INTEGER DEFAULT 0,
            questions_generated INTEGER DEFAULT 0,
            requirement_matches INTEGER DEFAULT 0,
            notes TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_answers (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            document_id INTEGER NOT NULL,
            answer_text TEXT,
            confidence FLOAT DEFAULT 0.0,
            document_section TEXT,
            extraction_method TEXT DEFAULT 'gemini',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_questions (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            source_document_id INTEGER NOT NULL,
            document_section TEXT,
            generated_from TEXT,
            relevance_score FLOAT DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id),
            FOREIGN KEY (source_document_id) REFERENCES additional_documents(id)
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_processing_log (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            document_id INTEGER,
            processing_step TEXT NOT NULL,
            processing_status TEXT NOT NULL,
            processing_details TEXT,
            error_message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        conn.commit()
        cursor.close()
        self.release_connection(conn)

    def store_additional_document(self, project_id: int, filename: str, filepath: str, 
                                file_size: int = 0, notes: str = "", s3_key: str = "") -> int:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO additional_documents 
                (project_id, original_filename, file_path, s3_key, file_size, notes) 
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (project_id, filename, filepath, s3_key, file_size, notes)
            )
            document_id = cursor.fetchone()[0]
            conn.commit()
            return document_id
        except Exception as e:
            print(f"Error storing additional document: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)

    def update_document_processing_status(self, document_id: int, status: str, 
                                        answers_found: int = 0, questions_generated: int = 0,
                                        requirement_matches: int = 0) -> bool:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE additional_documents 
                SET processing_status = %s, processed_date = CURRENT_TIMESTAMP,
                    answers_found = %s, questions_generated = %s, requirement_matches = %s
                WHERE id = %s
                """,
                (status, answers_found, questions_generated, requirement_matches, document_id)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error updating document processing status: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def store_document_answer(self, question_id: int, document_id: int, answer_text: str, 
                            confidence: float = 0.0, document_section: str = "") -> bool:
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO document_answers 
                (question_id, document_id, answer_text, confidence, document_section) 
                VALUES (%s, %s, %s, %s, %s)
                """,
                (question_id, document_id, answer_text, confidence, document_section)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error storing document answer: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def get_document_processing_summary(self, project_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT 
                COUNT(*) as total_documents,
                COUNT(CASE WHEN processing_status = 'completed' THEN 1 END) as completed,
                COUNT(CASE WHEN processing_status = 'pending' THEN 1 END) as pending,
                COUNT(CASE WHEN processing_status = 'failed' THEN 1 END) as failed,
                SUM(answers_found) as total_answers_found,
                SUM(questions_generated) as total_questions_generated,
                SUM(requirement_matches) as total_requirement_matches
            FROM additional_documents 
            WHERE project_id = %s
            """,
            (project_id,)
        )
        summary = cursor.fetchone()
        cursor.execute(
            """
            SELECT original_filename, processing_status, upload_date, 
                answers_found, questions_generated
            FROM additional_documents 
            WHERE project_id = %s 
            ORDER BY upload_date DESC 
            LIMIT 10
            """,
            (project_id,)
        )
        recent_documents = [{key: row[key] for key in row.keys()} for row in cursor.fetchall()]
        cursor.close()
        self.release_connection(conn)
        return {
            'project_id': project_id,
            'summary': {key: summary[key] for key in summary.keys()} if summary else {},
            'recent_documents': recent_documents
        }

    def log_processing_step(self, project_id: int, document_id: int, step: str, 
                        status: str, details: str = "", error: str = "") -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO document_processing_log 
                (project_id, document_id, processing_step, processing_status, 
                processing_details, error_message) 
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (project_id, document_id, step, status, details, error)
            )
            conn.commit()
            return True
        except Exception as e:
            print(f"Error logging processing step: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def get_questions_by_source(self, project_id: int) -> Dict[str, List]:
        conn = self._get_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            """
            SELECT q.*, ad.original_filename as source_document_name
            FROM questions q
            LEFT JOIN additional_documents ad ON q.source_document_id = ad.id
            WHERE q.project_id = %s
            ORDER BY q.created_at DESC
            """,
            (project_id,)
        )
        all_questions = cursor.fetchall()
        cursor.close()
        self.release_connection(conn)
        grouped = {
            'sow_questions': [],
            'document_questions': [],
            'transcript_questions': [],
            'other_questions': []
        }
        for q in all_questions:
            question_data = {key: q[key] for key in q.keys()}
            source_type = q.get('source_type', 'sow')
            if source_type == 'sow':
                grouped['sow_questions'].append(question_data)
            elif source_type == 'document':
                grouped['document_questions'].append(question_data)
            elif source_type == 'transcript':
                grouped['transcript_questions'].append(question_data)
            else:
                grouped['other_questions'].append(question_data)
        return grouped