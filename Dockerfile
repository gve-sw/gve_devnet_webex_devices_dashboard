FROM python:3.12-slim

WORKDIR /app
COPY ./requirements.txt /app
RUN pip install -r requirements.txt
COPY ./flask_app .

# Run the initial DB Script to build sqlite.db, then run main app
CMD ["sh", "-c", "python ./db.py && python ./app.py"]
