from agents.base import BaseAgent
from prompts.architecture_generation import ARCH_GEN_PROMPT


@tool
def query_database(query):
    """A function that can query a database for similarity of project requirements."""


def generate_prompt(files):
    data = []
    for file in files:
        file_name, file_ext = os.path.splitext(file)
        # content based or pass files directly can be chosen
        '''content =""
        with open(file, "r") as f:
            content = f.read()
        '''
        file_name = file_name.split("/")[-1]
        data.append(file_name)
    return "\n".join(data)        

class architectureAgent(BaseAgent):
    
    def run(self, input_data):
        # file path on the basis of project will be orcestrators responsibility
        self.prompt_template = self.prompt_template.format(files=generate_prompt(input_data))
        final_prompt = [self.prompt_template]
        for file in input_data:
            try:
                '''uploaded_file = genai.upload_file(path=file)'''
                final_prompt.append(file) #replace with uploaded file
            except Exception as e:
                self.error = "file upload error"
                return -1
        #response = model.generate_content(final_prompt)
        #return response.content
        return final_prompt   