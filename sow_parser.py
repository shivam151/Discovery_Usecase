import re
import logging
import datetime
from typing import Dict, List, Any, Union
from io import BytesIO
import google.generativeai as genai
from docx import Document
from pptx import Presentation
import pymupdf  # PyMuPDF for PDF processing
from PIL import Image
import time
import traceback
from dotenv import dotenv_values
import os
try:
    import magic  # python-magic for file type detection
except ImportError:
    magic = None
    logging.getLogger(__name__).warning("python-magic not available, falling back to extension-based file type detection")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class SOWParser:
    def __init__(self, gemini_api_key: str = None):
        """Initialize the SOWParser"""
        resolved_gemini_api_key = None

        # Priority 1: Use the API key passed directly to the constructor
        if gemini_api_key:
            resolved_gemini_api_key = gemini_api_key
        else:
            # Priority 2: Read from .env file
            env_vars_from_file = dotenv_values()
            if "GOOGLE_API_KEY" in env_vars_from_file:
                resolved_gemini_api_key = env_vars_from_file["GOOGLE_API_KEY"]

        # Ensure an API key was found
        if not resolved_gemini_api_key:
            logger.error("No Gemini API key provided")
            raise ValueError(
                "No Gemini API key provided. "
                "Please set GOOGLE_API_KEY in your .env file "
                "or pass it directly to the SOWParser constructor."
            )

        genai.configure(api_key=resolved_gemini_api_key)
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.gemini_api_key = resolved_gemini_api_key
        logger.info("SOWParser initialized with Gemini API")

    def parse_sow(self, file_input: Union[str, BytesIO], filename: str = None) -> Dict[str, Any]:
        """Parse SOW document and extract structured data"""
        logger.info(f"=== STARTING SOW PARSING at {datetime.datetime.now()} ===")
        
        try:
            # Determine input type
            if isinstance(file_input, str):
                logger.info(f"Parsing input type: file path ({file_input})")
                if not os.path.exists(file_input):
                    raise FileNotFoundError(f"File not found: {file_input}")
                file_extension = os.path.splitext(file_input)[1].lower()
                filename = os.path.basename(file_input)
                with open(file_input, 'rb') as f:
                    file_content = BytesIO(f.read())
            elif isinstance(file_input, BytesIO):
                logger.info("Parsing input type: BytesIO")
                if not filename:
                    raise ValueError("Filename must be provided for BytesIO input")
                file_extension = os.path.splitext(filename)[1].lower()
                logger.info(f"Detected file extension: {file_extension} for file: {filename}")
                file_content = file_input
            else:
                raise ValueError(f"Unsupported input type: {type(file_input)}")

            # Detect MIME type
            mime_type = None
            if magic:
                try:
                    mime_detector = magic.Magic(mime=True)
                    file_content.seek(0)
                    mime_type = mime_detector.from_buffer(file_content.read(1024))
                    file_content.seek(0)
                    logger.info(f"Detected MIME type: {mime_type}")
                except Exception as e:
                    logger.warning(f"Failed to detect MIME type with python-magic: {str(e)}")
                    mime_type = None

            # Fallback to extension-based detection if MIME type detection fails
            if not mime_type:
                logger.info("Using file extension for type detection")
                if file_extension == '.pdf':
                    mime_type = 'application/pdf'
                elif file_extension == '.docx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif file_extension == '.pptx':
                    mime_type = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                else:
                    raise ValueError(f"Unsupported file extension: {file_extension} for file: {filename}")

            # Fallback to MIME type if extension is empty or invalid
            if not file_extension or file_extension not in ['.pdf', '.docx', '.pptx']:
                logger.info(f"Empty or invalid file extension: {file_extension}, using MIME type fallback")
                if mime_type == 'application/pdf':
                    file_extension = '.pdf'
                    if not filename.lower().endswith('.pdf'):
                        filename = filename + '.pdf'
                        logger.info(f"Appended .pdf to filename: {filename}")
                elif mime_type in ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip']:
                    file_extension = '.docx'
                    if not filename.lower().endswith('.docx'):
                        filename = filename + '.docx'
                        logger.info(f"Appended .docx to filename: {filename}")
                elif mime_type == 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
                    file_extension = '.pptx'
                    if not filename.lower().endswith('.pptx'):
                        filename = filename + '.pptx'
                        logger.info(f"Appended .pptx to filename: {filename}")
                else:
                    raise ValueError(f"Unsupported MIME type: {mime_type} for file: {filename}")

            # Validate file format
            if file_extension not in ['.pdf', '.docx', '.pptx']:
                raise ValueError(f"Unsupported file format: {file_extension} for file: {filename}")

            # Validate file content for DOCX if MIME type is application/zip
            if file_extension == '.docx' and mime_type == 'application/zip':
                logger.info(f"Detected application/zip for .docx file, validating DOCX structure")
                try:
                    file_content.seek(0)
                    Document(file_content)
                    file_content.seek(0)
                    logger.info("File confirmed as valid DOCX despite application/zip MIME type")
                except Exception as e:
                    logger.error(f"File is not a valid DOCX: {str(e)}")
                    raise ValueError(f"File is not a valid DOCX (MIME type: {mime_type}, file: {filename}): {str(e)}")

            # Validate MIME type matches extension
            mime_map = {
                '.pdf': ['application/pdf'],
                '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/zip'],
                '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation']
            }
            if mime_type not in mime_map.get(file_extension, []):
                raise ValueError(f"File is not a valid {file_extension.upper()[1:]} (MIME type: {mime_type}, file: {filename})")

            # Parse file based on format
            logger.info(f"Extracting text from {file_extension} file: {filename}")
            try:
                if file_extension == '.pdf':
                    text = self._extract_text_from_pdf(file_content)
                elif file_extension == '.docx':
                    text = self._extract_text_from_docx(file_content)
                elif file_extension == '.pptx':
                    text = self._extract_text_from_pptx(file_content)
                else:
                    raise ValueError(f"Unexpected file format: {file_extension} for file: {filename}")
                
                # Process text with Gemini API
                logger.info(f"Processing text with Gemini API for file: {filename}")
                sow_data = self._process_with_gemini(text)
                logger.info(f"SOW parsing completed successfully for file: {filename}")
                return sow_data

            except Exception as e:
                logger.error(f"Error parsing SOW file {filename}: {str(e)}", exc_info=True)
                raise

        except Exception as e:
            logger.error(f"Error in parse_sow for file {filename}: {str(e)}", exc_info=True)
            raise

    def _extract_text_from_pdf(self, file_content: BytesIO) -> str:
        """Extract text from PDF file"""
        try:
            doc = pymupdf.open(stream=file_content, filetype='pdf')
            text = ""
            for page in doc:
                text += page.get_text()
            doc.close()
            logger.info("Successfully extracted text from PDF")
            return text
        except Exception as e:
            logger.error(f"Failed to extract text from PDF: {str(e)}", exc_info=True)
            raise

    def _extract_text_from_docx(self, file_content: BytesIO) -> str:
        """Extract text from DOCX file"""
        try:
            doc = Document(file_content)
            text = "\n".join([para.text for para in doc.paragraphs if para.text])
            logger.info("Successfully extracted text from DOCX")
            return text
        except Exception as e:
            logger.error(f"Failed to extract text from DOCX: {str(e)}", exc_info=True)
            raise

    def _extract_text_from_pptx(self, file_content: BytesIO) -> str:
        """Extract text from PPTX file"""
        try:
            prs = Presentation(file_content)
            text = "\n".join([shape.text for slide in prs.slides for shape in slide.shapes if hasattr(shape, 'text') and shape.text])
            logger.info("Successfully extracted text from PPTX")
            return text
        except Exception as e:
            logger.error(f"Failed to extract text from PPTX: {str(e)}", exc_info=True)
            raise

    def _extract_sections(self, document_text: str) -> Dict[str, str]:
        """Extract sections from SOW document using Gemini"""
        logger.info(f"Extracting sections from document at {time.strftime('%Y-%m-%d %H:%M:%S')}")
        prompt = f"""
        You are analyzing a Statement of Work (SOW) document. 
        Identify and extract the key sections from this document.
        Common sections in SOW documents include:
        - Introduction/Background
        - Scope of Work
        - Deliverables
        - Timeline/Schedule
        - Acceptance Criteria
        - Pricing/Payment Terms
        - Assumptions/Constraints
        - Change Management Process
        
        Extract each section with its heading and content.
        Format your response as a JSON object with section names as keys and their content as values.
        
        Document text:
        {document_text}
        """
        
        logger.info("Sending section extraction prompt to Gemini API")
        try:
            response = self.model.generate_content(prompt)
            logger.info(f"Received response from Gemini API at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            logger.info(f"Response length: {len(response.text)} characters")
        except Exception as api_error:
            logger.error(f"Gemini API call failed during section extraction: {str(api_error)}", exc_info=True)
            raise
        
        try:
            # Try to parse sections from the response
            sections_text = response.text
            # Extract JSON part from the response
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, sections_text)
            if json_match:
                import json
                sections = json.loads(json_match.group(1))
                logger.info(f"Successfully parsed {len(sections)} sections from JSON response")
            else:
                logger.info("JSON pattern not found in response, trying direct JSON parsing")
                # Try direct JSON parsing
                try:
                    import json
                    sections = json.loads(sections_text)
                    logger.info(f"Successfully parsed {len(sections)} sections with direct JSON parsing")
                except:
                    logger.info("Failed to parse JSON directly, falling back to heuristic approach")
                    # If no JSON found, use a simple heuristic approach
                    sections = {}
                    current_section = "Introduction"
                    lines = sections_text.split('\n')
                    section_content = []
                    
                    for line in lines:
                        if line.strip() and line[0] == '#' and len(line.strip()) < 50:
                            # If we find a new section heading, save the previous section
                            if section_content:
                                sections[current_section] = '\n'.join(section_content)
                            
                            # Start a new section
                            current_section = line.strip('#').strip()
                            section_content = []
                        else:
                            section_content.append(line)
                    
                    # Add the last section
                    if section_content:
                        sections[current_section] = '\n'.join(section_content)
            
            logger.info(f"Sections extracted: {', '.join(list(sections.keys())[:5])}{'...' if len(sections) > 5 else ''}")
            return sections
                
        except Exception as e:
            logger.error(f"Error extracting sections: {str(e)}", exc_info=True)
            # Fall back to a simple approach
            logger.info("Falling back to using full document as a single section")
            return {"Full Document": document_text}
    
    def _extract_requirements(self, sections: Dict[str, str]) -> List[Dict[str, str]]:
        """Extract requirements from SOW sections using Gemini"""
        requirements = []
        
        # Combine relevant sections for requirement extraction
        relevant_sections = []
        for section_name, section_content in sections.items():
            if any(keyword in section_name.lower() for keyword in 
                  ['scope', 'deliverable', 'requirement', 'objective', 'service', 
                   'feature', 'function', 'specification', 'work', 'task', 'activity',
                   'responsibility', 'obligation', 'timeline', 'acceptance']):
                relevant_sections.append(f"## {section_name}\n{section_content}")
        
        if not relevant_sections:
            # If no specifically relevant sections, use all sections
            for section_name, section_content in sections.items():
                relevant_sections.append(f"## {section_name}\n{section_content}")
        
        combined_text = "\n\n".join(relevant_sections)
        
        prompt = f"""
        Extract all specific requirements from these SOW sections.
        Be comprehensive - consider only IT technical requirements and any statement that implies or connect to that to be done as a requirement.
        You have to think like a Solution Architect in IT industry where your job is related to the technology and actual implementation related when reading the SOW
        
        For each requirement:
        1. Provide a short ID (e.g., REQ-01)
        2. Extract the exact requirement text
        3. Identify which section it comes from
        4. Determine if it's clearly defined or ambiguous
        
        A requirement should be considered ambiguous if it:
        - Contains vague or subjective terms (e.g., "appropriate", "reasonable", "sufficient")
        - Lacks measurable criteria or specific details
        - Uses unclear terminology or jargon without definition
        - Has multiple possible interpretations
        - Doesn't specify who is responsible for the work
        - Contains conditional statements without clear triggers
        - Uses words like "may", "might", "could", "should" instead of "will", "shall", "must"
        - Doesn't have clear acceptance criteria
        - Lacks timeline or deadline information
        
        Format your response as a JSON array of objects with keys:
        - id: A unique identifier for the requirement
        - text: The exact requirement text
        - section: The section it comes from
        - clarity: Either "clear" or "ambiguous"
        - reason: Brief explanation if marked as ambiguous
        
        SOW Sections:
        {combined_text}
        """
        
        logger.info("Sending requirement extraction prompt to Gemini API")
        response = self.model.generate_content(prompt)
        logger.info(f"Requirement extraction response length: {len(response.text)}")
        
        try:
            # Try to parse requirements from the response
            requirements_text = response.text
            # Extract JSON part from the response
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, requirements_text)
            if json_match:
                import json
                requirements = json.loads(json_match.group(1))
                logger.info(f"Successfully parsed {len(requirements)} requirements from JSON response")
            else:
                # Try direct JSON parsing
                try:
                    import json
                    requirements = json.loads(requirements_text)
                    logger.info(f"Successfully parsed {len(requirements)} requirements with direct JSON parsing")
                except:
                    logger.info("Failed to parse JSON directly, attempting manual parsing")
                    # Simple parsing for requirements if no JSON found
                    lines = requirements_text.split('\n')
                    current_req = {}
                    
                    for line in lines:
                        if line.startswith('REQ-') or line.startswith('- ID: REQ-'):
                            # Save previous requirement if it exists
                            if current_req and 'id' in current_req:
                                requirements.append(current_req)
                            
                            # Start a new requirement
                            current_req = {'id': line.strip()}
                        elif ':' in line and current_req:
                            key, value = line.split(':', 1)
                            key = key.strip().lower()
                            if key in ['text', 'section', 'clarity', 'reason']:
                                current_req[key] = value.strip()
                    
                    # Add the last requirement
                    if current_req and 'id' in current_req:
                        requirements.append(current_req)
                    
                    logger.info(f"Parsed {len(requirements)} requirements with manual parsing")
        
        except Exception as e:
            logger.error(f"Error extracting requirements: {str(e)}", exc_info=True)
        
        # Make sure all requirements have the required fields
        for req in requirements:
            for field in ['id', 'text', 'section', 'clarity']:
                if field not in req:
                    req[field] = f"Unknown {field}"
            if req['clarity'] == 'ambiguous' and 'reason' not in req:
                req['reason'] = "No reason provided"
                
        logger.info(f"Returning {len(requirements)} requirements")
        logger.info(f"Ambiguous requirements: {sum(1 for req in requirements if req.get('clarity') == 'ambiguous')}")
        
        return requirements
    
    def _identify_boundaries(self, sections: Dict[str, str]) -> Dict[str, Any]:
        """Identify boundaries (in-scope vs out-of-scope) from SOW sections"""
        boundaries = {
            'in_scope': [],
            'out_of_scope': [],
            'unclear': []
        }
        
        # Combine relevant sections for boundary extraction
        scope_sections = []
        for section_name, section_content in sections.items():
            if any(keyword in section_name.lower() for keyword in 
                  ['scope', 'assumption', 'exclusion', 'limitation', 'constraint',
                   'boundary', 'deliverable', 'not included', 'included', 'exclude',
                   'include', 'work', 'service', 'responsibility']):
                scope_sections.append(f"## {section_name}\n{section_content}")
        
        if not scope_sections:
            # If no specifically relevant sections, use all sections
            for section_name, section_content in sections.items():
                scope_sections.append(f"## {section_name}\n{section_content}")
        
        combined_text = "\n\n".join(scope_sections)
        
        prompt = f"""
        Analyze these SOW sections and clearly identify what is in-scope, out-of-scope, and areas that are unclear or not explicitly defined.
        
        For the unclear/ambiguous areas, be liberal in your interpretation - any aspect that lacks specific detail or could be interpreted in multiple ways should be considered unclear.
        Consider the following as potentially unclear:
        - Services or deliverables without detailed specifications
        - Responsibilities without clear assignment
        - Processes without defined steps
        - Quality expectations without measurable criteria
        - Requirements with subjective terms (like "appropriate" or "reasonable")
        - Timeline dependencies without specific milestones
        - Resource requirements without quantification
        
        Format your response as a JSON object with three arrays:
        1. "in_scope": List of items explicitly included in the scope
        2. "out_of_scope": List of items explicitly excluded from the scope
        3. "unclear": List of important items that should be clarified (with brief explanation of why)
        
        SOW Sections:
        {combined_text}
        """
        
        logger.info("Sending boundary identification prompt to Gemini API")
        response = self.model.generate_content(prompt)
        logger.info(f"Boundary identification response length: {len(response.text)}")
        
        try:
            # Try to parse boundaries from the response
            boundaries_text = response.text
            # Extract JSON part from the response
            json_pattern = r'```json\n([\s\S]*?)\n```'
            json_match = re.search(json_pattern, boundaries_text)
            if json_match:
                import json
                boundaries = json.loads(json_match.group(1))
                logger.info(f"Successfully parsed boundaries from JSON response")
            else:
                # Try direct JSON parsing
                try:
                    import json
                    boundaries = json.loads(boundaries_text)
                    logger.info(f"Successfully parsed boundaries with direct JSON parsing")
                except:
                    logger.info("Failed to parse JSON directly, attempting manual parsing")
                    # Simple parsing if no JSON found
                    in_scope_section = False
                    out_scope_section = False
                    unclear_section = False
                    
                    lines = boundaries_text.split('\n')
                    
                    for line in lines:
                        line = line.strip()
                        if not line:
                            continue
                        
                        if "in-scope" in line.lower() or "in scope" in line.lower():
                            in_scope_section = True
                            out_scope_section = False
                            unclear_section = False
                            continue
                        elif "out-of-scope" in line.lower() or "out of scope" in line.lower():
                            in_scope_section = False
                            out_scope_section = True
                            unclear_section = False
                            continue
                        elif "unclear" in line.lower() or "ambiguous" in line.lower():
                            in_scope_section = False
                            out_scope_section = False
                            unclear_section = True
                            continue
                        
                        if line.startswith('-') or line.startswith('*'):
                            item = line[1:].strip()
                            if in_scope_section:
                                boundaries['in_scope'].append(item)
                            elif out_scope_section:
                                boundaries['out_of_scope'].append(item)
                            elif unclear_section:
                                boundaries['unclear'].append(item)
                    
                    logger.info(f"Parsed boundaries with manual parsing")
        
        except Exception as e:
            logger.error(f"Error identifying boundaries: {str(e)}", exc_info=True)
        
        # Make sure all key arrays exist
        for key in ['in_scope', 'out_of_scope', 'unclear']:
            if key not in boundaries:
                boundaries[key] = []
                
        logger.info(f"Boundaries identified: {len(boundaries.get('in_scope', []))} in-scope, "
                    f"{len(boundaries.get('out_of_scope', []))} out-of-scope, "
                    f"{len(boundaries.get('unclear', []))} unclear")
        
        return boundaries

    def _process_with_gemini(self, text: str) -> Dict[str, Any]:
        """Process extracted text with Gemini API to extract structured data"""
        try:
            # Truncate text to avoid Gemini API limits
            max_text_length = 10000
            if len(text) > max_text_length:
                logger.warning(f"Text length ({len(text)}) exceeds Gemini API limit, truncating to {max_text_length} characters")
                text = text[:max_text_length]

            # Extract sections
            sections = self._extract_sections(text)
            # Extract requirements
            requirements = self._extract_requirements(sections)
            # Identify boundaries
            boundaries = self._identify_boundaries(sections)

            return {
                "sections": sections,
                "requirements": requirements,
                "boundaries": boundaries
            }
        except Exception as e:
            logger.error(f"Failed to process with Gemini API: {str(e)}", exc_info=True)
            raise