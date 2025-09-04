import os
import google.generativeai as genai
from typing import List, Dict, Any, Optional
import json
import re

from dotenv import dotenv_values


class TranscriptAnalyzer:
    def __init__(self, db_connection, gemini_api_key=None):
        """Initialize the TranscriptAnalyzer"""
        
        resolved_gemini_api_key = None

        # Priority 1: Use the API key passed directly to the constructor
        if gemini_api_key:
            resolved_gemini_api_key = gemini_api_key
        else:
            # Priority 2: If no key was passed to the constructor,
            # then ONLY attempt to read from the .env file.
            # dotenv_values() reads the .env file and returns a dict; it does NOT modify os.environ.
            env_vars_from_file = dotenv_values() 

            if "GOOGLE_API_KEY" in env_vars_from_file:
                resolved_gemini_api_key = env_vars_from_file["GOOGLE_API_KEY"]
            
        # Ensure an API key was found from either the constructor argument or the .env file
        if not resolved_gemini_api_key:
            raise ValueError(
                "No Gemini API key provided. "
                "Please set GOOGLE_API_KEY in your .env file "
                "or pass it directly to the TranscriptAnalyzer constructor."
            )
            
        genai.configure(api_key=resolved_gemini_api_key)
            
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.db = db_connection
        # Store the key that was actually used
        self.gemini_api_key = resolved_gemini_api_key 
        
    def process_transcript(self, project_id: int, transcript_text: str) -> Dict[str, Any]:
        """
        Process a meeting transcript to extract answers to existing questions
        
        Args:
            project_id: ID of the project
            transcript_text: Text of the meeting transcript
            
        Returns:
            Dictionary containing processing results
        """
        # Get unanswered questions for the project
        unanswered_questions = self.db.get_unanswered_questions(project_id)
        if not unanswered_questions:
            return {"status": "no_questions", "answers_found": 0}
        
        # Store transcript
        transcript_id = self.db.store_transcript(project_id, transcript_text)
        
        # Extract answers from transcript
        answers = self.extract_answers(transcript_text, unanswered_questions)
        
        # Store answers in database
        answer_count = 0
        for answer in answers:
            stored = self.db.store_answer(
                question_id=answer['question_id'],
                transcript_id=transcript_id,
                answer_text=answer['answer'],
                confidence=answer['confidence']
            )
            if stored:
                answer_count += 1
        
        # Identify new information that might require new questions
        new_info = self.identify_new_information(project_id, transcript_text)
        
        return {
            "status": "success",
            "transcript_id": transcript_id,
            "answers_found": answer_count,
            "new_info_topics": new_info
        }
    
    def extract_answers(self, transcript_text: str, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract answers to specific questions from transcript
        
        Args:
            transcript_text: Text of the meeting transcript
            questions: List of questions to find answers for
            
        Returns:
            List of answers with question IDs and confidence scores
        """
        answers = []
        
        # Process in smaller batches to avoid token limits
        batch_size = 5
        for i in range(0, len(questions), batch_size):
            batch_questions = questions[i:i+batch_size]
            
            # Create question list for prompt
            questions_text = ""
            for idx, q in enumerate(batch_questions):
                questions_text += f"{idx+1}. {q['question']}\n"
            
            prompt = f"""
            You are analyzing a meeting transcript to find answers to specific questions.
            
            QUESTIONS:
            {questions_text}
            
            TRANSCRIPT:
            {transcript_text}
            
            For each question, determine if there is a relevant answer in the transcript.
            Format your response as a JSON array of objects with these keys:
            - question_index: The number of the question (1-based)
            - answer_found: Boolean indicating if an answer was found
            - answer: The extracted answer (if found)
            - confidence: A value from 0.0 to 1.0 indicating confidence in the answer
            - explanation: Brief explanation for your confidence score
            
            Only extract direct answers from the transcript. If no clear answer is provided for a question, mark it as not found.
            """
            
            response = self.model.generate_content(prompt)
            
            try:
                # Parse answers from response
                batch_answers = self._parse_answers_from_response(response.text)
                
                # Map back to original question IDs
                for answer in batch_answers:
                    if answer.get('answer_found', False):
                        q_idx = answer.get('question_index', 0) - 1
                        if 0 <= q_idx < len(batch_questions):
                            answers.append({
                                'question_id': batch_questions[q_idx].get('id'),
                                'answer': answer.get('answer', ''),
                                'confidence': answer.get('confidence', 0.0)
                            })
            
            except Exception as e:
                print(f"Error extracting answers from batch: {str(e)}")
        
        return answers
    
    def identify_new_information(self, project_id: int, transcript_text: str) -> List[Dict[str, Any]]:
        """
        Identify new information in transcript that might require new questions
        
        Args:
            project_id: ID of the project
            transcript_text: Text of the meeting transcript
            
        Returns:
            List of new information topics
        """
        # Get project SOW data
        sow_data = self.db.get_project_sow_data(project_id)
        if not sow_data:
            return []
        
        # Create summary of SOW scope and boundaries
        scope_summary = ""
        if 'boundaries' in sow_data:
            in_scope = sow_data['boundaries'].get('in_scope', [])
            out_scope = sow_data['boundaries'].get('out_of_scope', [])
            
            if in_scope:
                scope_summary += "IN SCOPE:\n" + "\n".join([f"- {item}" for item in in_scope]) + "\n\n"
            
            if out_scope:
                scope_summary += "OUT OF SCOPE:\n" + "\n".join([f"- {item}" for item in out_scope])
        
        prompt = f"""
        You are analyzing a meeting transcript to identify new information that wasn't previously captured in the project's scope.
        
        PROJECT SCOPE:
        {scope_summary}
        
        TRANSCRIPT:
        {transcript_text[:30000]}  # Using first 30000 chars for analysis
        
        Identify any new information, topics, or requirements mentioned in the transcript that:
        1. Are not clearly within the defined scope
        2. Represent potential new requirements
        3. Could modify existing requirements or scope
        4. Might represent scope creep
        
        Format your response as a JSON array of objects with these keys:
        - topic: Brief description of the new information or topic
        - transcript_excerpt: The relevant excerpt from the transcript
        - impact: How this might impact project scope ("new_requirement", "scope_change", "clarification", "scope_creep")
        - priority: A value from 1-3 (1 being highest priority) for addressing this new info
        """
        
        response = self.model.generate_content(prompt)
        
        try:
            # Extract JSON from response
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, response.text)
            
            if json_match:
                new_info = json.loads(json_match.group(1))
            else:
                # Try direct JSON parsing
                try:
                    new_info = json.loads(response.text)
                except:
                    # Fall back to simple parsing
                    new_info = []
                    
            # Store new information in database
            if new_info and hasattr(self.db, 'store_new_information'):
                self.db.store_new_information(project_id, new_info)
            
            return new_info
            
        except Exception as e:
            print(f"Error identifying new information: {str(e)}")
            return []
    
    def _parse_answers_from_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse answers from Gemini response text"""
        answers = []
        
        try:
            # Try to extract JSON from the response
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, response_text)
            
            if json_match:
                answers = json.loads(json_match.group(1))
            else:
                # Try direct JSON parsing
                try:
                    answers = json.loads(response_text)
                except:
                    # Fall back to simple parsing
                    lines = response_text.split('\n')
                    current_answer = {}
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        if line.startswith('Question') or line.startswith('- Question'):
                            # Save previous answer if it exists
                            if current_answer and 'question_index' in current_answer:
                                answers.append(current_answer)
                            
                            # Start a new answer
                            q_match = re.search(r'Question\s+(\d+)', line)
                            if q_match:
                                current_answer = {'question_index': int(q_match.group(1))}
                            else:
                                current_answer = {}
                        elif ':' in line and current_answer:
                            key, value = line.split(':', 1)
                            key = key.strip().lower()
                            if key in ['answer_found', 'answer', 'confidence', 'explanation']:
                                if key == 'answer_found':
                                    current_answer[key] = 'true' in value.lower() or 'yes' in value.lower()
                                elif key == 'confidence':
                                    try:
                                        current_answer[key] = float(value.strip())
                                    except:
                                        current_answer[key] = 0.5
                                else:
                                    current_answer[key] = value.strip()
                    
                    # Add the last answer
                    if current_answer and 'question_index' in current_answer:
                        answers.append(current_answer)
        
        except Exception as e:
            print(f"Error parsing answers from response: {str(e)}")
        
        return answers