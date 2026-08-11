from pypdf import PdfReader
from ResumeDataParser import ResumeDataParser
from common.models import ResumeData
import io

class ResumeService:
    def get_text_from_pdf(self,file_bytes: bytes) -> str:
        file_stream = io.BytesIO(file_bytes)
        file_text = ""
        reader = PdfReader(file_stream)
        for page in reader.pages:
            file_text += page.extract_text()
        return file_text
    def parse_resume(self,parser: ResumeDataParser,file_text: str) -> ResumeData:
        parsed_resume = parser.parse(file_text)
        return parsed_resume
    