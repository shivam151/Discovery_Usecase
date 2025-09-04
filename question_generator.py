import os
import google.generativeai as genai
from typing import List, Dict, Any, Optional
import json
import re
import time
import traceback
import numpy as np
from collections import defaultdict
from dotenv import load_dotenv
import logging
import requests
# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class QuestionGenerator:
    def __init__(self, db_connection, gemini_api_key: Optional[str] = None, inference_api_url: str = "http://localhost:5000"):
        """Initialize the QuestionGenerator with database connection and Gemini API."""
        # Set API key for Gemini
        if not gemini_api_key:
            gemini_api_key = os.getenv("GOOGLE_API_KEY")
            if not gemini_api_key:
                logger.error("Google API key not provided and not found in environment variables")
                raise ValueError("Google API key is required")
        genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.db = db_connection
        self.inference_api_url = inference_api_url

    def get_text_embedding(self, text: str) -> np.ndarray:
        """Get text embedding from the inference API or return zero vector as fallback."""
        try:
            logger.info(f"Getting text embedding for text of length {len(text)}")
            response = requests.post(
                f"{self.inference_api_url}/embed_text",
                json={'text': text},
                timeout=10
            )
            if response.status_code == 200:
                result = response.json()
                logger.info("Successfully got embedding from API")
                return np.array(result['embedding'], dtype=np.float32)
            else:
                logger.warning(f"API Error ({response.status_code}): {response.text}. Falling back to zero vector.")
                return np.zeros((384,), dtype=np.float32)
        except Exception as e:
            logger.error(f"Error getting text embedding: {str(e)}. Falling back to zero vector.")
            return np.zeros((384,), dtype=np.float32)

    def _categorize_requirements(self, requirements: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Categorize requirements by clarity and ambiguity type.
        
        Args:
            requirements: List of requirement dictionaries
            
        Returns:
            Dictionary with categorized requirements
        """
        categories = {
            "clear": [],
            "ambiguous": {
                "vague_language": [],
                "missing_criteria": [],
                "undefined_terms": [],
                "scope_issues": [],
                "format_missing": [],
                "other": []
            }
        }
        
        ambiguity_patterns = {
            "vague_language": [
                "vague", "high-level", "lacks specific", "subjective", "unclear", 
                "lacks detail", "lack of precision", "not clear", "ambiguous"
            ],
            "missing_criteria": [
                "acceptance criteria", "success criteria", "no criteria", 
                "lacks metrics", "measurements", "how will", "what constitutes"
            ],
            "undefined_terms": [
                "not defined", "undefined", "not specified", "which documents", 
                "what is considered", "term is not", "which stakeholders"
            ],
            "scope_issues": [
                "scope", "boundary", "too broad", "too high level", "not sufficiently detailed",
                "what level of", "depth of"
            ],
            "format_missing": [
                "format", "documentation", "how to document", "level of detail"
            ]
        }
        
        for req in requirements:
            if req.get("clarity") == "clear":
                categories["clear"].append(req)
                continue
            
            reason = req.get("reason", "").lower()
            ambiguity_type = "other"
            for category, patterns in ambiguity_patterns.items():
                if any(pattern in reason for pattern in patterns):
                    ambiguity_type = category
                    break
            categories["ambiguous"][ambiguity_type].append(req)
        
        return categories
    
    def _prioritize_requirements(self, categorized_reqs: Dict) -> List[Dict]:
        """
        Prioritize requirements based on ambiguity type and section.
        
        Args:
            categorized_reqs: Dictionary with categorized requirements
            
        Returns:
            List of prioritized requirements with priority field added
        """
        high_priority_sections = ["Scope of Work", "Deliverables", "Acceptance Criteria"]
        medium_priority_sections = ["Timeline/Schedule", "Assumptions/Constraints"]
        
        ambiguity_priority = {
            "vague_language": 2,
            "missing_criteria": 1,
            "undefined_terms": 2,
            "scope_issues": 1,
            "format_missing": 3,
            "other": 2
        }
        
        prioritized_reqs = []
        for ambiguity_type, reqs in categorized_reqs["ambiguous"].items():
            base_priority = ambiguity_priority.get(ambiguity_type, 2)
            for req in reqs:
                section = req.get("section", "")
                if section in high_priority_sections:
                    req["priority"] = min(base_priority, 2)
                elif section in medium_priority_sections:
                    req["priority"] = min(base_priority + 1, 3)
                else:
                    req["priority"] = min(base_priority + 1, 3)
                prioritized_reqs.append(req)
        
        return sorted(prioritized_reqs, key=lambda x: x.get("priority", 3))
    
    def _create_sow_context(self, sow_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a comprehensive context from SOW data for better question generation."""
        context = {}
        overview_sections = ['Introduction', 'Background', 'Overview', 'Executive Summary', 'Project Summary']
        overview_text = []
        for section_name, section_content in sow_data.get('sections', {}).items():
            if any(keyword.lower() in section_name.lower() for keyword in overview_sections):
                overview_text.append(section_content)
        context['overview'] = '\n'.join(overview_text) if overview_text else "Not available"
        
        deliverables_sections = ['Deliverables', 'Expected Outcomes', 'Results', 'Work Products']
        deliverables_text = []
        for section_name, section_content in sow_data.get('sections', {}).items():
            if any(keyword.lower() in section_name.lower() for keyword in deliverables_sections):
                deliverables_text.append(section_content)
        context['deliverables'] = '\n'.join(deliverables_text) if deliverables_text else "Not specified"
        
        stakeholders_sections = ['Stakeholders', 'Team', 'Roles', 'Responsibilities', 'Organization']
        stakeholders_text = []
        for section_name, section_content in sow_data.get('sections', {}).items():
            if any(keyword.lower() in section_name.lower() for keyword in stakeholders_sections):
                stakeholders_text.append(section_content)
        context['stakeholders'] = '\n'.join(stakeholders_text) if stakeholders_text else "Not specified"
        
        context['project_type'] = self._classify_project_type(sow_data)
        return context
    
    def _classify_project_type(self, sow_data: Dict[str, Any]) -> str:
        """Classify the project type based on SOW data."""
        try:
            sections_text = []
            for section_name, section_content in sow_data.get('sections', {}).items():
                sections_text.append(f"## {section_name}\n{section_content}")
            combined_text = "\n\n".join(sections_text)
            lower_text = combined_text.lower()
            
            if any(term in lower_text for term in ["software", "application", "system", "platform", "code", "development"]):
                return "software_development"
            elif any(term in lower_text for term in ["analysis", "assessment", "discovery", "flowmart modernization"]):
                return "consulting"
            elif any(term in lower_text for term in ["training", "knowledge transfer", "learning"]):
                return "training"
            elif any(term in lower_text for term in ["infrastructure", "hardware", "network", "server"]):
                return "infrastructure"
            return "consulting"
        except Exception as e:
            logger.error(f"Error classifying project type: {str(e)}")
            return "consulting"
    
    def _find_related_requirements(self, req: Dict[str, Any], all_requirements: List[Dict[str, Any]], 
                                 max_related: int = 3) -> List[Dict[str, Any]]:
        """
        Find requirements related to the given requirement.
        
        Args:
            req: The requirement to find related items for
            all_requirements: List of all requirements
            max_related: Maximum number of related requirements to return
            
        Returns:
            List of related requirements
        """
        related = []
        scoring = []
        if not req.get('text'):
            return related
        
        req_id = req.get('id', '')
        req_section = req.get('section', '')
        req_text = req.get('text', '').lower()
        req_words = set(req_text.split())
        
        for other_req in all_requirements:
            other_id = other_req.get('id', '')
            if other_id == req_id:
                continue
                
            other_section = other_req.get('section', '')
            other_text = other_req.get('text', '').lower()
            other_words = set(other_text.split())
            
            score = 0
            if other_section == req_section:
                score += 50
            if other_req.get('clarity') == 'clear':
                score += 25
            common_words = req_words.intersection(other_words)
            if len(req_words) > 0:
                overlap_ratio = len(common_words) / len(req_words)
                score += int(overlap_ratio * 100)
            if req_id and other_id:
                try:
                    req_num = int(req_id.split('-')[-1])
                    other_num = int(other_id.split('-')[-1])
                    if abs(req_num - other_num) <= 2:
                        score += 30
                except ValueError:
                    pass
            
            if score > 0:
                scoring.append((score, other_req))
        
        scoring.sort(reverse=True, key=lambda x: x[0])
        return [item[1] for item in scoring[:max_related]]
    
    def refine_questions(self, questions: List[Dict[str, Any]], batch_size: int = 25) -> List[Dict[str, Any]]:
        """
        Refine generated questions as a senior solution architect.
        
        Args:
            questions: List of question dictionaries to refine
            batch_size: Size of question batches to process at once
            
        Returns:
            List of refined question dictionaries
        """
        logger.info(f"Starting question refinement for {len(questions)} questions in batches of {batch_size}")
        
        refined_questions = []
        grouped_questions = defaultdict(list)
        for q in questions:
            req_id = q.get('requirement_id', q.get('source', 'unknown'))
            grouped_questions[req_id].append(q)
        
        total_input_questions = len(questions)
        total_output_questions = 0
        batch_num = 0
        
        for req_id, req_questions in grouped_questions.items():
            batch_num += 1
            logger.info(f"Processing batch {batch_num}/{len(grouped_questions)}: {req_id} with {len(req_questions)} questions")
            
            if not req_questions:
                continue
            
            req_text = req_questions[0].get('source_text', '')
            req_section = req_questions[0].get('section', '')
            
            prompt = f"""
            Act as a senior solution architect with extensive experience in IT and business consulting. Review and refine these automatically generated questions to ensure they are high-quality, technically precise, and ready for stakeholder discussions.
            
            Context about the requirement these questions address:
            Requirement: {req_text}
            Section: {req_section}
            
            Questions to refine:
            {json.dumps(req_questions, indent=2)}
            
            Your task:
            1. IMPROVE language, clarity, and technical specificity of each question
            2. REMOVE questions that are:
               - Too generic or obvious
               - Redundant with other questions
               - Not relevant to the requirement
               - Not technically focused enough
            3. MERGE questions that overlap or could be more efficiently asked together
            4. CONSOLIDATE to no more than 3-5 high-value questions (unless the requirement is very complex)
            5. PRIORITIZE questions based on technical importance and impact
            
            Specific guidance:
            - Make questions specific, focused, and technical
            - Ensure questions lead to actionable, measurable responses
            - Focus on questions that uncover technical details
            - Each question should target a specific stakeholder role
            - Avoid assuming implementation details
            - Ensure questions are professional and concise
            
            Return ONLY the refined list of questions in JSON format with:
            - question: The refined question text
            - context: Updated context explaining why this question is important
            - priority: Adjusted priority (1-3, 1 being highest)
            - target_stakeholder: The specific role best suited to answer
            """
            
            try:
                logger.info(f"Sending refinement prompt to Gemini API")
                response = self.model.generate_content(prompt)
                logger.info(f"Received response from Gemini API")
                
                refined_batch = self._parse_questions_from_response(response.text)
                logger.info(f"Parsed {len(refined_batch)} refined questions")
                
                for refined_q in refined_batch:
                    original_metadata = {
                        'requirement_id': req_questions[0].get('requirement_id', ''),
                        'source': req_questions[0].get('source', ''),
                        'source_text': req_questions[0].get('source_text', ''),
                        'section': req_questions[0].get('section', ''),
                        'status': 'unanswered'
                    }
                    for key, value in original_metadata.items():
                        if key not in refined_q:
                            refined_q[key] = value
                    refined_questions.append(refined_q)
                total_output_questions += len(refined_batch)
                
                logger.info(f"Batch {batch_num} refinement: {len(req_questions)} input → {len(refined_batch)} output questions")
            except Exception as e:
                logger.error(f"Failed to refine batch {batch_num}: {str(e)}", exc_info=True)
                refined_questions.extend(req_questions)
                total_output_questions += len(req_questions)
        
        logger.info(f"Completed question refinement: {total_input_questions} input → {total_output_questions} output questions")
        logger.info(f"Reduction: {round((1 - total_output_questions/total_input_questions) * 100, 1)}%")
        return refined_questions
    
    def generate_initial_questions(self, sow_data: Dict[str, Any], project_name: str) -> Dict[str, Any]:
        """
        Generate initial questions based on all requirements.
        
        Args:
            sow_data: Dictionary containing SOW sections, requirements, and boundaries
            project_name: Name of the project
            
        Returns:
            Dictionary with generated questions and requirement analysis
        """
        logger.info(f"Starting question generation for project: {project_name}")
        
        requirements = sow_data.get('requirements', [])
        logger.info(f"SOW data contains {len(requirements)} requirements and {len(sow_data.get('boundaries', {}).get('unclear', []))} unclear boundaries")
        
        result = {
            "clear_requirements": [],
            "ambiguous_requirements": [],
            "questions": [],
            "summary": {
                "total_requirements": len(requirements),
                "clear_count": 0,
                "ambiguous_count": 0,
                "questions_count": 0,
                "categories": {}
            }
        }
        
        categorized_reqs = self._categorize_requirements(requirements)
        result["clear_requirements"] = categorized_reqs["clear"]
        result["summary"]["clear_count"] = len(categorized_reqs["clear"])
        
        prioritized_ambiguous_reqs = self._prioritize_requirements(categorized_reqs)
        result["ambiguous_requirements"] = prioritized_ambiguous_reqs
        result["summary"]["ambiguous_count"] = len(prioritized_ambiguous_reqs)
        
        category_counts = {category: len(reqs) for category, reqs in categorized_reqs["ambiguous"].items()}
        result["summary"]["categories"] = category_counts
        
        sow_context = self._create_sow_context(sow_data)
        all_prioritized_reqs = prioritized_ambiguous_reqs + categorized_reqs["clear"]
        requirement_matches = sow_data.get('requirement_matches', {})
        
        questions = []
        req_count = 0
        for req in all_prioritized_reqs:
            req_count += 1
            req_id = req.get('id', 'Unknown')
            logger.info(f"Processing requirement {req_count}/{len(all_prioritized_reqs)}: {req_id}")
            
            related_reqs = self._find_related_requirements(req, requirements)
            related_reqs_text = ""
            if related_reqs:
                related_reqs_text = "Related requirements:\n" + "\n".join([f"- {r.get('id', 'Unknown')}: {r.get('text', '')}" for r in related_reqs])
            
            supporting_content = ""
            if req_id in requirement_matches:
                supporting_content = "Supporting document content:\n"
                for match in requirement_matches[req_id][:5]:
                    supporting_content += f"- \"{match.get('context', '')}\"\n"
            
            is_ambiguous = req.get('clarity') != 'clear'
            ambiguity_category = "unknown"
            if is_ambiguous:
                for category, reqs in categorized_reqs["ambiguous"].items():
                    if req in reqs:
                        ambiguity_category = category
                        break
            
            ambiguity_focus = self._get_ambiguity_focus(ambiguity_category) if is_ambiguous else """
            Focus on questions that help:
            1. Validate understanding of the clear requirement
            2. Gather additional technical details needed for implementation
            3. Identify dependencies with other requirements
            4. Uncover hidden assumptions
            5. Establish success criteria and acceptance tests
            """
            
            prompt = f"""
            Act as a Technical Analyst with business context. Generate specific, technically focused questions to clarify the following requirement.
            
            Project Overview:
            {sow_context.get('overview', 'Not available')}
            
            Project Type: {sow_context.get('project_type', 'General')}
            
            Key Stakeholders:
            {sow_context.get('stakeholders', 'Not specified')}
            
            Key Deliverables:
            {sow_context.get('deliverables', 'Not specified')}
            
            Requirement:
            Text: {req.get('text', 'Not provided')}
            Section: {req.get('section', 'Not provided')}
            Clarity: {req.get('clarity', 'Unknown')}
            {f"Reason for ambiguity: {req.get('reason', 'Not specified')}" if is_ambiguous else ""}
            
            {related_reqs_text}
            
            {supporting_content}
            
            {ambiguity_focus}
            
            Generate specific, technically focused questions that would help clarify this requirement.
            Target questions at the right stakeholders and consider:
            1. Deliverable expectations
            2. Acceptance criteria
            3. Scope boundaries
            4. Technical constraints
            5. Timeline and priority
            6. Business process
            7. Technical process
            8. Potential risks
            
            Note:
            - Be specific and actionable
            - Avoid generic or vague questions
            - DO NOT INCLUDE REQUIREMENT ID IN QUESTION TEXT
            - Limit to 5 questions per requirement
            
            Format as a JSON array of objects with:
            - question: The specific question text
            - context: Why this question needs to be asked
            - priority: 1-3 (1 being highest)
            - target_stakeholder: Who should answer
            """
            
            try:
                logger.info(f"Sending prompt to Gemini API for requirement {req_id}")
                response = self.model.generate_content(prompt)
                logger.info(f"Received response from Gemini API")
                req_questions = self._parse_questions_from_response(response.text)
                logger.info(f"Extracted {len(req_questions)} questions")
                
                for q in req_questions:
                    q['source'] = f"Requirement: {req.get('id', 'Unknown')}"
                    q['source_text'] = req.get('text', '')
                    q['status'] = "unanswered"
                    q['requirement_id'] = req.get('id', 'Unknown')
                    q['section'] = req.get('section', '')
                    if is_ambiguous:
                        q['ambiguity_reason'] = req.get('reason', '')
                        q['priority'] = max(1, int(q.get('priority', 3)) - 1)
                    else:
                        q['priority'] = min(3, max(1, int(q.get('priority', 2))))
                    questions.append(q)
            except Exception as e:
                logger.error(f"Failed to generate questions for requirement {req_id}: {str(e)}", exc_info=True)
                continue
        
        if sow_data.get('boundaries', {}).get('unclear'):
            logger.info("Processing unclear boundaries")
            unclear_items = sow_data.get('boundaries', {}).get('unclear', [])
            
            prompt = f"""
            You are an expert discovery analyst. Generate questions to clarify unclear project boundaries.
            
            Project Overview:
            {sow_context.get('overview', 'Not available')}
            
            Project Type: {sow_context.get('project_type', 'General')}
            
            Unclear Items:
            {json.dumps(unclear_items, indent=2)}
            
            Known In-Scope Items:
            {json.dumps(sow_data.get('boundaries', {}).get('in_scope', []), indent=2)}
            
            Known Out-of-Scope Items:
            {json.dumps(sow_data.get('boundaries', {}).get('out_of_scope', []), indent=2)}
            
            For each unclear item, generate 5 IT and technology-specific questions to clarify scope.
            Questions should:
            1. Be specific and actionable
            2. Establish clear boundaries
            3. Uncover hidden assumptions
            4. Identify scope creep risks
            
            Format as a JSON array of objects with:
            - item: The unclear item
            - question: The specific question text
            - context: Why this question is needed
            - priority: 1-3 (1 being highest)
            - risk_level: high, medium, low
            - target_stakeholder: Who should answer
            """
            
            try:
                logger.info("Sending boundary question prompt to Gemini API")
                response = self.model.generate_content(prompt)
                boundary_questions = self._parse_questions_from_response(response.text)
                logger.info(f"Extracted {len(boundary_questions)} boundary questions")
                
                for q in boundary_questions:
                    q['source'] = "Unclear Boundary"
                    q['source_text'] = q.get('item', 'Boundary item')
                    q['status'] = "unanswered"
                    q['boundary_item'] = q.get('item', '')
                    questions.append(q)
            except Exception as e:
                logger.error(f"Failed to generate boundary questions: {str(e)}", exc_info=True)
        
        industry_questions = self._generate_industry_specific_questions(sow_context.get('project_type', 'General'), sow_data)
        if industry_questions:
            logger.info(f"Added {len(industry_questions)} industry-specific questions")
            questions.extend(industry_questions)
        
        logger.info("Starting question refinement")
        questions = self.refine_questions(questions)
        logger.info(f"Refinement completed: {len(questions)} questions")
        
        result["questions"] = questions
        result["summary"]["questions_count"] = len(questions)
        
        if questions and hasattr(self.db, 'store_questions'):
            logger.info(f"Storing {len(questions)} questions in database")
            try:
                self.db.store_questions(questions)
                logger.info("Questions stored in database")
            except Exception as e:
                logger.error(f"Failed to store questions in database: {str(e)}", exc_info=True)
        
        logger.info("Completed question generation")
        return result

    def _get_ambiguity_focus(self, ambiguity_category: str) -> str:
        """Get focus questions based on ambiguity category."""
        focus_by_category = {
            "vague_language": """
            Focus on questions that help:
            1. Define specific, measurable criteria for vague terms
            2. Establish clear boundaries and scope
            3. Clarify expected outcomes and deliverables
            """,
            "missing_criteria": """
            Focus on questions that help:
            1. Establish measurable acceptance criteria
            2. Define what "good" looks like
            3. Identify evaluation methods and metrics
            4. Clarify who approves and using what criteria
            """,
            "undefined_terms": """
            Focus on questions that help:
            1. Get precise definitions for domain-specific terminology
            2. Identify specific items (documents, stakeholders, systems)
            3. Establish common understanding of key terms
            """,
            "scope_issues": """
            Focus on questions that help:
            1. Define clear boundaries of what's in vs. out of scope
            2. Establish the required level of detail or depth
            3. Determine specific deliverables and their format
            4. Identify specific inclusions and exclusions
            """,
            "format_missing": """
            Focus on questions that help:
            1. Establish required format and documentation standards
            2. Determine level of detail required in deliverables
            3. Clarify expectations for diagrams, models, or other artifacts
            4. Identify specific audiences and their needs
            """
        }
        return focus_by_category.get(ambiguity_category, """
        Focus on questions that help:
        1. Clarify specific expectations and requirements
        2. Establish measurable criteria for success
        3. Define boundaries and scope
        4. Identify key stakeholders and their needs
        """)
    
    def _generate_industry_specific_questions(self, project_type: str, sow_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate industry-specific questions based on project type."""
        try:
            industry_prompts = {
                'software_development': """
                Generate 3-5 critical questions for software modernization projects, focusing on:
                - Technical Architecture: Are architecture requirements and constraints defined?
                - System Integration: How will the system integrate with existing infrastructure?
                - Data Migration Strategy: What are the risks and methodologies for data migration?
                - Performance and SLAs: Are performance benchmarks and SLAs defined?
                - Security and Compliance: Are security protocols and compliance standards addressed?
                - Testing and Quality Assurance: Is there a robust testing plan?
                - Post-deployment Support: What is the support and maintenance plan?
                """,
                'consulting': """
                Generate 3-5 critical questions for consulting engagements, focusing on:
                - Decision-Making Authority: Are decision-making processes defined?
                - Success Definition: Are success criteria and KPIs established?
                - Deliverable Format: Are expectations for deliverables clear?
                - Client Resource Availability: Is client resource commitment defined?
                - Knowledge Transfer: Are plans for knowledge transfer clear?
                - Follow-on Work: Are expectations for future phases defined?
                """,
                'training': """
                Generate 3-5 critical questions for training projects, focusing on:
                - Skill Assessment: Has a skills assessment been conducted?
                - Learning Outcomes: Are measurable learning outcomes defined?
                - Material Ownership: Who owns and maintains training materials?
                - Train-the-Trainer: Are there plans for internal trainer upskilling?
                - Ongoing Support: What support is available post-training?
                """,
                'infrastructure': """
                Generate 3-5 critical questions for infrastructure projects, focusing on:
                - Dependencies: Are all system dependencies identified?
                - Performance Requirements: Are performance and capacity needs defined?
                - Maintenance: What are the maintenance and support expectations?
                - Disaster Recovery: Are DR requirements and testing plans clear?
                - Security and Compliance: Are security and compliance standards addressed?
                - Integration: How will new infrastructure integrate with existing systems?
                """
            }
            
            prompt_template = industry_prompts.get(project_type, """
            Generate 3-5 critical questions for general projects, focusing on:
            - Scope Boundaries: Are scope boundaries clear to prevent creep?
            - Governance: Are roles and decision-making processes defined?
            - Communication: Are communication protocols established?
            - Approval Processes: Are approval criteria and authority clear?
            - Success Criteria: Are success criteria measurable and aligned?
            - Risk Management: Are risks identified and mitigated?
            """)
            
            sections_summary = []
            key_sections = ['Scope', 'Deliverable', 'Timeline', 'Assumption']
            for section_name, section_content in sow_data.get('sections', {}).items():
                if any(keyword.lower() in section_name.lower() for keyword in key_sections):
                    sections_summary.append(f"## {section_name}\n{section_content}")
            combined_summary = "\n\n".join(sections_summary)
            
            prompt = f"""
            {prompt_template}
            
            SOW Summary:
            {combined_summary[:5000]}
            
            Format as a JSON array of objects with:
            - question: The specific question text
            - context: Why this question is important
            - priority: 1-3 (1 being highest)
            - category: The project aspect (e.g., "Technical", "Process")
            - target_stakeholder: Who should answer
            """
            
            response = self.model.generate_content(prompt)
            return self._parse_questions_from_response(response.text)
        except Exception as e:
            logger.error(f"Error generating industry-specific questions: {str(e)}")
            return []
    
    def _parse_questions_from_response(self, response_text: str) -> List[Dict[str, Any]]:
        """Parse questions from Gemini response text."""
        logger.info("Parsing response from Gemini")
        if not response_text:
            logger.warning("Empty response received from Gemini")
            return []
        
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
                    lines = response_text.split('\n')
                    current_question = {}
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith('Question:') or line.startswith('- Question:'):
                            if current_question and 'question' in current_question:
                                questions.append(current_question)
                            current_question = {'question': line.split(':', 1)[1].strip()}
                        elif ':' in line and current_question:
                            key, value = line.split(':', 1)
                            key = key.strip().lower()
                            if key in ['context', 'priority', 'item', 'category', 'target_stakeholder', 'risk_level']:
                                current_question[key] = value.strip()
                    if current_question and 'question' in current_question:
                        questions.append(current_question)
        except Exception as e:
            logger.error(f"Error parsing questions from response: {str(e)}")
        return questions
    
    def generate_followup_questions(self, question_id: int, answer: str) -> List[Dict[str, Any]]:
        """
        Generate follow-up questions based on an answer.
        
        Args:
            question_id: ID of the answered question
            answer: The answer provided
            
        Returns:
            List of follow-up question dictionaries
        """
        original_question = self.db.get_question(question_id)
        if not original_question:
            logger.warning(f"No question found for ID {question_id}")
            return []
        
        prompt = f"""
        Original Question: {original_question.get('question', '')}
        Context: {original_question.get('context', '')}
        Source: {original_question.get('source', '')}
        Source Text: {original_question.get('source_text', '')}
        
        Answer Received: {answer}
        
        Analyze if this answer fully addresses the original question. If not, or if it raises new questions:
        1. Identify unclear aspects
        2. Highlight new information needing clarification
        3. Check for inconsistencies
        4. Identify new risks or issues
        5. Generate follow-up questions
        
        Format as a JSON object with:
        - fully_answered: Boolean
        - reason: Explanation
        - followup_questions: Array of questions with:
          * question: The question text
          * context: Why this follow-up is needed
          * priority: 1-3 (1 being highest)
          * category: Aspect addressed (e.g., "Clarification", "Risk")
          * target_stakeholder: Who should answer
        """
        
        try:
            response = self.model.generate_content(prompt)
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, response.text)
            
            if json_match:
                result = json.loads(json_match.group(1))
            else:
                try:
                    result = json.loads(response.text)
                except:
                    result = {
                        'fully_answered': 'fully answered' in response.text.lower(),
                        'reason': 'Could not parse structured response',
                        'followup_questions': []
                    }
            
            if result.get('fully_answered', False):
                self.db.update_question_status(question_id, "answered")
            else:
                self.db.update_question_status(question_id, "partially_answered")
            
            followup_questions = result.get('followup_questions', [])
            followup_questions = self.refine_questions(followup_questions)
            for q in followup_questions:
                q['parent_question_id'] = question_id
                q['source'] = original_question.get('source', '')
                q['source_text'] = original_question.get('source_text', '')
                q['status'] = "unanswered"
            
            if followup_questions and hasattr(self.db, 'store_questions'):
                self.db.store_questions(followup_questions)
            
            return followup_questions
        except Exception as e:
            logger.error(f"Error generating follow-up questions: {str(e)}")
            return []
    
    def generate_requirements_summary(self, sow_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a summary of requirements by clarity status.
        
        Args:
            sow_data: Dictionary containing SOW sections, requirements, and boundaries
            
        Returns:
            Dictionary with summary information
        """
        requirements = sow_data.get('requirements', [])
        categorized_reqs = self._categorize_requirements(requirements)
        
        summary = {
            "total_requirements": len(requirements),
            "clear_requirements": {
                "count": len(categorized_reqs["clear"]),
                "percentage": round(len(categorized_reqs["clear"]) / len(requirements) * 100, 1) if requirements else 0,
                "examples": [req.get("id") for req in categorized_reqs["clear"][:5]]
            },
            "ambiguous_requirements": {
                "count": sum(len(reqs) for reqs in categorized_reqs["ambiguous"].values()),
                "percentage": round(sum(len(reqs) for reqs in categorized_reqs["ambiguous"].values()) / len(requirements) * 100, 1) if requirements else 0,
                "by_category": {}
            },
            "boundaries": {
                "in_scope": len(sow_data.get('boundaries', {}).get('in_scope', [])),
                "out_of_scope": len(sow_data.get('boundaries', {}).get('out_of_scope', [])),
                "unclear": len(sow_data.get('boundaries', {}).get('unclear', []))
            }
        }
        
        for category, reqs in categorized_reqs["ambiguous"].items():
            summary["ambiguous_requirements"]["by_category"][category] = {
                "count": len(reqs),
                "percentage": round(len(reqs) / len(requirements) * 100, 1) if requirements else 0,
                "examples": [req.get("id") for req in reqs[:3]]
            }
        
        section_analysis = defaultdict(lambda: {"total": 0, "clear": 0, "ambiguous": 0})
        for req in requirements:
            section = req.get("section", "Unknown")
            section_analysis[section]["total"] += 1
            if req.get("clarity") == "clear":
                section_analysis[section]["clear"] += 1
            else:
                section_analysis[section]["ambiguous"] += 1
        
        summary["by_section"] = {}
        for section, counts in section_analysis.items():
            summary["by_section"][section] = {
                "total": counts["total"],
                "clear": counts["clear"],
                "ambiguous": counts["ambiguous"],
                "clear_percentage": round(counts["clear"] / counts["total"] * 100, 1) if counts["total"] > 0 else 0,
                "ambiguous_percentage": round(counts["ambiguous"] / counts["total"] * 100, 1) if counts["total"] > 0 else 0
            }
        
        return summary