FROM public.ecr.aws/lambda/python:3.14

RUN dnf install -y pango cairo gdk-pixbuf2 libffi dejavu-sans-fonts && dnf clean all

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY generateResumeWorker/ ./
COPY common/ ./common/

CMD ["main.handler"]
