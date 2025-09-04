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
#Implementation using metaclass for cleaner singleton pattern
class SingletonMeta(type):
    _instances = {}
    _lock = threading.Lock()
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            with cls._lock:
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
class DiscoveryDatabase(metaclass=SingletonMeta):
#class DiscoveryDatabase():
    def __init__(self):
        # if hasattr(self, '_initialized'):
        #     return  # Prevent re-initialization
        
        DB_CONFIG = {
            'host': os.getenv('DB_HOST'),
            'database': os.getenv('DB_NAME'),
            'user': os.getenv('DB_USER'),
            'password': os.getenv('DB_PASSWORD'),
            'port': os.getenv('DB_PORT')
        }
        """Initialize database connection and create tables if they don't exist"""
        self.DB_CONFIG = DB_CONFIG
        #print("DBConfig---->",self.DB_CONFIG)
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                os.getenv('POOL_MIN_SIZE', 1), os.getenv('POOL_MAX_SIZE', 10),  # min and max connections
                **self.DB_CONFIG
            )
            self.initialize_db()
            self._initialized = True
            #self.initialize_enhanced_schema()
        except Exception as e:
            print(f"DiscoveryDatabaseV2: Error initializing database: {e}")
            raise

    def _get_connection(self):
        """Get a database connection with row factory enabled"""
        return self.connection_pool.getconn()
    def release_connection(self, conn):
        """Release a database connection back to the pool"""
        self.connection_pool.putconn(conn)
    def initialize_db(self):
        """Create database tables if they don't exist"""
        #conn = self._get_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Projects table
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
        
        # SOW data table (stores parsed SOW data)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS sow_data (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            sections TEXT,  -- JSON string of sections
            requirements TEXT,  -- JSON string of requirements
            boundaries TEXT,  -- JSON string of boundaries
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id) 
            ON DELETE CASCADE
        )
        ''')
        
        # Questions table
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
            FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE CASCADE,
            FOREIGN KEY (parent_question_id) REFERENCES questions(id)
            ON DELETE CASCADE
        )
        ''')
        
        # Meetings/lTranscripts table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS transcripts (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            meeting_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            transcript_text TEXT,
            processed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE CASCADE
        )
        ''')
        
        # Answers table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS answers (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            transcript_id INTEGER NOT NULL,
            answer_text TEXT,
            confidence FLOAT DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id)
            ON DELETE CASCADE,
            FOREIGN KEY (transcript_id) REFERENCES transcripts(id)
            ON DELETE CASCADE
        )
        ''')
        
        # New Information table (for additional topics from transcripts)
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
            FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE CASCADE,
            FOREIGN KEY (transcript_id) REFERENCES transcripts(id)
            ON DELETE CASCADE
        )
        ''')
        # Additional Documents table - separate from transcripts for better tracking
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS additional_documents (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
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
            ON DELETE CASCADE
        )
        ''')
        
        # Document Answers table - link answers specifically to documents
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
            FOREIGN KEY (question_id) REFERENCES questions(id)
            ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
            ON DELETE CASCADE
        )
        ''')
        
        # Document Questions table - track questions generated from documents
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS document_questions (
            id SERIAL PRIMARY KEY,
            question_id INTEGER NOT NULL,
            source_document_id INTEGER NOT NULL,
            document_section TEXT,
            generated_from TEXT,
            relevance_score FLOAT DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (question_id) REFERENCES questions(id)
            ON DELETE CASCADE,
            FOREIGN KEY (source_document_id) REFERENCES additional_documents(id)
            ON DELETE CASCADE
        )
        ''')
        
        # Document Processing Log table - for debugging and tracking
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
            FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
            ON DELETE CASCADE
        )
        ''')
        # Requirement Matches table
        # This table will store matches found in SOW documents
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
                    ON DELETE CASCADE
                )
                ''')
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_project_owner ON projects(project_owner)
        ''')
        conn.commit()
        #conn.close()
        cursor.close()
        self.release_connection(conn) 
    
    def list_projects_byowner(self, email) -> List[str]:
        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT id, name FROM projects where project_owner=%s", (email,))
            projects = cursor.fetchall()
            #conn.close()
            if not projects:
                return []
            return projects
        except Exception as e:
            print(f"Error getting project data: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)



    def create_project(self, name: str, sow_path: Optional[str] = None, project_owner=None) -> int:
        """Create a new project and return its ID"""
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
        #conn.close()
        finally:
            cursor.close()
            self.release_connection(conn)
        
    
    def get_project_info(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get project information by ID"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            cursor.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            
            row = cursor.fetchone()
            #conn.close()
            
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
    def delete_project_by_id(self, project_id):
        """Delete a project and its associated collection"""
        try:

            conn = self._get_connection()
            cursor = conn.cursor()

            # Check if project exists
            cursor.execute("SELECT id, name FROM projects WHERE id = %s", (project_id,))
            
            project = cursor.fetchone()
            if not project:
                #conn.close()
                cursor.close()
                self.release_connection(conn)
                return None

            # Check if requirement_matches table exists
            # cursor.execute("""
            #     SELECT table_name FROM information_schema.tables 
            #     WHERE table_schema = 'public' AND table_name = 'requirement_matches'
            # """)
            # if cursor.fetchone():
            #     cursor.execute("DELETE FROM requirement_matches WHERE project_id = %s", (project_id,))

            # # Delete related data in order to avoid foreign key errors
            # #=================Do not change the order of deletion========================
            # cursor.execute("DELETE FROM sow_data WHERE project_id = %s", (project_id,))
            # cursor.execute("DELETE FROM answers WHERE question_id IN (SELECT id FROM questions WHERE project_id = %s)", (project_id,))
            # cursor.execute("DELETE FROM document_answers WHERE document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
            # cursor.execute("DELETE FROM document_questions WHERE source_document_id IN (SELECT id FROM additional_documents WHERE project_id = %s)", (project_id,))
            # cursor.execute("DELETE FROM document_processing_log WHERE project_id = %s", (project_id,))
            # cursor.execute("DELETE FROM new_information WHERE project_id = %s", (project_id,))
            # cursor.execute("DELETE FROM transcripts WHERE project_id = %s", (project_id,))
            # cursor.execute("DELETE FROM additional_documents WHERE project_id = %s", (project_id,))
            # cursor.execute("DELETE FROM questions WHERE project_id = %s", (project_id,))
            cursor.execute("DELETE FROM projects WHERE id = %s", (project_id,))

            conn.commit()
            return  project_id
        except Exception as e:
            print(f"Error deleting project {project_id}: {str(e)}")  
            conn.rollback()
            raise
        finally:
            cursor.close() 
            self.release_connection(conn)



    def store_sow_data(self, project_id: int, sow_data: Dict[str, Any]) -> bool:
        """Store parsed SOW data for a project"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Extract requirement matches if present
            requirement_matches = sow_data.pop('requirement_matches', {}) if 'requirement_matches' in sow_data else {}
            
            # Convert dictionaries to JSON strings
            sections_json = json.dumps(sow_data.get('sections', {}))
            requirements_json = json.dumps(sow_data.get('requirements', []))
            boundaries_json = json.dumps(sow_data.get('boundaries', {}))
            
            # Add requirement_matches back to data
            if requirement_matches:
                sow_data['requirement_matches'] = requirement_matches
            
            # Check if SOW data already exists for this project
            cursor.execute(
                "SELECT id FROM sow_data WHERE project_id = %s",
                (project_id,)
            )
            existing_id = cursor.fetchone()
            
            if existing_id:
                # Update existing SOW data
                cursor.execute(
                    "UPDATE sow_data SET sections = %s, requirements = %s, boundaries = %s WHERE id = %s",
                    (sections_json, requirements_json, boundaries_json, existing_id[0])
                )
            else:
                # Insert new SOW data
                cursor.execute(
                    "INSERT INTO sow_data (project_id, sections, requirements, boundaries) VALUES (%s, %s, %s, %s)",
                    (project_id, sections_json, requirements_json, boundaries_json)
                )
            
            # Store requirement matches in a separate table if they exist
            if requirement_matches:
                # Check if requirement_matches table exists, if not create it
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
                    ON DELETE CASCADE
                )
                ''')
                
                # Clear any existing matches for this project to avoid duplicates
                cursor.execute(
                    "DELETE FROM requirement_matches WHERE project_id = %s",
                    (project_id,)
                )
                
                # Store the new matches
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
            #conn.close()
            return True
        
        except Exception as e:
            print(f"Error storing SOW data: {str(e)}")
            #conn.close()
            conn.rollback()
            raise
            return False
        finally:
            cursor.close()
            self.release_connection(conn)
    def get_project_sow_data(self, project_id: int) -> Optional[Dict[str, Any]]:
        """Get SOW data for a project including requirement matches"""
        conn = self._get_connection()
        #conn.row_factory = sqlite3.Row
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        #cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM sow_data WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (project_id,)
        )
        
        row = cursor.fetchone()
        print("----->>>>",type(row)) 
        if not row:
            #conn.close()
            cursor.close()
            self.release_connection(conn) 
            return None
        
        # Parse JSON strings back to dictionaries
        result = {
            'id': row['id'],
            'project_id': row['project_id'],
            'sections': json.loads(row['sections']),
            'requirements': json.loads(row['requirements']),
            'boundaries': json.loads(row['boundaries']),
            'created_at': row['created_at']
        }
        
        # Check if requirement_matches table exists
        cursor.execute(
            #"SELECT name FROM sqlite_master WHERE type='table' AND name='requirement_matches'"
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'requirement_matches'"
        )
        if cursor.fetchone():
            # Get requirement matches for this project
            cursor.execute(
                "SELECT * FROM requirement_matches WHERE project_id = %s",
                (project_id,)
            )
            
            # Organize matches by requirement ID
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
        
        #conn.close()
        cursor.close()
        self.release_connection(conn) 
        return result
    
    def store_questions(self, questions: List[Dict[str, Any]], project_id: Optional[int] = None) -> List[int]:
        """Store questions and return their IDs"""
        if not questions:
            return []
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            question_ids = []
            
            for q in questions:
                # Ensure we have a project_id
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
            #conn.close()        
            return question_ids
        except Exception as e:
            print(f"Error storing questions: {str(e)}")
            
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def get_question(self, question_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific question by ID"""
        try:

            conn = self._get_connection()
            # conn.row_factory = sqlite3.Row
            # cursor = conn.cursor()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            cursor.execute("SELECT * FROM questions WHERE id = %s", (question_id,))
            
            row = cursor.fetchone()
            #conn.close()
            
            if not row:
                return None
            
            return {key: row[key] for key in row.keys()}
        except Exception as e:
            print(f"Error fetching question: {str(e)}")
            conn
            raise
        finally:
            cursor.close()
            self.release_connection(conn)    
    def get_unanswered_questions(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all unanswered questions for a project"""
        try:

            conn = self._get_connection()
            # conn.row_factory = sqlite3.Row
            # cursor = conn.cursor()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(
                "SELECT * FROM questions WHERE project_id = %s AND status = 'unanswered' ORDER BY priority",
                (project_id,)
            )
        
            rows = cursor.fetchall()
            #conn.close()
        
            return [{key: row[key] for key in row.keys()} for row in rows]
        except Exception as e:
            print(f"Error fetching question: {str(e)}")
            conn.rollback() 
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
        
    def update_question_status(self, question_id: int, status: str, answer: Optional[str] = None) -> bool:
        """Update a question's status"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "UPDATE questions SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (status, question_id)
            )
            
            conn.commit()
            #conn.close()
            return True
        
        except Exception as e:
            print(f"Error updating question status: {str(e)}")
            #conn.close()
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def store_transcript(self, project_id: int, transcript_text: str) -> int:
        """Store a meeting transcript and return its ID"""
        try:

            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "INSERT INTO transcripts (project_id, transcript_text) VALUES (%s, %s) RETURNING id",
                (project_id, transcript_text)
            )
            
            transcript_id = cursor.fetchone()[0]
            conn.commit()
            #conn.close()
            return transcript_id
        except Exception as e:
            print(f"Error storing transcript: {str(e)}")
            conn.rollback()
            raise
        finally:
            cursor.close()
            self.release_connection(conn)
    
    def store_answer(self, question_id: int, transcript_id: int, answer_text: str, confidence: float = 0.0) -> bool:
        """Store an answer extracted from a transcript"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "INSERT INTO answers (question_id, transcript_id, answer_text, confidence) VALUES (%s, %s, %s, %s)",
                (question_id, transcript_id, answer_text, confidence)
            )
            
            conn.commit()
            
            #conn.close()
            return True
        
        except Exception as e:
            print(f"Error storing answer: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)
    def store_new_information(self, project_id: int, new_info: List[Dict[str, Any]], transcript_id: Optional[int] = None) -> List[int]:
        """Store new information topics identified from transcripts"""
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
            #conn.close()
            
            return info_ids
        except Exception as e:
            print(f"Error storing new information: {str(e)}")
            conn.rollback()
            raise
        finally:    
            cursor.close()
            self.release_connection(conn)
            
    
    def get_discovery_status(self, project_id: int) -> Dict[str, Any]:
        """Get the current status of the discovery process for a project"""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Count questions by status
        cursor.execute(
            "SELECT status, COUNT(*) as count FROM questions WHERE project_id = %s GROUP BY status",
            (project_id,)
        )
        status_counts = {row[0]: row[1] for row in cursor.fetchall()}
        
        # Count total questions
        total_questions = sum(status_counts.values())
        
        # Count transcripts
        cursor.execute(
            "SELECT COUNT(*) FROM transcripts WHERE project_id = %s",
            (project_id,)
        )
        transcript_count = cursor.fetchone()[0]
        
        # Check if there are any unanswered questions
        unanswered = status_counts.get('unanswered', 0) + status_counts.get('partially_answered', 0)
        discovery_complete = unanswered == 0 and total_questions > 0
        
        #conn.close()
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
        """Add new tables to support additional document processing"""
        #conn = self._get_connection()
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(questions)")
        columns = [column[1] for column in cursor.fetchall()]
        # Additional Documents table - separate from transcripts for better tracking
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS additional_documents (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
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
            ON DELETE CASCADE
        )
        ''')
        
        # Document Answers table - link answers specifically to documents
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
            FOREIGN KEY (question_id) REFERENCES questions(id)
            ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        
        # Document Questions table - track questions generated from documents
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
        
        # Document Processing Log table - for debugging and tracking
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
            FOREIGN KEY (project_id) REFERENCES projects(id)
            ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES additional_documents(id)
        )
        ''')
        
        # Enhanced Questions table - add columns for better tracking
        # cursor.execute('''
        # ALTER TABLE questions ADD COLUMN source_type TEXT DEFAULT 'sow'
        # ''')
        
        # cursor.execute('''
        # ALTER TABLE questions ADD COLUMN source_document_id INTEGER
        # ''')
        
        # cursor.execute('''
        # ALTER TABLE questions ADD COLUMN confidence_score FLOAT DEFAULT 0.0
        # ''')
        
        conn.commit()
        conn.close()

    def store_additional_document(self, project_id: int, filename: str, filepath: str, 
                                file_size: int = 0, notes: str = "") -> int:
        """Store additional document information"""
        try:

            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO additional_documents 
                (project_id, original_filename, file_path, file_size, notes) 
                VALUES (%s, %s, %s, %s, %s) RETURNING id
                """,
                (project_id, filename, filepath, file_size, notes)
            )
            
            document_id = cursor.fetchone()[0]
            conn.commit()
            #conn.close()
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
        """Update document processing status and results"""
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
            #conn.close()
            return True
        except Exception as e:
            print(f"Error updating document processing status: {str(e)}")
            #conn.close()
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def store_document_answer(self, question_id: int, document_id: int, answer_text: str, 
                            confidence: float = 0.0, document_section: str = "") -> bool:
        """Store answer extracted from a document"""
        
        
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
            #conn.close()
            return True
        
        except Exception as e:
            print(f"Error storing document answer: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def get_document_processing_summary(self, project_id: int) -> Dict[str, Any]:
        """Get summary of all additional document processing for a project"""
        conn = self._get_connection()
        # conn.row_factory = sqlite3.Row
        # cursor = conn.cursor()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        # Get document processing summary
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
        
        # Get recent documents
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
        
        #conn.close()
        cursor.close()
        self.release_connection(conn) 
        return {
            'project_id': project_id,
            'summary': {key: summary[key] for key in summary.keys()} if summary else {},
            'recent_documents': recent_documents
        }

    def log_processing_step(self, project_id: int, document_id: int, step: str, 
                        status: str, details: str = "", error: str = "") -> bool:
        """Log document processing steps for debugging"""
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
            #conn.close()
            return True
        
        except Exception as e:
            print(f"Error logging processing step: {str(e)}")
            conn.rollback()
            return False
        finally:
            cursor.close()
            self.release_connection(conn)

    def get_questions_by_source(self, project_id: int) -> Dict[str, List]:
        """Get questions grouped by their source (SOW, documents, transcripts)"""
        conn = self._get_connection()
        #conn.row_factory = sqlite3.Row
        #cursor = conn.cursor()
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
        #conn.close()
        cursor.close()
        self.release_connection(conn) 
        # Group questions by source
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