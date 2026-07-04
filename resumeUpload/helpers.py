import re
def verify_correct_email_format(email: str) -> bool:
    '''
    Verify if the provided email is in a correct format.
    '''
    email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(email_regex, email) is not None

class ResumeParsingException(Exception):
    def __init__(self,status_code: int,message: str):
        super().__init__(status_code,message)
        self.message = message
        self.status_code = status_code
    