FROM public.ecr.aws/lambda/python:3.14

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestration/ ./
COPY common/ ./common/

CMD ["app.lambda_handler"]
